"""Advisory context stored in the existing private SQLite evidence ledger."""
from datetime import datetime, timezone
import json
from uuid import uuid4
from functools import wraps

from .advisory import (validate_snapshot, relevant_rules, boss_manifest, check_plan,
                       state_digest, campfire_comparison)


def atomic(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._transaction():
            return method(self, *args, **kwargs)
    return wrapped


class AdvisoryMemory:
    def _advisory_revision(self, db, run_id, combat_id):
        # No frame is inferred from game input. A ledger revision detects known
        # changes; the caller must still observe after any unlogged input.
        return {
            'inventory': db.execute('SELECT COALESCE(MAX(rowid),0) FROM inventory_events WHERE run_id=?', (run_id,)).fetchone()[0],
            'baseline': db.execute('SELECT COALESCE(MAX(rowid),0) FROM inventory_baselines WHERE run_id=?', (run_id,)).fetchone()[0],
            'zones': db.execute('SELECT COALESCE(MAX(rowid),0) FROM combat_zone_events WHERE combat_id=?', (combat_id,)).fetchone()[0],
            'evidence': db.execute("SELECT COALESCE(MAX(rowid),0) FROM evidence_events WHERE combat_id=? AND kind NOT IN ('advisory_snapshot','meaningful_decision','advisory_check')", (combat_id,)).fetchone()[0],
            'decisions': db.execute('SELECT COALESCE(MAX(d.rowid),0), COALESCE(MAX(d.resolved_at),\'\') FROM decisions d JOIN evidence_events e ON e.id=d.event_id WHERE e.combat_id=?', (combat_id,)).fetchone()[:],
            'turn': db.execute('SELECT id, closed_at FROM combat_turns WHERE combat_id=? ORDER BY turn_number DESC LIMIT 1', (combat_id,)).fetchone()[:],
        }

    @atomic
    def record_advisory_snapshot(self, *, run_id, floor_id, combat_id, turn_id, state, source, screenshot_path=None):
        validate_snapshot(state)
        if not source.strip():
            raise ValueError('snapshot needs a named evidence source')
        self.initialize()
        with self._connection() as db:
            self._validate_event_context(db, run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id)
            combat = db.execute('SELECT closed_at FROM combats WHERE id=?', (combat_id,)).fetchone()
            if combat['closed_at']:
                raise ValueError('cannot record a live snapshot for a completed combat')
            turn = db.execute('SELECT id,closed_at FROM combat_turns WHERE combat_id=? ORDER BY turn_number DESC LIMIT 1', (combat_id,)).fetchone()
            if turn['id'] != turn_id or turn['closed_at']:
                raise ValueError('snapshot must belong to the latest open turn')
            observed = datetime.fromisoformat(state['observed_at'])
            changes = db.execute("SELECT observed_at FROM evidence_events WHERE combat_id=? UNION ALL SELECT observed_at FROM inventory_events WHERE run_id=? UNION ALL SELECT observed_at FROM inventory_baselines WHERE run_id=? UNION ALL SELECT observed_at FROM combat_zone_events WHERE combat_id=? UNION ALL SELECT opened_at FROM combat_turns WHERE combat_id=? UNION ALL SELECT d.resolved_at FROM decisions d JOIN evidence_events e ON e.id=d.event_id WHERE e.combat_id=? AND d.resolved_at IS NOT NULL", (combat_id,run_id,run_id,combat_id,combat_id,combat_id)).fetchall()
            if any(datetime.fromisoformat(r[0]) > observed for r in changes):
                raise ValueError('observation predates a recorded change or a newer snapshot')
            revision = self._advisory_revision(db, run_id, combat_id)
        return self.record_event(run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id,
            kind='advisory_snapshot', phase='combat', state=state,
            payload={'revision': revision, 'state_digest': state_digest(state)}, source=source, screenshot_path=screenshot_path)

    @atomic
    def advisory_context(self, *, combat_id, max_age_seconds=180):
        self.initialize()
        with self._connection() as db:
            combat = db.execute('SELECT c.*,r.ascension,r.status AS run_status FROM combats c JOIN runs r ON r.id=c.run_id WHERE c.id=?', (combat_id,)).fetchone()
            if combat is None:
                raise ValueError('unknown combat')
            row = db.execute("SELECT * FROM evidence_events WHERE combat_id=? AND kind='advisory_snapshot' ORDER BY rowid DESC LIMIT 1", (combat_id,)).fetchone()
            if row is None:
                return {'fresh': False, 'state': None, 'unknowns': ['no current advisory snapshot'], 'combat_id': combat_id}
            revision = self._advisory_revision(db, combat['run_id'], combat_id)
        state = json.loads(row['state_json'])
        payload = json.loads(row['payload_json'])
        unknowns = []
        if json.loads(json.dumps(revision)) != payload['revision']:
            unknowns.append('recorded action, inventory, pile or turn changed after this snapshot')
        if combat['closed_at'] or combat['run_status'] != 'active':
            unknowns.append('combat or run is closed')
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(state['observed_at'])).total_seconds()
        if age > max_age_seconds:
            unknowns.append('snapshot is older than the observation freshness limit')
        inventory = self.inventory_ledger(run_id=combat['run_id'], include_history=False)
        rules = relevant_rules(state, inventory)
        return {'fresh': not unknowns, 'unknowns': unknowns, 'state': state, 'age_seconds': age,
                'snapshot_id': row['id'], 'run_id': combat['run_id'], 'floor_id': combat['floor_id'],
                'combat_id': combat_id, 'turn_id': row['turn_id'], 'screenshot_path': row['screenshot_path'],
                'encounter_type': combat['encounter_type'], 'inventory': inventory, 'rules': rules,
                'boss_manifest': boss_manifest(combat['encounter_name'], combat['ascension']),
                'freshness_limit': 'Ledger changes and age are checked. Unlogged game input always requires a new observation.'}

    @atomic
    def record_checked_advice(self, *, combat_id, snapshot_id, plan, reasoning):
        context = self.advisory_context(combat_id=combat_id)
        if snapshot_id != context.get('snapshot_id'):
            raise ValueError('plan references a superseded snapshot')
        result = check_plan(context, plan)
        if not result['allowed']:
            raise ValueError('; '.join(result['reasons']))
        # All checked advice uses the one-pending-sequence gate, even hallway fights.
        decision = self.record_decision(run_id=context['run_id'], floor_id=context['floor_id'], combat_id=combat_id,
            turn_id=context['turn_id'], phase='combat', state=context['state'], options=[plan], recommendation=plan,
            reasoning=reasoning, prediction=result, screenshot_path=context['screenshot_path'],
            high_stakes=True, requires_boss_preflight=context['encounter_type'] == 'boss',
            evidence_metadata={'snapshot_id': snapshot_id, 'rule_version': context['rules']['version'],
                               'rule_ids': [r['id'] for r in context['rules']['rules']], 'boss_manifest': context['boss_manifest']})
        return {'decision_id': decision, 'check': result}

    @atomic
    def record_potion_use(self, *, run_id, floor_id, item_name, source, observed_effect, combat_id=None,
                          turn_id=None, screenshot_path=None):
        """Atomic consumption and context link, without inferring a potion's effect."""
        if not source.strip() or not isinstance(observed_effect, dict):
            raise ValueError('potion use needs a named source and observed effect object')
        inventory = self.inventory_ledger(run_id=run_id, include_history=False)
        if item_name not in inventory['current']['potion']:
            raise ValueError('consumed potion is absent from the confirmed inventory')
        now = datetime.now(timezone.utc).isoformat(); event_id = str(uuid4())
        with self._connection() as db:
            self._validate_event_context(db, run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id)
            db.execute('INSERT INTO inventory_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (event_id, run_id, floor_id, 'potion', 'consumed', item_name, None, None, screenshot_path, source, 1.0, now))
            # Use explicit column names to preserve the existing evidence schema.
            db.execute('INSERT INTO evidence_events (id,run_id,floor_id,kind,phase,state_json,payload_json,screenshot_path,source,confidence,observed_at,combat_id,turn_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (event_id, run_id, floor_id, 'potion_use', 'combat' if combat_id else 'inventory',
                 json.dumps(observed_effect), json.dumps({'potion': item_name, 'inventory_event_id': event_id}),
                 screenshot_path, source, 1.0, now, combat_id, turn_id))
        return event_id

    @atomic
    def record_campfire_advice(self, *, run_id, floor_id, state, choice, reasoning, source, screenshot_path=None):
        if choice not in ('Rest', 'Smith') or not reasoning.strip() or not source.strip():
            raise ValueError('campfire advice needs Rest/Smith, reasoning and evidence source')
        inventory = self.inventory_ledger(run_id=run_id, include_history=False)
        if inventory['coverage']['relic'] != 'complete':
            raise ValueError('confirm all relics before comparing campfire healing')
        if state.get('healing_modifiers_verified') is not True or state.get('options_verified') is not True:
            raise ValueError('verify healing modifiers and available campfire options')
        if any(r in inventory['current']['relic'] for r in ('Mark of the Bloom', 'Magic Flower', 'Regal Pillow', 'Coffee Dripper', 'Fusion Hammer')):
            raise ValueError('this healing/option modifier requires a separate verified comparison')
        comparison = campfire_comparison(state, inventory['current']['relic'])
        return self.record_decision(run_id=run_id, floor_id=floor_id, phase='rest_site', state=state,
            options=[{'choice': 'Rest'}, {'choice': 'Smith'}], recommendation={'choice': choice}, reasoning=reasoning,
            prediction=comparison, source=source, screenshot_path=screenshot_path)
