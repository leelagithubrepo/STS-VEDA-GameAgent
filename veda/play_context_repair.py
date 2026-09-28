"""Audited metadata repair for one legacy Neow/combat anchoring bug.

This is not outcome reconciliation or gameplay. It never opens a controller,
changes combat facts, rewrites historical evidence, or claims an input did not
happen. A current exact-image review, exclusive session lock, pre-change SQLite
backup, and an atomic compare-and-swap are required to apply a repair.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .menu_requests import read_menu_draft
from .play_requests import reviewed_capture_source, _CAPTURE_NAME
from .play_telemetry import outcome_request_digest, PlayTelemetry
from .saved_frame_reader import _identity
from .telemetry_database import SCHEMA_VERSION, TelemetryDatabase

SCHEMA = 'veda.play-context-repair.v1'
MAX_BYTES = 1_000_000
_PLAY_KEYS = {'play_operation_id', 'request_sha256', 'request', 'receipt', 'durable_verified_evidence',
              'controller_input_sent', 'dispatch_status', 'attempted_at'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 4096 and '\0' not in value


def _json(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _time(value):
    stamp = datetime.fromisoformat(value)
    _require(stamp.tzinfo is not None, 'recorded timestamp needs timezone')
    return stamp


def _shape(value):
    value = json.loads(_json(value))
    required = {'schema', 'expected', 'supersede_advisory_decision_id', 'repair_neow_floor',
                'neow_exit_event_id', 'observed', 'reasoning'}
    _require(isinstance(value, dict) and required <= set(value) and not set(value) - required - {'closed_first_floor'}
        and value['schema'] == SCHEMA and _text(value['reasoning']), 'exact context repair draft required')
    ids = value['expected']
    closed = value.get('closed_first_floor')
    _require(isinstance(ids, dict) and set(ids) == {'run_id', 'floor_id', 'combat_id', 'turn_id'}
        and all(_text(ids[k]) and len(ids[k]) <= 128 for k in ('run_id', 'floor_id')),
        'exact current run/floor/combat/turn IDs required')
    if closed is not None:
        _require(isinstance(closed, dict) and set(closed) == {'combat_id', 'orphaned_turn_id', 'expected_closed_at'}
                 and all(_text(i) and len(i) <= 128 for i in closed.values())
                 and ids['combat_id'] is None and ids['turn_id'] is None
                 and value['repair_neow_floor'] is True and value['supersede_advisory_decision_id'] is None,
                 'closed first-floor repair needs exact historical combat/turn/closure and no active combat or advisory')
        _time(closed['expected_closed_at'])
    else:
        _require('closed_first_floor' not in value and all(_text(ids[k]) and len(ids[k]) <= 128
                 for k in ('combat_id', 'turn_id')), 'open repair needs exact combat and turn IDs')
    decision = value['supersede_advisory_decision_id']
    _require(decision is None or _text(decision), 'advisory decision ID must be explicit or null')
    _require(type(value['repair_neow_floor']) is bool and (decision is not None or value['repair_neow_floor']),
             'at least one explicit supported correction required')
    _require(value['neow_exit_event_id'] is None or _text(value['neow_exit_event_id']), 'Neow exit event ID invalid')
    observed = value['observed']
    resources = {'hp', 'max_hp', 'gold', 'deck_size'} if closed else {'hp', 'max_hp', 'energy', 'block', 'gold'}
    fields = {'screen', 'act', 'floor', 'ascension', 'character', 'unknowns'} | resources
    if closed:
        fields |= {'route_graph_complete', 'route_choice_committed'}
    _require(isinstance(observed, dict) and set(observed) == fields, 'explicit observed combat HUD or map HUD and unknowns required')
    _require(observed['screen'] == ('map' if closed else 'combat') and type(observed['act']) is int and observed['act'] == 1
             and type(observed['floor']) is int and observed['floor'] == 1,
             'repair supports only observed Act 1 floor 1 and its explicit combat/map variant')
    if closed:
        _require(observed['route_graph_complete'] is False and observed['route_choice_committed'] is False,
                 'this map repair cannot claim a complete route graph or commit a next node')
    _require(type(observed['ascension']) is int and 0 <= observed['ascension'] <= 20
             and _text(observed['character']), 'observed character and ascension required')
    _require(all(type(observed[k]) is int and 0 <= observed[k] <= 10**9
                 for k in resources)
             and 0 < observed['hp'] <= observed['max_hp'], 'living observed combat resources required')
    _require(isinstance(observed['unknowns'], list) and len(observed['unknowns']) <= 64
             and all(_text(i) for i in observed['unknowns']), 'unknowns must remain explicit')
    return value


def _read_session(path, run_id):
    _require(path.name == 'state.json' and path.is_file() and path.stat().st_size <= MAX_BYTES,
             'existing bounded reviewed session state required')
    value = read_menu_draft(path)
    _require(value.get('schema') == 'veda.reviewed-play.v1' and value.get('run_id') == run_id
             and 'pending' in value and value['pending'] is None,
             'matching idle session required; attempted or ambiguous input cannot be repaired here')
    return value, hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def _locked_session(session, run_id):
    path = Path(session).expanduser().resolve()
    lock = path.parent / 'session.lock'
    _require(lock.is_file(), 'existing session.lock required; close the reviewed adapter first')
    with lock.open('rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('reviewed session has an owner; close it before metadata repair') from None
        try:
            value, digest = _read_session(path, run_id)
            yield path, value, digest
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _contains(value, needle):
    if isinstance(value, dict):
        return any(_contains(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, needle) for v in value)
    return value == needle


def _has_play_metadata(value):
    if isinstance(value, dict):
        return bool(set(value) & _PLAY_KEYS) or any(_has_play_metadata(v) for v in value.values())
    return isinstance(value, list) and any(_has_play_metadata(v) for v in value)


def _neow_exit(db, value, floor, combat):
    ident = value['neow_exit_event_id']
    if floor['outcome'] in {'completed', 'left', 'reward_resolved', 'neow_completed'} and ident is None:
        return {'basis': 'recorded_floor_outcome', 'outcome': floor['outcome']}
    _require(ident is not None, 'unfinished Neow row requires retained verified Neow Leave to map evidence')
    row = db.execute('SELECT * FROM evidence_events WHERE id=?', (ident,)).fetchone()
    _require(row is not None and row['run_id'] == value['expected']['run_id']
             and row['floor_id'] == floor['id'] and row['kind'] == 'play_outcome'
             and row['source'] == 'play_telemetry:reviewer', 'Neow exit evidence is not a reviewed play outcome')
    payload = json.loads(row['payload_json']); state = json.loads(row['state_json'])
    request = payload.get('request', {}); receipt = payload.get('receipt', {})
    proof = payload.get('durable_verified_evidence', {}); review = proof.get('review', {})
    outcome = review.get('outcome', {}); source = request.get('source', {})
    facts = state.get('facts', {})
    _require(request.get('status') == receipt.get('status') == 'verified' and receipt.get('unresolved') is False
             and request.get('state') == state and receipt.get('event_id') == ident
             and payload.get('request_sha256') == outcome_request_digest(request)
             and proof.get('outcome_sha256') == payload['request_sha256']
             and proof.get('source') == receipt.get('source') == source
             and review.get('complete') is True and review.get('image_sha256') == source.get('sha256')
             and proof.get('action_id') == outcome.get('action_id')
             and outcome.get('choice_id') == 'neow-leave' and outcome.get('option_ids') == ['leave']
             and facts.get('act') == 1 and facts.get('floor') == 0 and facts.get('event_id') == 'neow'
             and state.get('ui', {}).get('screen') == 'map', 'retained Neow exit proof is incomplete or inconsistent')
    exit_context = dict(value['expected'], combat_id=None, turn_id=None)
    expected_frame = 'reviewed-' + hashlib.sha256((source['captured_at'] + '\0' + source['sha256']).encode()).hexdigest()
    _require(request.get('context') == receipt.get('context') == receipt.get('next_context') == exit_context
             and receipt.get('kind') == 'outcome' and receipt.get('schema') == 'veda.play-telemetry-receipt.v1'
             and request.get('decision_id') == receipt.get('decision_id')
             and _text(request.get('operation_id'))
             and request['operation_id'] == receipt.get('operation_id') == payload.get('play_operation_id')
             and review.get('frame_id') == expected_frame
             and proof.get('basis') in {'fresh_verified_result', 'reviewed_retained_verified_result'}
             and _time(source['captured_at']) < _time(combat['opened_at']),
             'Neow exit must precede the incorrectly anchored combat')
    decision = db.execute('SELECT d.*,e.run_id,e.floor_id,e.combat_id,e.turn_id,e.source,e.kind,e.payload_json '
        'FROM decisions d JOIN evidence_events e ON e.id=d.event_id WHERE d.id=?', (receipt.get('decision_id'),)).fetchone()
    _require(decision is not None and decision['status'] == 'resolved'
             and all(decision[k] == exit_context[k] for k in exit_context)
             and decision['source'] == 'play_telemetry:reviewer' and decision['kind'] == 'meaningful_decision',
             'Neow Leave decision provenance does not match the exact run and floor')
    dp = json.loads(decision['payload_json']); dr = dp.get('request', {}); action = dr.get('action', {})
    dc = dp.get('receipt', {}); actual = json.loads(decision['actual_outcome_json'] or '{}')
    _require(dr.get('context') == dc.get('context') == exit_context
             and dr.get('operation_id') == dc.get('operation_id') == dp.get('play_operation_id') == proof.get('action_id')
             and dc.get('kind') == 'decision' and dc.get('decision_id') == decision['id']
             and dp.get('request_sha256') == outcome_request_digest(dr)
             and action.get('kind') == 'choice' and action.get('step_kind') == 'commit'
             and action.get('choice', {}).get('choice_id') == 'neow-leave'
             and action['choice'].get('option_ids') == ['leave']
             and json.loads(decision['chosen_action_json'] or '{}') == action
             and json.loads(decision['recommendation_json']) == action
             and actual.get('status') == 'verified' and actual.get('event_id') == ident
             and actual.get('source') == source and actual.get('state') == state
             and outcome.get('before_sha256') == dr.get('source', {}).get('sha256')
             and outcome.get('before_frame_id') == action['choice'].get('review', {}).get('frame_id'),
             'retained result is not linked to the exact resolved Neow Leave action')
    _require(_identity(Path(source['path']))[0] == source['sha256'], 'retained Neow exit image bytes changed')
    return {'basis': 'retained_verified_neow_exit', 'event_id': ident, 'source': source,
            'outcome_sha256': payload['request_sha256']}


def _closed_map_evidence(value, combat, turn):
    """Check retained capture provenance, without claiming automatic pixel review."""
    closing = json.loads(combat['closing_state_json'])
    _require(closing.get('screen') == 'map' and all(closing.get(k) == value['observed'][k]
             for k in ('hp', 'max_hp', 'gold')) and _text(closing.get('screenshot')),
             'completed combat needs retained map closure with matching observed resources')
    image = Path(closing['screenshot']).expanduser().resolve()
    receipt = read_menu_draft(image.with_suffix('.capture.json'))
    sha, dimensions = _identity(image)
    _require(receipt.get('schema') == 'veda.game-window-capture.v1' and receipt.get('image_path') == str(image)
             and receipt.get('image_sha256') == sha and receipt.get('dimensions') == dimensions,
             'retained victory map capture provenance is invalid')
    captured = _time(receipt['capture_requested_at']); completed = _time(receipt['capture_completed_at'])
    name = _CAPTURE_NAME.fullmatch(image.name)
    _require(name is not None, 'retained map capture filename invalid')
    fmt = '%Y%m%dT%H%M%S.%f' if '.' in name[1] else '%Y%m%dT%H%M%S'
    named = datetime.strptime(name[1], fmt).replace(tzinfo=timezone.utc)
    _require(named == captured and _time(combat['opened_at']) <= _time(turn['opened_at'])
             < captured <= completed <= _time(combat['closed_at']),
             'retained map capture chronology does not prove this closed first combat')
    _require(json.loads(turn['closing_state_json']) == {}, 'orphan turn already has unclassified ending evidence')
    return {'basis': 'recorded_victory_with_retained_map_capture',
            'source': {'path': str(image), 'sha256': sha, 'captured_at': captured.isoformat()},
            'capture_receipt_sha256': hashlib.sha256(image.with_suffix('.capture.json').read_bytes()).hexdigest(),
            'combat_closed_at': combat['closed_at'], 'automatic_pixel_recognition': False,
            'actual_number_of_turns': 'unknown', 'actual_final_turn_state': 'unknown'}


def _preconditions(db, value, session):
    ids = value['expected']; observed = value['observed']
    schema = db.execute("SELECT value FROM schema_metadata WHERE key='schema'").fetchone()
    _require(schema is not None and schema[0] == SCHEMA_VERSION, 'existing current schema required; no migration performed')
    run = db.execute('SELECT * FROM runs WHERE id=?', (ids['run_id'],)).fetchone()
    _require(run is not None and run['game'] == 'Slay the Spire' and run['status'] == 'active'
             and run['ended_at'] is None and run['ascension'] == observed['ascension']
             and run['character_name'] == observed['character'], 'current run identity or observed character/ascension conflict')
    floors = db.execute('SELECT * FROM floors WHERE run_id=? ORDER BY recorded_at DESC,rowid DESC', (ids['run_id'],)).fetchall()
    _require(floors and floors[0]['id'] == ids['floor_id'], 'expected floor is no longer latest')
    floor = floors[0]
    closed = value.get('closed_first_floor')
    if closed:
        combats = db.execute('SELECT * FROM combats WHERE run_id=?', (ids['run_id'],)).fetchall()
        _require(len(combats) == 1 and combats[0]['id'] == closed['combat_id']
                 and combats[0]['floor_id'] == ids['floor_id'] and combats[0]['outcome'] == 'victory'
                 and combats[0]['closed_at'] == closed['expected_closed_at'],
                 'expected sole completed first victory changed; no open or additional combat allowed')
    else:
        combats = db.execute('SELECT * FROM combats WHERE run_id=? AND closed_at IS NULL', (ids['run_id'],)).fetchall()
        _require(len(combats) == 1 and combats[0]['id'] == ids['combat_id'] and combats[0]['floor_id'] == ids['floor_id']
                 and combats[0]['outcome'] is None, 'expected sole open combat changed')
    combat = combats[0]
    turns = db.execute('SELECT * FROM combat_turns WHERE combat_id=? ORDER BY turn_number DESC', (combat['id'],)).fetchall()
    turn_id = closed['orphaned_turn_id'] if closed else ids['turn_id']
    _require(turns and turns[0]['id'] == turn_id and turns[0]['closed_at'] is None
             and sum(t['closed_at'] is None for t in turns) == 1, 'expected latest sole open turn changed')
    closing_proof = _closed_map_evidence(value, combat, turns[0]) if closed else None
    pending = db.execute('SELECT d.*,e.run_id,e.floor_id,e.combat_id,e.turn_id,e.source,e.kind,e.payload_json '
        'FROM decisions d JOIN evidence_events e ON e.id=d.event_id WHERE e.run_id=? AND d.status=\'recommended\'',
        (ids['run_id'],)).fetchall()
    advisory = None; wanted = value['supersede_advisory_decision_id']
    _require([r['id'] for r in pending] == ([wanted] if wanted else []),
             'unexpected unresolved decision; cannot retire controller or unrelated recommendations')
    if wanted:
        advisory = dict(pending[0]); payload = json.loads(advisory['payload_json'])
        _require(all(advisory[k] == ids[k] for k in ids) and advisory['source'] == 'veda'
                 and advisory['kind'] == 'meaningful_decision'
                 and set(payload) == {'recommendation', 'evidence', 'high_stakes', 'requires_boss_preflight'}
                 and not _has_play_metadata(payload) and advisory['chosen_action_json'] is None
                 and advisory['actual_outcome_json'] is None and advisory['resolved_at'] is None
                 and not _contains(session, wanted) and not _contains(session, advisory['event_id']),
                 'only positive advisory-only provenance can be superseded; physical action remains unknown')
    proof = None
    if value['repair_neow_floor']:
        opening = json.loads(floor['starting_state_json'])
        _require(len(floors) == 1 and floor['act'] == 1 and floor['floor'] == 0 and floor['node_type'] == 'event'
                 and opening.get('event_id') == 'neow', 'only a sole original Neow floor0 can be repaired')
        proof = _neow_exit(db, value, floor, combat)
    else:
        _require(floor['act'] == observed['act'] and floor['floor'] == observed['floor'],
                 'observed floor mismatch requires explicit supported floor repair')
        _require(value['neow_exit_event_id'] is None, 'unused Neow exit evidence is not accepted')
    return {'floor': dict(floor), 'combat': dict(combat), 'turn': dict(turns[0]),
            'advisory': advisory, 'neow_exit': proof, 'run': dict(run), 'closing_proof': closing_proof}


def _connection(database, writable=False):
    path = Path(database).expanduser().resolve()
    _require(path.is_file(), 'existing database required')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if not writable:
        db.execute('PRAGMA query_only=ON')
    return db


def validate_context_repair(value, *, database, session):
    """Read-only preflight before obtaining a fresh image; never a live-state claim."""
    value = _shape(value)
    with _locked_session(session, value['expected']['run_id']) as (_, state, _):
        db = _connection(database)
        try:
            db.execute('BEGIN')
            evidence = _preconditions(db, value, state)
            return {'schema': 'veda.play-context-repair-validation.v1', 'draft_valid': True,
                'validation_only': True, 'controller_input_sent': False, 'runtime_authorized': False,
                'requires_fresh_capture_review': True, 'requires_backup': True,
                'neow_exit_basis': evidence['neow_exit'], 'closed_first_floor_basis': evidence['closing_proof'],
                'expected': value['expected']}
        finally:
            db.close()


def _backup(database, destination, protected):
    path = Path(destination).expanduser().absolute()
    _require(path.resolve() not in protected, 'backup cannot overwrite database, session or source')
    with path.open('xb'):
        pass
    try:
        source = _connection(database)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
            _require(target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'backup integrity check failed')
        finally:
            target.close(); source.close()
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def apply_context_repair(value, *, database, session, backup, capture, reviewer, evidence_note, reviewed, now=None):
    """Apply only the two explicit corrections, together or not at all."""
    value = _shape(value)
    args = dict(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now)
    checked = reviewed_capture_source(**args)
    with _locked_session(session, value['expected']['run_id']) as (session_path, state, session_digest):
        db = _connection(database, writable=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            prior = _preconditions(db, value, state)
            captured = _time(checked['source']['captured_at'])
            _require(all(captured > _time(row[key]) for row, key in ((prior['run'], 'started_at'),
                (prior['floor'], 'recorded_at'), (prior['combat'], 'opened_at'), (prior['turn'], 'opened_at'))),
                'repair capture must follow recorded current context')
            if prior['closing_proof']:
                _require(captured > _time(prior['combat']['closed_at']), 'repair capture must follow recorded victory closure')
            PlayTelemetry(TelemetryDatabase(Path(database)))._chronology(db, value['expected'], captured)
            latest_pause = db.execute('SELECT observed_at FROM session_checkpoints WHERE run_id=? '
                'ORDER BY julianday(observed_at) DESC LIMIT 1', (value['expected']['run_id'],)).fetchone()
            _require(latest_pause is None or captured > _time(latest_pause[0]), 'repair capture predates checkpoint')
            image = Path(checked['source']['path'])
            database_path = Path(database).resolve()
            saved = _backup(database, backup, {database_path,
                *(Path(str(database_path) + suffix) for suffix in ('-wal', '-shm', '-journal')), session_path,
                session_path.parent / 'session.lock', image, image.with_suffix('.capture.json')})
            _require(reviewed_capture_source(**args) == checked, 'capture changed during repair')
            _require(_read_session(session_path, value['expected']['run_id'])[1] == session_digest,
                     'session changed during repair')
            old = value['expected']; current = dict(old)
            ident = str(uuid4()); recorded = (now or datetime.now(timezone.utc)).isoformat()
            closed = value.get('closed_first_floor')
            if value['repair_neow_floor']:
                current['floor_id'] = str(uuid4())
                # This is a current-state correction, never a fabricated opening.
                db.execute('INSERT INTO floors VALUES (?,?,?,?,?,?,?,?,?,?)', (current['floor_id'], old['run_id'],
                    1, 1, 'enemy', 'victory' if closed else None, '{}',
                    _json({'observed_map_after_victory': value['observed']}) if closed else '{}',
                    _json({'context_correction_event_id': ident,
                    'opening_state_known': False, 'observed_current_state': value['observed']}), recorded))
                if closed:
                    changed = db.execute('UPDATE combats SET floor_id=? WHERE id=? AND run_id=? AND floor_id=? '
                        'AND closed_at=? AND outcome=\'victory\'', (current['floor_id'], closed['combat_id'],
                        old['run_id'], old['floor_id'], closed['expected_closed_at'])).rowcount
                else:
                    changed = db.execute('UPDATE combats SET floor_id=? WHERE id=? AND run_id=? AND floor_id=? '
                        'AND closed_at IS NULL AND outcome IS NULL',
                        (current['floor_id'], old['combat_id'], old['run_id'], old['floor_id'])).rowcount
                _require(changed == 1, 'combat compare-and-swap failed')
            if closed:
                # Close only the orphaned ledger row. Its number and unknown
                # final state are not evidence of the actual last gameplay turn.
                summary = json.loads(prior['turn']['summary_json'])
                _require(isinstance(summary, dict) and 'administrative_closure' not in summary,
                         'orphaned turn summary cannot be safely extended')
                summary['administrative_closure'] = {'context_correction_event_id': ident,
                    'basis': 'parent combat already recorded as victory',
                    'actual_number_of_turns': 'unknown', 'actual_final_turn_state': 'unknown',
                    'closed_at_basis': 'recorded parent combat closure, not observed turn-end time'}
                changed = db.execute('UPDATE combat_turns SET closed_at=?,summary_json=? '
                    'WHERE id=? AND combat_id=? AND closed_at IS NULL AND closing_state_json=?',
                    (closed['expected_closed_at'], _json(summary), closed['orphaned_turn_id'], closed['combat_id'],
                     prior['turn']['closing_state_json'])).rowcount
                _require(changed == 1, 'orphaned turn compare-and-swap failed')
            advisory = prior['advisory']
            if advisory:
                changed = db.execute('UPDATE decisions SET status=\'skipped\',chosen_action_json=?,actual_outcome_json=?,resolved_at=? '
                    'WHERE id=? AND status=\'recommended\' AND chosen_action_json IS NULL AND actual_outcome_json IS NULL',
                    (_json({'kind': 'supersede_advisory_recommendation'}), _json({'disposition': 'superseded_for_fresh_review',
                     'physical_action_execution': 'unknown', 'context_correction_event_id': ident}), recorded, advisory['id'])).rowcount
                _require(changed == 1, 'advisory compare-and-swap failed')
            payload = {'schema': SCHEMA, 'request': value, 'old_context': old, 'new_context': current,
                'source': checked['source'], 'review': checked['review'], 'backup': saved,
                'session_sha256': session_digest, 'neow_exit_evidence': prior['neow_exit'],
                'closed_first_floor_evidence': prior['closing_proof'],
                'previous_advisory': advisory, 'previous_combat_floor_id': prior['combat']['floor_id'],
                'previous_orphaned_turn': prior['turn'] if closed else None,
                'historical_evidence_unchanged': True, 'physical_action_execution': 'unknown',
                'controller_input_sent': False, 'runtime_authorized': False}
            db.execute('INSERT INTO evidence_events '
                '(id,run_id,floor_id,combat_id,turn_id,kind,phase,observed_at,state_json,payload_json,screenshot_path,source,confidence) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, current['run_id'], current['floor_id'], current['combat_id'],
                current['turn_id'], 'play_context_correction', 'map' if closed else 'combat', checked['source']['captured_at'],
                _json(value['observed']), _json(payload), checked['source']['path'], 'play_context_repair:reviewer', None))
            _require(reviewed_capture_source(**args) == checked, 'capture changed before repair commit')
            for proof in (prior['neow_exit'], prior['closing_proof']):
                if proof and proof.get('source'):
                    _require(_identity(Path(proof['source']['path']))[0] == proof['source']['sha256'],
                             'retained correction evidence changed before commit')
                    if proof.get('capture_receipt_sha256'):
                        receipt_path = Path(proof['source']['path']).with_suffix('.capture.json')
                        _require(hashlib.sha256(receipt_path.read_bytes()).hexdigest() == proof['capture_receipt_sha256'],
                                 'retained capture receipt changed before commit')
            _require(_read_session(session_path, old['run_id'])[1] == session_digest, 'session changed before repair commit')
            db.commit()
            return {'schema': 'veda.play-context-repair-receipt.v1', 'status': 'corrected', 'event_id': ident,
                'next_context': current, 'backup': saved, 'controller_input_sent': False,
                'runtime_authorized': False, 'controller_authorized': False, 'requires_fresh_play_review': True}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
