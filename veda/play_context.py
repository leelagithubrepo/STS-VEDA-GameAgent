"""Compact, read-only recorded startup context. Never establishes live state."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .telemetry_database import SCHEMA_VERSION, TelemetryDatabase

DEFAULT_DATABASE = Path('artifacts/veda-memory.sqlite3')
MAX_JSON_BYTES = 1_000_000
MAX_ROWS = 20_000
MAX_NAMES = 100
MAX_CANDIDATES = 20
MAX_OUTPUT_BYTES = 64_000
CONTEXT_KEYS = ('run_id', 'floor_id', 'combat_id', 'turn_id')


class _ReadOnlyInventory(TelemetryDatabase):
    """Reuse inventory semantics without invoking schema initialization or writes."""
    def __init__(self, path, connection):
        self.path, self.connection = path, connection

    def initialize(self):
        pass

    @contextmanager
    def _connection(self):
        yield self.connection


def _text(value, limit=500):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('recorded text exceeds the startup context contract')
    return value


def _object(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    if len(raw.encode() if isinstance(raw, str) else raw) > MAX_JSON_BYTES:
        raise ValueError('recorded JSON exceeds startup byte limit')
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except RecursionError as error:
        raise ValueError('recorded JSON nesting exceeds startup limit') from error
    if not isinstance(value, dict):
        raise ValueError('recorded JSON must be an object')
    return value


def _timing(value, now):
    status, age = 'unknown', None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            age = (now - parsed).total_seconds()
            status = 'future_record' if age < 0 else 'historical'
    except (ValueError, TypeError):
        pass
    return {'observed_at': _text(value), 'timing_status': status,
            'age_seconds': age, 'live': False}


def _rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params).fetchmany(MAX_CANDIDATES + 1)]


def _candidates(rows, keys):
    return [{k: _text(row[k]) if isinstance(row[k], str) else row[k] for k in keys}
            for row in rows[:MAX_CANDIDATES]]


def _context(db, run):
    run_id = run['id']; reasons = []
    floors = [dict(row) for row in db.execute('SELECT id,act,floor,node_type,outcome,recorded_at '
        'FROM floors WHERE run_id=?', (run_id,)).fetchmany(MAX_ROWS + 1)]
    if len(floors) > MAX_ROWS:
        raise ValueError('floor records exceed bounded startup context')
    invalid = False
    for row in floors:
        try:
            moment = datetime.fromisoformat(row['recorded_at'])
            if moment.tzinfo is None:
                raise ValueError('timezone missing')
            row['moment'] = moment
        except (ValueError, TypeError):
            invalid = True
            row['moment'] = datetime.min.replace(tzinfo=timezone.utc)
    floors.sort(key=lambda row: row['moment'], reverse=True)
    floor = None
    if not floors:
        reasons.append('no recorded floor')
    elif invalid or (len(floors) > 1 and floors[0]['moment'] == floors[1]['moment']):
        reasons.append('latest recorded floor is ambiguous or has an invalid timestamp')
    else:
        floor = {k: floors[0][k] for k in ('id', 'act', 'floor', 'node_type', 'outcome', 'recorded_at')}
    combats = _rows(db, 'SELECT id,floor_id,encounter_name,encounter_type,opened_at FROM combats '
        'WHERE run_id=? AND closed_at IS NULL ORDER BY id', (run_id,))
    current_combats = [c for c in combats if floor is not None and c['floor_id'] == floor['id']]
    historical_combats = [c for c in combats if floor is not None and c['floor_id'] != floor['id']]
    combat = None; turn = None; turns = []
    if historical_combats:
        reasons.append('older-floor combats remain open in the ledger; recorded context needs reconciliation')
    if len(combats) > MAX_CANDIDATES:
        reasons.append('open combat candidate limit exceeded; none selected')
    elif len(current_combats) > 1:
        reasons.append('multiple open combats; none selected')
    elif current_combats:
        combat = current_combats[0]
        turns = _rows(db, 'SELECT id,turn_number,phase,opened_at FROM combat_turns '
            'WHERE combat_id=? AND closed_at IS NULL ORDER BY turn_number DESC', (combat['id'],))
        latest = db.execute('SELECT id FROM combat_turns WHERE combat_id=? '
            'ORDER BY turn_number DESC LIMIT 1', (combat['id'],)).fetchone()
        if len(turns) > 1:
            reasons.append('multiple open turns; none selected')
        elif turns and latest[0] != turns[0]['id']:
            reasons.append('open turn is older than the latest recorded turn')
        elif turns:
            turn = turns[0]
    elif combats and floor is None:
        reasons.append('open combat does not bind to the unique latest recorded floor')
    return {'selection_status': 'resolved' if not reasons else 'needs_review', 'reasons': reasons,
            'binding': {'run_id': run_id, 'floor_id': floor['id'] if floor else None,
                        'combat_id': combat['id'] if combat else None, 'turn_id': turn['id'] if turn else None},
            'floor': floor, 'combat': combat, 'turn': turn,
            'floor_candidates': _candidates(floors, ('id', 'act', 'floor', 'recorded_at')) if floor is None else [],
            'open_combat_candidates': combats[:MAX_CANDIDATES] if combat is None else [],
            'historical_open_combats': historical_combats[:MAX_CANDIDATES],
            'open_turn_candidates': turns[:MAX_CANDIDATES] if turn is None else [],
            'candidate_lists_truncated': any(len(rows) > MAX_CANDIDATES for rows in (floors if floor is None else [], combats, turns)),
            'basis': 'Recorded lifecycle IDs only; requires a fresh screen before play.', 'live': False}


def _lifecycle(db, run, context):
    """Recognize corroborated defeat; a room victory or HP value is insufficient.

    Floors have no closed_at column: their explicit outcome is the floor-end
    evidence. Require a matching, closed final combat rather than treating any
    earlier defeat as the end of a potentially mixed run record.
    """
    floor = context['floor']
    combats = [dict(row) for row in db.execute(
        'SELECT id,floor_id,outcome,opened_at,closed_at FROM combats WHERE run_id=?',
        (run['id'],)).fetchmany(MAX_ROWS + 1)]
    defeats = [dict(row) for row in db.execute(
        "SELECT id,outcome,recorded_at FROM floors WHERE run_id=? AND lower(trim(outcome))='defeat'",
        (run['id'],)).fetchmany(MAX_ROWS + 1)]
    if len(combats) > MAX_ROWS or len(defeats) > MAX_ROWS:
        raise ValueError('lifecycle records exceed bounded startup context')
    issues = []; moments = []
    for combat in combats:
        try:
            opened = datetime.fromisoformat(combat['opened_at'])
            closed = datetime.fromisoformat(combat['closed_at']) if combat['closed_at'] is not None else None
            if opened.tzinfo is None or closed is not None and (closed.tzinfo is None or closed < opened):
                raise ValueError('invalid lifecycle chronology')
            moments.append((opened, combat))
        except (TypeError, ValueError):
            issues.append('combat lifecycle timestamps are invalid')
    moments.sort(key=lambda item: item[0], reverse=True)
    latest = moments[0][1] if moments else None
    if len(moments) > 1 and moments[0][0] == moments[1][0]:
        issues.append('latest combat is ambiguous')
        latest = None
    defeated_combats = [c for c in combats if str(c['outcome'] or '').strip().casefold() == 'defeat']
    evidence = [{'table': 'floors', 'id': f['id'], 'floor_id': f['id'], 'outcome': f['outcome'],
                 'recorded_at': f['recorded_at']} for f in defeats[:MAX_CANDIDATES]]
    evidence += [{'table': 'combats', **c} for c in defeated_combats[:MAX_CANDIDATES]]
    status = 'not_established'; outcome = None
    if defeats or defeated_combats:
        if floor is None:
            issues.append('defeat evidence has no unique latest floor')
        else:
            if any(f['id'] != floor['id'] for f in defeats) or any(c['floor_id'] != floor['id'] for c in defeated_combats):
                issues.append('earlier defeat conflicts with newer floor records')
            if (str(floor.get('outcome') or '').strip().casefold() != 'defeat' or latest is None
                    or latest['floor_id'] != floor['id'] or latest['closed_at'] is None
                    or str(latest['outcome'] or '').strip().casefold() != 'defeat'):
                issues.append('defeat requires matching latest floor and closed final combat outcomes')
            if latest is not None and any(c['id'] != latest['id'] for c in defeated_combats):
                issues.append('an earlier combat defeat conflicts with subsequent combat records')
        if any(c['closed_at'] is None for c in combats):
            issues.append('defeat evidence conflicts with an open combat')
        if not issues:
            status, outcome = 'terminal_recorded', 'defeat'
    elif run['status'] in ('completed', 'abandoned'):
        if any(c['closed_at'] is None for c in combats):
            issues.append('closed run status conflicts with an open combat')
        else:
            status = 'terminal_recorded'  # Closure is recorded; victory/defeat remains unspecified.
            evidence.append({'table': 'runs', 'id': run['id'], 'status': run['status'], 'ended_at': run.get('ended_at')})
    elif run.get('ended_at') is not None:
        issues.append('active run has a recorded end timestamp without corroborated terminal outcome')
    if issues:
        status = 'conflicting_records'
    return {'status': status, 'terminal_outcome': outcome, 'run_status': run['status'],
            'ended_at': run.get('ended_at'), 'active_flag_conflict': status == 'terminal_recorded' and run['status'] == 'active',
            'last_combat': latest, 'evidence': evidence,
            'evidence_truncated': len(defeats) > MAX_CANDIDATES or len(defeated_combats) > MAX_CANDIDATES,
            'reasons': list(dict.fromkeys(issues)), 'historical_only': True, 'live': False,
            'basis': 'Explicit lifecycle outcomes only. HP zero and ordinary room victories do not establish a run ending.'}


def _inventory(db, path, run_id):
    for table in ('inventory_events', 'inventory_baselines'):
        if db.execute(f'SELECT COUNT(*) FROM {table} WHERE run_id=?', (run_id,)).fetchone()[0] > MAX_ROWS:
            raise ValueError('inventory exceeds bounded startup replay')
    if db.execute('SELECT COUNT(*) FROM inventory_baseline_items i JOIN inventory_baselines b '
                  'ON b.id=i.baseline_id WHERE b.run_id=?', (run_id,)).fetchone()[0] > MAX_ROWS:
        raise ValueError('inventory items exceed bounded startup replay')
    ledger = _ReadOnlyInventory(path, db).inventory_ledger(run_id=run_id, include_history=False)
    categories = {}
    for kind in ('card', 'relic', 'potion'):
        counts = Counter(ledger['current'][kind])
        names = sorted(counts)
        categories[kind] = {'coverage': ledger['coverage'][kind], 'recorded_count': sum(counts.values()),
            'distinct_names': len(names), 'items': [{'name': _text(n, 160), 'count': counts[n]} for n in names[:MAX_NAMES]],
            'omitted_names': max(0, len(names) - MAX_NAMES)}
    baseline = ledger['latest_baseline']
    return {'categories': categories, 'latest_baseline_id': baseline['id'] if baseline else None,
            'latest_baseline_observed_at': baseline['observed_at'] if baseline else None,
            'basis': 'Recorded inventory replay; incomplete coverage is not an empty inventory.', 'live': False}


def _latest(db, table, run_id, now, *, advisory=False):
    extra = " AND kind='advisory_snapshot'" if advisory else ''
    phase = 'phase' if table == 'evidence_events' else 'boundary AS phase'
    turn = 'turn_id' if table == 'evidence_events' else 'NULL AS turn_id'
    row = db.execute(f'SELECT id,run_id,floor_id,combat_id,{turn},kind,{phase},observed_at,'
        f'state_json,screenshot_path,source FROM {table} WHERE run_id=?{extra} '
        'ORDER BY julianday(observed_at) DESC,rowid DESC LIMIT 1', (run_id,)).fetchone()
    if row is None:
        return None
    value = dict(row)
    state = _object(value['state_json'])
    brief = {key: state[key] for key in ('act', 'floor', 'ascension', 'hp', 'max_hp', 'energy', 'block', 'gold')
             if type(state.get(key)) is int or state.get(key) is None and key in state}
    if isinstance(state.get('hand'), list):
        brief['recorded_hand_count'] = len(state['hand'])
    return {'id': value['id'], 'kind': _text(value['kind']),
            'phase': _text(value.get('phase', value.get('boundary'))),
            'context': {k: value.get(k) for k in CONTEXT_KEYS},
            **_timing(value['observed_at'], now), 'source': _text(value['source']),
            'screenshot_path': _text(value['screenshot_path']), 'recorded_state': brief}


def _pending(db, run_id):
    rows = _rows(db, 'SELECT d.id,d.status,e.floor_id,e.combat_id,e.turn_id,e.observed_at '
        'FROM decisions d JOIN evidence_events e ON e.id=d.event_id '
        "WHERE e.run_id=? AND d.status='recommended' ORDER BY d.rowid", (run_id,))
    count = db.execute('SELECT COUNT(*) FROM decisions d JOIN evidence_events e ON e.id=d.event_id '
                      "WHERE e.run_id=? AND d.status='recommended'", (run_id,)).fetchone()[0]
    return {'count': count, 'items': rows[:MAX_CANDIDATES], 'omitted': max(0, count - MAX_CANDIDATES),
            'dispatch_status': 'unknown', 'must_not_repeat': bool(count)}


def _session(path, run_id, now):
    result = {'directory': str(path.resolve()), 'exists': path.exists(), 'matching_run': False}
    file = path / 'state.json'
    if not file.exists():
        result['status'] = 'missing_state' if path.exists() else 'absent'
        return result
    try:
        with file.open('rb') as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        value = _object(raw)
        if value.get('schema') != 'veda.reviewed-play.v1' or not isinstance(value.get('run_id'), str):
            raise ValueError('invalid reviewed session schema')
        result.update(status='recorded', run_id=_text(value['run_id'], 128),
                      matching_run=value['run_id'] == run_id, state_sha256=hashlib.sha256(raw).hexdigest())
        if 'pending' not in value:
            raise ValueError('missing pending state')
        pending = value['pending']; brief = None
        if pending is not None:
            if not isinstance(pending, dict) or pending.get('status') not in {
                    'prepared', 'preparing_dispatch', 'attempted', 'verified_pending_log'}:
                raise ValueError('invalid pending state')
            request = pending['request']; context = request['context']; source = request['source']
            if not isinstance(context, dict) or context.get('run_id') != value['run_id']:
                raise ValueError('pending session context disagrees with its run')
            brief = {'action_id': _text(pending['action_id'], 128), 'status': pending['status'],
                'kind': _text(request['kind'], 64), 'decision_id': _text(pending.get('decision_id'), 128),
                'context': {k: _text(context.get(k), 128) for k in CONTEXT_KEYS},
                'source': {'path': _text(source.get('path')), 'sha256': _text(source.get('sha256'), 64),
                           **_timing(source.get('captured_at'), now)},
                'must_not_repeat': pending['status'] != 'prepared'}
        result['pending'] = brief
        result['completed'] = value.get('completed') if type(value.get('completed')) is int else None
        result['has_verified_history'] = value.get('last_verified') is not None
        last_verified = value.get('last_verified')
        result['last_verified_action_id'] = (_text(last_verified.get('action_id'), 128)
                                            if isinstance(last_verified, dict) else None)
        result['live'] = False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result.update(status='unreadable_or_invalid', matching_run=False)
    return result


def _sessions(root, run_id, pending, now, db=None):
    paths = (root / 'CURRENT_RUN', root / run_id)
    rows = [_session(path, run_id, now) for path in paths]
    reasons = []
    # An idle legacy pointer to a closed attempt is historical, never a new run binding.
    legacy = rows[0]
    if (db is not None and legacy['status'] == 'recorded' and not legacy['matching_run']
            and legacy.get('pending') is None):
        old = db.execute('SELECT status,ended_at FROM runs WHERE id=?', (legacy['run_id'],)).fetchone()
        unresolved = db.execute("SELECT 1 FROM decisions d JOIN evidence_events e ON e.id=d.event_id "
            "WHERE e.run_id=? AND d.status='recommended' LIMIT 1", (legacy['run_id'],)).fetchone()
        if old and old['status'] in ('completed', 'abandoned') and old['ended_at'] and not unresolved:
            legacy['historical_closed_run'] = True
    matching = {r['directory']: r for r in rows if r['matching_run']}
    if any(r['status'] in ('unreadable_or_invalid', 'missing_state') for r in rows):
        reasons.append('a known session directory has unreadable or missing state; inspect it before choosing')
    if any(r['status'] == 'recorded' and not r['matching_run'] and not r.get('historical_closed_run') for r in rows):
        reasons.append('a known session directory belongs to another run; inspect it before choosing')
    both_idle = (len(matching) == 2 and all(r.get('pending') is None and r.get('completed') == 0
                                          and r.get('has_verified_history') is False for r in matching.values()))
    if len(matching) > 1 and not both_idle:
        reasons.append('both session directories belong to this run; no directory selected')
    selected = next(iter(matching)) if len(matching) == 1 and not reasons else None
    if both_idle and not reasons and not pending['count']:
        selected = str(paths[1].resolve())
    ids = {r['id'] for r in pending['items']}
    for row in matching.values():
        item = row['pending']
        if item is not None and item['decision_id'] is not None and item['decision_id'] not in ids:
            reasons.append('session decision is not among unresolved database decisions; inspect recovery state')
        if pending['count'] and (item is None or item['decision_id'] not in ids or pending['count'] != 1):
            reasons.append('session and database pending records differ; reconcile before choosing')
    if reasons:
        selected = None
    if not matching and pending['count']:
        reasons.append('database has unresolved decisions without a matching session state')
    return {'locations': rows, 'selected_directory': selected, 'reasons': list(dict.fromkeys(reasons)),
            'legacy_idle_directory_exists': both_idle,
            'must_not_repeat': bool(reasons) or bool(pending['count']) or any(
                r.get('pending') and r['pending']['must_not_repeat'] for r in matching.values()),
            'basis': 'Saved files only; no process ownership, arming or transport health established.'}


def _bounded(result):
    if len(json.dumps(result, allow_nan=False).encode()) > MAX_OUTPUT_BYTES:
        raise ValueError('startup summary exceeds compact output limit')
    return result


def read_play_context(database=DEFAULT_DATABASE, *, run_id=None, sessions_root=None, now=None):
    """Read one consistent SQLite transaction and two bounded session files.

    mode=ro/query_only prevents business/schema writes. Normal SQLite WAL/SHM
    coordination may occur; no immutable flag hides outstanding WAL commits.
    """
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError('startup context requires an existing database; none was created')
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()
                               or len(run_id) > 128 or Path(run_id).name != run_id or run_id in ('.', '..')):
        raise ValueError('run_id must be a bounded identifier, not a path')
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    root = Path(sessions_root).resolve() if sessions_root is not None else path.parent / 'reviewed-play'
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        schema = db.execute("SELECT value FROM schema_metadata WHERE key='schema'").fetchone()
        if schema is None or schema[0] != SCHEMA_VERSION:
            raise ValueError('unsupported telemetry schema; startup does not initialize or migrate')
        runs = _rows(db, 'SELECT id,game,character_name,ascension,started_at,ended_at,status FROM runs '
            + ('WHERE id=?' if run_id else "WHERE status='active'") + ' ORDER BY id', (run_id,) if run_id else ())
        result = {'schema': 'veda.play-context.v1', 'database': str(path), 'generated_at': now.isoformat(),
            'historical_only': True, 'live': False, 'runtime_authorized': False, 'controller_authorized': False,
            'selection_status': 'selected', 'reasons': [], 'run': None, 'run_candidates': runs[:MAX_CANDIDATES]}
        if len(runs) != 1 or runs[0]['status'] != 'active':
            result.update(selection_status='needs_review', reasons=[
                'multiple active runs; supply --run-id' if len(runs) > 1 else 'no matching active run'])
            result['run_candidates_truncated'] = len(runs) > MAX_CANDIDATES
            if len(runs) == 1:
                context = _context(db, runs[0])
                result['context'] = context
                result['lifecycle'] = _lifecycle(db, runs[0], context)
                pending = _pending(db, runs[0]['id'])
                result['unresolved_decisions'] = pending
                result['sessions'] = _sessions(root, runs[0]['id'], pending, now, db)
                result['reasons'] += context['reasons'] + result['sessions']['reasons']
            return _bounded(result)
        run = runs[0]
        _text(run['id'], 128)
        if Path(run['id']).name != run['id'] or run['id'] in ('.', '..'):
            raise ValueError('recorded run ID is not safe as a session directory name')
        result.update(run=run, run_candidates=[])
        context = _context(db, run)
        lifecycle = _lifecycle(db, run, context)
        result.update(context=context, lifecycle=lifecycle, inventory=_inventory(db, path, run['id']),
                      latest_advisory=_latest(db, 'evidence_events', run['id'], now, advisory=True),
                      latest_evidence=_latest(db, 'evidence_events', run['id'], now),
                      latest_checkpoint=_latest(db, 'session_checkpoints', run['id'], now))
        pending = _pending(db, run['id'])
        result['unresolved_decisions'] = pending
        result['sessions'] = _sessions(root, run['id'], pending, now, db)
        result['reasons'] = context['reasons'] + result['sessions']['reasons']
        if lifecycle['status'] == 'terminal_recorded':
            result['reasons'].append('terminal run outcome is recorded despite active status; bind an authorized new attempt before play')
        elif lifecycle['status'] == 'conflicting_records':
            result['reasons'].append('lifecycle records conflict; reconcile the run binding before play')
        if result['reasons']:
            result['selection_status'] = 'needs_review'
        return _bounded(result)
    except sqlite3.Error as error:
        raise ValueError('telemetry schema or read transaction unavailable; no migration attempted') from error
    finally:
        db.close()
