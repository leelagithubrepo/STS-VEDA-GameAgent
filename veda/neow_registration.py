"""Register an explicitly authorized, already-open Neow attempt. Never sends input."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
from uuid import uuid4, uuid5, NAMESPACE_URL

from .execution import ARM_PHRASE
from .play_context import read_play_context
from .play_requests import reviewed_capture_source, PlayRequestError
from .telemetry_database import TelemetryDatabase, SCHEMA_VERSION

SCHEMA = 'veda.neow-review.v1'
AUTHORIZATION_SCOPE = 'current_visible_attempt'
MAX_BYTES = 128_000


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _object(raw):
    _require(len(raw) <= MAX_BYTES, 'Neow review exceeds byte limit')
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, 'duplicate review field')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite review')))
    _require(isinstance(value, dict), 'review must be an object')
    return value


def read_review(path):
    with Path(path).open('rb') as stream:
        return _object(stream.read(MAX_BYTES + 1))


def validate_neow_review(review, *, now=None):
    """The reviewer declares pixels; this validates structure and saved capture identity."""
    _require(isinstance(review, dict) and review.get('schema') == SCHEMA, 'Neow review schema required')
    _require(review.get('game') == 'Slay the Spire' and review.get('review_complete') is True,
             'explicit complete Slay the Spire review required')
    facts = review.get('facts', {})
    _require(isinstance(facts, dict) and facts.get('event_id') == 'neow'
             and facts.get('event_phase') == 'opening_dialogue'
             and type(facts.get('act')) is int and facts['act'] == 1
             and type(facts.get('floor')) is int and facts['floor'] == 0,
             'review must identify the already-open Neow opening')
    _require(facts.get('character') in {'Ironclad', 'Silent', 'Defect', 'Watcher'}
             and type(facts.get('ascension')) is int and 0 <= facts['ascension'] <= 20,
             'visible character and Ascension required')
    _require(isinstance(facts.get('dialogue_text'), str) and 0 < len(facts['dialogue_text'].strip()) <= 500,
             'visible opening dialogue required')
    options = review.get('visible_options')
    _require(isinstance(options, list) and len(options) == 1 and isinstance(options[0], dict)
             and options[0].get('id') == 'talk' and options[0].get('label') in {'[Talk]', 'Talk'}
             and options[0].get('enabled') is True and options[0].get('costs') == {}
             and review.get('focused_id') == 'talk', 'single free focused Talk required')
    resources = review.get('resources', {})
    _require(isinstance(resources, dict) and set(resources) == {'hp', 'max_hp', 'gold', 'deck_size'}
             and all(type(v) is int and 0 <= v <= 10000 for v in resources.values())
             and 0 < resources['hp'] <= resources['max_hp'] and resources['deck_size'] > 0,
             'visible starting HP, gold and deck count required')
    inventory = review.get('inventory', {})
    _require(isinstance(inventory, dict) and set(inventory) == {'items', 'coverage'}, 'reviewed inventory required')
    coverage = inventory['coverage']
    _require(isinstance(coverage, dict) and set(coverage) == {'card', 'relic', 'potion'}
             and coverage['card'] in {'unknown', 'partial', 'complete'}
             and coverage['relic'] == coverage['potion'] == 'complete',
             'review visible relics/potions; keep unseen deck explicitly unknown')
    _require(isinstance(inventory['items'], list) and len(inventory['items']) <= 100, 'bounded inventory items required')
    for item in inventory['items']:
        _require(isinstance(item, dict) and set(item) == {'kind', 'item'}
                 and item['kind'] in coverage and isinstance(item['item'], str)
                 and 0 < len(item['item'].strip()) <= 160, 'invalid observed inventory item')
    cards = sum(item['kind'] == 'card' for item in inventory['items'])
    _require(cards <= resources['deck_size'] and (coverage['card'] != 'unknown' or cards == 0)
             and (coverage['card'] != 'complete' or cards == resources['deck_size']),
             'deck count and observed inventory coverage disagree')
    checked = reviewed_capture_source(capture=review.get('capture'), reviewer=review.get('reviewer'),
        evidence_note=review.get('evidence_note'), reviewed=review['review_complete'], now=now)
    return checked


@contextmanager
def _idle_sessions(root, run_ids):
    """Hold existing session locks while binding; never hide an unresolved input."""
    with ExitStack() as stack:
        directories = {root / 'CURRENT_RUN', *(root / ident for ident in run_ids)}
        if root.is_dir():
            directories.update(child for child in root.iterdir() if child.is_dir() and (child / 'state.json').exists())
        _require(len(directories) <= 100, 'too many saved sessions; inspect before registering')
        for directory in sorted(directories):
            if not directory.exists():
                continue
            state_path, lock_path = directory / 'state.json', directory / 'session.lock'
            _require(state_path.is_file() and lock_path.is_file(), 'inspect incomplete prior session before registering')
            handle = stack.enter_context(lock_path.open('r+'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError('prior reviewed adapter still owns its session; stop it before registering') from None
            with state_path.open('rb') as stream:
                state = _object(stream.read(MAX_BYTES + 1))
            _require(state.get('schema') == 'veda.reviewed-play.v1' and isinstance(state.get('run_id'), str)
                     and 'pending' in state and state['pending'] is None,
                     'prior session has pending or invalid state; reconcile before registering')
        yield


def _no_pending(db, ids, *, require_closed):
    for ident in ids:
        _require(not db.execute("SELECT 1 FROM decisions d JOIN evidence_events e ON e.id=d.event_id "
            "WHERE e.run_id=? AND d.status='recommended' LIMIT 1", (ident,)).fetchone(),
            'prior run has unresolved decisions; reconcile before registering')
        if ident not in require_closed:
            continue
        _require(not db.execute('SELECT 1 FROM combats WHERE run_id=? AND closed_at IS NULL LIMIT 1',
                               (ident,)).fetchone(), 'prior run has an open combat')
        _require(not db.execute('SELECT 1 FROM combat_turns t JOIN combats c ON c.id=t.combat_id '
            'WHERE c.run_id=? AND t.closed_at IS NULL LIMIT 1', (ident,)).fetchone(), 'prior run has an open turn')


def register_neow_attempt(database, review, *, phrase, authorization_scope, previous_run_id=None,
                          sessions_root=None, now=None):
    _require(phrase == ARM_PHRASE and authorization_scope == AUTHORIZATION_SCOPE,
             'explicit current-visible-attempt authorization required; resume-only cannot switch runs')
    checked = validate_neow_review(review, now=now)
    path = Path(database).resolve()
    _require(path.is_file(), 'existing telemetry database required')
    if previous_run_id is not None:
        _require(isinstance(previous_run_id, str) and 0 < len(previous_run_id) <= 128
                 and Path(previous_run_id).name == previous_run_id and previous_run_id not in {'.', '..'},
                 'invalid previous run ID')
    facts, resources = review['facts'], review['resources']
    root = Path(sessions_root).resolve() if sessions_root else path.parent / 'reviewed-play'
    moment = now or datetime.now(timezone.utc)
    fingerprint = {'facts': facts, 'resources': resources, 'inventory': review['inventory']}
    attempt_key = previous_run_id or checked['source']['sha256']
    connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=2000')
    try:
        connection.execute('BEGIN IMMEDIATE')
        schema = connection.execute("SELECT value FROM schema_metadata WHERE key='schema'").fetchone()
        _require(schema and schema[0] == SCHEMA_VERSION, 'existing telemetry schema required; no migration attempted')
        matching = [dict(row) for row in connection.execute(
            "SELECT * FROM runs WHERE game='Slay the Spire' AND character_name=? AND ascension IS ? AND status='active'",
            (facts['character'], facts['ascension'])).fetchmany(21)]
        _require(len(matching) <= 20, 'too many matching run records')
        previous = connection.execute('SELECT * FROM runs WHERE id=?', (previous_run_id,)).fetchone() if previous_run_id else None
        _require(previous_run_id is None or previous is not None, 'previous run record missing')
        all_active = [row['id'] for row in connection.execute(
            "SELECT id FROM runs WHERE game='Slay the Spire' AND status='active'").fetchmany(101)]
        _require(len(all_active) <= 100, 'too many active records; inspect before registering')
        ids = set(all_active) | ({previous_run_id} if previous_run_id else set())
        with _idle_sessions(root, ids):
            _no_pending(connection, ids, require_closed={row['id'] for row in matching}
                        | ({previous_run_id} if previous_run_id else set()))
            existing = []
            for row in matching:
                metadata = _object(row['metadata_json'].encode())
                marker = metadata.get('neow_start', {})
                if isinstance(marker, dict) and marker.get('attempt_key') == attempt_key:
                    existing.append((row, marker))
            if existing:
                _require(len(existing) == 1 and len(matching) == 1, 'multiple run candidates; do not choose newest')
                row, marker = existing[0]
                _require(marker.get('fingerprint') == fingerprint, 'existing attempt facts differ; review current binding')
                floors = connection.execute('SELECT id,act,floor,node_type,outcome FROM floors WHERE run_id=?',
                                            (row['id'],)).fetchall()
                _require(len(floors) == 1 and floors[0]['id'] == marker.get('floor_id')
                         and floors[0]['act'] == 1 and floors[0]['floor'] == 0 and floors[0]['outcome'] is None,
                         'registered attempt has progressed; resume it with fresh evidence')
                _require(not connection.execute('SELECT 1 FROM decisions d JOIN evidence_events e ON e.id=d.event_id '
                    'WHERE e.run_id=? LIMIT 1', (row['id'],)).fetchone(),
                    'registered attempt already has decisions; use its existing session')
                run_id, floor_id, created = row['id'], floors[0]['id'], False
            else:
                _require(not matching or len(matching) == 1 and matching[0]['id'] == previous_run_id,
                         'another active attempt exists; inspect it before registering')
                if previous is not None:
                    _require(previous['game'] == 'Slay the Spire' and previous['character_name'] == facts['character']
                             and previous['ascension'] == facts['ascension'], 'previous run identity differs')
                    if previous['status'] == 'active':
                        prior = read_play_context(path, run_id=previous_run_id, sessions_root=root, now=moment)
                        lifecycle = prior.get('lifecycle', {})
                        _require(lifecycle.get('status') == 'terminal_recorded'
                                 and lifecycle.get('terminal_outcome') == 'defeat',
                                 'previous attempt has no corroborated terminal defeat; do not silently abandon it')
                        metadata = _object(previous['metadata_json'].encode())
                        metadata['terminal_reconciliation'] = {'outcome': 'defeat', 'evidence': lifecycle['evidence'],
                            'recorded_at': moment.isoformat(), 'reason': 'authorized observed new Neow attempt'}
                        ended_at = lifecycle['last_combat']['closed_at']
                        _require(datetime.fromisoformat(ended_at) <= datetime.fromisoformat(checked['source']['captured_at']),
                                 'Neow capture predates the previous terminal result')
                        connection.execute("UPDATE runs SET status='completed',ended_at=?,metadata_json=? WHERE id=?",
                            (ended_at, json.dumps(metadata), previous_run_id))
                run_id, floor_id, created = str(uuid4()), str(uuid4()), True
                marker = {'attempt_key': attempt_key, 'previous_run_id': previous_run_id, 'floor_id': floor_id,
                          'fingerprint': fingerprint, 'source': checked['source'], 'review': checked['review'],
                          'authorization_scope': authorization_scope}
                connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,NULL,'active',?)", (run_id, 'Slay the Spire',
                    facts['character'], facts['ascension'], checked['source']['captured_at'], json.dumps({'neow_start': marker})))
                state = {**facts, **resources, 'screen': 'event'}
                connection.execute('INSERT INTO floors VALUES (?,?,?,?,?,NULL,?,?,?,?)', (floor_id, run_id, 1, 0,
                    'event', json.dumps(state), '{}', '{}', checked['source']['captured_at']))
                telemetry = TelemetryDatabase(path)
                telemetry._initialized = True
                token = telemetry._transaction_connection.set(connection)
                try:
                    telemetry.record_inventory_baseline(run_id=run_id, floor_id=floor_id,
                        **review['inventory'], screenshot_path=checked['source']['path'],
                        source='Explicit Neow opening review: ' + review['reviewer'])
                    telemetry.record_event(run_id=run_id, floor_id=floor_id, kind='neow_start_review', phase='event',
                        state=state, payload={'source': checked['source'], 'review': checked['review']},
                        screenshot_path=checked['source']['path'], source='Explicit Neow opening review')
                finally:
                    telemetry._transaction_connection.reset(token)
            _require(validate_neow_review(review, now=now) == checked, 'capture changed during registration')
            connection.commit()
            return {'status': 'registered_neow_attempt' if created else 'existing_neow_attempt',
                    'run_id': run_id, 'floor_id': floor_id, 'session_directory': str(root / run_id),
                    'controller_input_sent': False, 'armed': False,
                    'next': 'Complete bridge preflight, inspect a fresh frame, arm this run, then prepare one Neow Talk.'}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def write_neow_talk_request(database, review, *, run_id, output, now=None):
    """Build one checked prepare request; the reviewed adapter alone may send it."""
    from collections import Counter
    from .neow_start import build_neow_talk_from_review
    from .play_context import _ReadOnlyInventory
    from .reviewed_play import inventory_digest
    checked = validate_neow_review(review, now=now)
    path = Path(database).resolve()
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        run = connection.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        _require(run and run['game'] == 'Slay the Spire' and run['status'] == 'active' and run['character_name'] == review['facts']['character']
                 and run['ascension'] == review['facts']['ascension'], 'active reviewed attempt required')
        floors = connection.execute('SELECT id,act,floor,outcome FROM floors WHERE run_id=?', (run_id,)).fetchall()
        _require(len(floors) == 1 and floors[0]['act'] == 1 and floors[0]['floor'] == 0
                 and floors[0]['outcome'] is None, 'Neow Talk requires this attempt at its open starting floor')
        marker = _object(run['metadata_json'].encode()).get('neow_start', {})
        _require(isinstance(marker, dict) and marker.get('floor_id') == floors[0]['id'],
                 'Talk requires the reviewed Neow registration for this starting floor')
        inventory = _ReadOnlyInventory(path, connection).inventory_ledger(run_id=run_id, include_history=False)
        _require(inventory['coverage'] == review['inventory']['coverage'] and all(
            Counter(inventory['current'][kind]) == Counter(item['item'] for item in review['inventory']['items']
            if item['kind'] == kind) for kind in ('card', 'relic', 'potion')),
            'fresh inventory review differs from this attempt; reconcile before Talk')
        context = {'run_id': run_id, 'floor_id': floors[0]['id'], 'combat_id': None, 'turn_id': None}
        frame = {'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
                 'observed_at': checked['source']['captured_at']}
        planned = build_neow_talk_from_review(frame=frame, context=context, reviewer=review['reviewer'],
            resources=review['resources'], inventory_digest=inventory_digest(inventory), facts=review['facts'],
            visible_options=review['visible_options'], focused_id=review['focused_id'],
            control_profile=review.get('control_profile'), review_complete=True, now=now)
        request = {'operation': 'prepare', 'kind': 'choice', 'source': checked['source'], 'context': context,
                   'observation': planned['observation'], 'choice': planned['choice'],
                   'inventory': {key: inventory[key] for key in ('current', 'coverage', 'properties')},
                   'review': planned['observation']['review'],
                   'reasoning': 'Advance the reviewed Neow opening Talk once; inspect the resulting dialogue and choices.'}
        _require(validate_neow_review(review, now=now) == checked, 'capture changed during Talk packaging')
        destination = Path(output).expanduser()
        _require(destination.parent.is_dir(), 'Talk request output directory must exist')
        data = json.dumps(request, sort_keys=True, indent=2, allow_nan=False) + '\n'
        # Exclusive creation preserves existing captures, database and previous requests.
        with destination.open('x') as stream:
            stream.write(data)
        return {'request_file': str(destination.resolve())}
    finally:
        connection.close()


def write_neow_talk_result(before_request, review, *, action_id, observed_result, output, now=None):
    """Package an inspected result for the exact pending Talk; never repeats input."""
    from collections import Counter
    from .neow_start import build_neow_talk_result_from_review
    from .reviewed_play import inventory_digest
    from .saved_frame_reader import _identity
    before = read_review(before_request)
    _require(before.get('operation') == 'prepare' and before.get('kind') == 'choice', 'original Talk prepare request required')
    _require(_identity(Path(before['source']['path']))[0] == before['source']['sha256'], 'original source bytes changed')
    _require(review.get('schema') == SCHEMA and review.get('game') == 'Slay the Spire'
             and review.get('review_complete') is True, 'explicit inspected Neow result required')
    checked = reviewed_capture_source(capture=review.get('capture'), reviewer=review.get('reviewer'),
        evidence_note=review.get('evidence_note'), reviewed=review['review_complete'], now=now)
    inventory = before['inventory']
    reviewed_inventory = review.get('inventory', {})
    _require(reviewed_inventory.get('coverage') == inventory['coverage']
             and isinstance(reviewed_inventory.get('items'), list), 'review unchanged inventory coverage')
    _require(all(isinstance(item, dict) and set(item) == {'kind', 'item'}
                 and item['kind'] in {'card', 'relic', 'potion'} and isinstance(item['item'], str)
                 for item in reviewed_inventory['items']), 'invalid result inventory')
    _require(all(Counter(inventory['current'][kind]) == Counter(item['item'] for item in reviewed_inventory['items']
                 if item['kind'] == kind) for kind in ('card', 'relic', 'potion')), 'Talk result inventory changed')
    frame = {'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
             'observed_at': checked['source']['captured_at']}
    after = build_neow_talk_result_from_review(before['observation'], frame=frame, reviewer=review['reviewer'],
        resources=review.get('resources'), inventory_digest=inventory_digest(inventory), facts=review.get('facts'),
        visible_options=review.get('visible_options'), focused_id=review.get('focused_id'), action_id=action_id,
        observed_result=observed_result, review_complete=True, now=now)
    request = {'operation': 'verify', 'action_id': action_id,
               'operation_id': str(uuid5(NAMESPACE_URL, 'veda:neow-talk-result:' + action_id)), 'after': {'kind': 'choice',
        'context': before['context'], 'source': checked['source'], 'inventory': inventory,
        'observation': after, 'review': after['review']}, 'telemetry': {}}
    _require(reviewed_capture_source(capture=review['capture'], reviewer=review['reviewer'],
        evidence_note=review['evidence_note'], reviewed=True, now=now) == checked, 'result capture changed during packaging')
    destination = Path(output).expanduser()
    with destination.open('x') as stream:
        stream.write(json.dumps(request, sort_keys=True, indent=2, allow_nan=False) + '\n')
    return {'request_file': str(destination.resolve())}
