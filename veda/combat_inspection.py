"""Reviewed tooltip dismissal only; no tactical waiver, capture, input or ledger writes.

Unknown facts stay unknown through this navigation result. Newly revealed facts
belong to the next combat review, never to a claim that navigation changed them.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from .choice_execution import _checked, _context, _json, _require, _review, _same_json, _text, _time
from .controller_state_machine import ControllerStateMachine
from .menu_controls import CONTROL_PROFILE

OBSERVATION_SCHEMA = 'veda.combat-inspection-observation.v1'
PLAN_SCHEMA = 'veda.combat-inspection-step.v1'
DRAFT_SCHEMA = 'veda.combat-inspection-draft.v1'
RESULT_SCHEMA = 'veda.combat-inspection-result.v1'
MAX_BYTES = 1_000_000
MAX_AGE = 30
_UI_KEYS = {'screen', 'phase', 'tooltip_visible', 'focused_card_id', 'selected_card_id',
            'focused_target_id', 'selected_target_id', 'tooltip_subject_id'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _core(value):
    return {key: value[key] for key in ('context', 'inventory_digest', 'resources', 'facts')}


def inspection_scope(observation):
    """Stable retry scope; new frame identities and timestamps cannot reset it."""
    return _digest(_core(observation))


@_checked
def validate_inspection_observation(observation, *, now=None, max_age_seconds=MAX_AGE):
    value = _json(observation)
    _require(set(value) == {'schema', 'frame', 'review', 'context', 'inventory_digest', 'resources', 'facts', 'ui'}
             and value['schema'] == OBSERVATION_SCHEMA, 'exact combat inspection observation required')
    frame = value['frame']
    _require(isinstance(frame, dict) and set(frame) == {'frame_id', 'image_sha256', 'observed_at'}
             and _text(frame['frame_id']) and isinstance(frame['image_sha256'], str)
             and len(frame['image_sha256']) == 64
             and all(c in '0123456789abcdef' for c in frame['image_sha256']), 'inspection frame identity required')
    captured = _time(frame['observed_at'])
    if max_age_seconds is not None:
        clock = now or datetime.now(timezone.utc)
        _require(type(max_age_seconds) in (int, float) and 0 < max_age_seconds <= MAX_AGE
                 and isinstance(clock, datetime) and clock.tzinfo is not None
                 and 0 <= (clock - captured).total_seconds() <= max_age_seconds,
                 'inspection source is stale or future dated')
    _review(value['review'], frame, 'reviewed_choice_ui')
    _context(value['context'])
    _require(value['context']['combat_id'] is not None and value['context']['turn_id'] is not None,
             'inspection requires current combat and turn context')
    inventory = value['inventory_digest']
    _require(isinstance(inventory, str) and len(inventory) == 64
             and all(c in '0123456789abcdef' for c in inventory), 'inspection inventory digest required')
    resources = value['resources']
    _require(isinstance(resources, dict) and {'hp', 'max_hp', 'energy', 'block', 'gold'} <= set(resources)
             and len(resources) <= 32 and all(_text(k) and type(v) is int and 0 <= v <= 10**9
                                            for k, v in resources.items())
             and 0 < resources['hp'] <= resources['max_hp'], 'living player resources required')
    facts = value['facts']
    _require(isinstance(facts, dict) and len(facts) <= 64
             and not set(facts) & {'observed_at', 'frame_id', 'captured_at', 'source', 'review'}
             and {'act', 'floor', 'ascension', 'character', 'player', 'hand', 'enemies', 'unknowns'} <= set(facts)
             and type(facts['act']) is int and 1 <= facts['act'] <= 4
             and type(facts['floor']) is int and 1 <= facts['floor'] <= 99
             and type(facts['ascension']) is int and 0 <= facts['ascension'] <= 20
             and _text(facts['character']) and isinstance(facts['player'], dict)
             and isinstance(facts['hand'], list) and len(facts['hand']) <= 10
             and isinstance(facts['enemies'], list) and len(facts['enemies']) <= 10
             and isinstance(facts['unknowns'], list) and all(_text(s) for s in facts['unknowns']),
             'inspection needs explicit player/hand/enemy facts and declared unknowns')
    ui = value['ui']
    _require(isinstance(ui, dict) and set(ui) == _UI_KEYS and ui['screen'] == 'combat'
             and ui['phase'] in {'tooltip', 'hand'} and type(ui['tooltip_visible']) is bool
             and ui['tooltip_visible'] == (ui['phase'] == 'tooltip')
             and ui['selected_card_id'] is None and ui['focused_target_id'] is None
             and ui['selected_target_id'] is None
             and (ui['focused_card_id'] is None or _text(ui['focused_card_id']))
             and (ui['tooltip_subject_id'] is None or _text(ui['tooltip_subject_id'])),
             'inspection requires an explicit tooltip/hand phase without card or target selection')
    if ui['phase'] == 'tooltip':
        _require(ui['focused_card_id'] is None and ui['tooltip_subject_id'] is not None,
                 'visible tooltip subject required; do not infer a hand focus')
    else:
        _require(ui['tooltip_subject_id'] is None, 'cleared hand phase cannot retain tooltip subject')
        _require(ui['focused_card_id'] is None or any(isinstance(card, dict)
                 and card.get('id') == ui['focused_card_id'] for card in facts['hand']),
                 'inspected hand focus must identify a known hand card')
    return value


def _progress(progress, scope):
    if progress is None:
        return {'schema': 'veda.combat-inspection-progress.v1', 'scope': scope, 'attempted_action_ids': []}
    _require(isinstance(progress, dict) and set(progress) == {'schema', 'scope', 'attempted_action_ids'}
             and progress['schema'] == 'veda.combat-inspection-progress.v1'
             and isinstance(progress['scope'], str) and len(progress['scope']) == 64
             and isinstance(progress['attempted_action_ids'], list) and len(progress['attempted_action_ids']) <= 1
             and all(_text(i) for i in progress['attempted_action_ids']), 'invalid persisted inspection budget')
    if progress['scope'] != scope:
        return {'schema': progress['schema'], 'scope': scope, 'attempted_action_ids': []}
    return deepcopy(progress)


@_checked
def record_inspection_attempt(progress, observation, action_id):
    """Return the budget to persist before dispatch; never dispatch from here."""
    value = validate_inspection_observation(observation, max_age_seconds=None)
    _require(value['ui']['phase'] == 'tooltip' and _text(action_id), 'tooltip action identity required')
    result = _progress(progress, inspection_scope(value))
    previous = result['attempted_action_ids']
    _require(not previous or previous == [action_id], 'tooltip clear was already attempted; inspect pending outcome, do not retry')
    result['attempted_action_ids'] = [action_id]
    return result


@_checked
def plan_tooltip_clear(observation, *, control_profile, now=None, max_age_seconds=MAX_AGE, action_id=None, progress=None):
    _require(control_profile == CONTROL_PROFILE, 'explicit default PS5 control profile required')
    _require(type(max_age_seconds) in (int, float) and 0 < max_age_seconds <= MAX_AGE,
             'inspection freshness must remain bounded to 30 seconds')
    before = validate_inspection_observation(observation, now=now, max_age_seconds=max_age_seconds)
    _require(before['ui']['phase'] == 'tooltip', 'tooltip clear requires a currently visible tooltip')
    budget = _progress(progress, inspection_scope(before))
    _require(not budget['attempted_action_ids'], 'tooltip clear was already attempted; inspect pending outcome, do not retry')
    action_id = action_id or uuid4().hex
    _require(_text(action_id), 'inspection action identity required')
    step = ControllerStateMachine().plan_end_turn({'screen_type': 'combat', 'tooltip': True, 'selected_item': None})
    _require(step['buttons'] == ['up'], 'unexpected tooltip-clearing control')
    proposal = {'schema': PLAN_SCHEMA, 'action_id': action_id, 'step_kind': 'clear_tooltip',
        'control_profile': control_profile, 'before_digest': _digest(before), 'scope': inspection_scope(before),
        'before_frame': deepcopy(before['frame']), 'max_age_seconds': max_age_seconds,
        'command': {'action': 'tap', 'buttons': ['up'], 'request_id': action_id},
        'runtime_authorized': False, 'controller_authorized': False}
    proposal['proposal_digest'] = _digest(proposal)
    return proposal


@_checked
def validate_inspection_proposal(proposal, before, *, now=None):
    value = _json(proposal)
    seal = value.pop('proposal_digest', None)
    _require(_digest(value) == seal, 'inspection proposal changed')
    expected = plan_tooltip_clear(before, control_profile=value['control_profile'], now=now,
        max_age_seconds=value['max_age_seconds'], action_id=value['action_id'])
    _require(expected['proposal_digest'] == seal, 'inspection proposal differs from reviewed source')
    return expected


@_checked
def verify_tooltip_clear(proposal, before, after, *, now=None):
    before = validate_inspection_observation(before, max_age_seconds=None)
    proposal = validate_inspection_proposal(proposal, before, now=_time(before['frame']['observed_at']))
    after = validate_inspection_observation(after, now=now, max_age_seconds=proposal['max_age_seconds'])
    _require(before['frame']['frame_id'] != after['frame']['frame_id']
             and before['frame']['image_sha256'] != after['frame']['image_sha256']
             and _time(after['frame']['observed_at']) > _time(before['frame']['observed_at']),
             'tooltip result requires a later distinct image')
    _require(_same_json(_core(before), _core(after)),
             'tooltip inspection cannot change context, resources, inventory or known facts')
    _require(after['ui']['phase'] == 'hand', 'tooltip clear not verified; retain pending input without retry')
    outcome = after['review'].get('outcome', {})
    _require(outcome.get('action_id') == proposal['action_id']
             and outcome.get('before_frame_id') == before['frame']['frame_id']
             and outcome.get('before_sha256') == before['frame']['image_sha256']
             and outcome.get('inspection') == 'clear_tooltip' and _text(outcome.get('observed_result')),
             'source-bound tooltip result review required')
    return {'schema': 'veda.combat-inspection-verification.v1', 'action_id': proposal['action_id'],
        'step_verified': True, 'logical_action_complete': False, 'requires_new_combat_review': True,
        'controller_authorized': False, 'runtime_authorized': False}


def _inventory(value, policy='strict'):
    from .menu_requests import _inventory as normalize
    return normalize(value, policy)


def _inventory_digest(value):
    from .reviewed_play import inventory_digest
    return inventory_digest(value)


def _packet(draft, checked, control_profile, clock, execute):
    _require(isinstance(draft, dict) and set(draft) - {'decision_policy'} == {'schema', 'context', 'inventory', 'resources', 'facts', 'ui', 'reasoning'}
             and draft['schema'] == DRAFT_SCHEMA and _text(draft['reasoning']), 'exact source-free inspection draft required')
    policy = draft.get('decision_policy', 'strict')
    inventory = _inventory(draft['inventory'], policy)
    observation = {'schema': OBSERVATION_SCHEMA, 'context': deepcopy(draft['context']),
        'inventory_digest': _inventory_digest(inventory), 'resources': deepcopy(draft['resources']),
        'facts': deepcopy(draft['facts']), 'ui': deepcopy(draft['ui']),
        'frame': {'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
                  'observed_at': checked['source']['captured_at']},
        'review': dict(checked['review'], kind='reviewed_choice_ui')}
    plan_tooltip_clear(observation, control_profile=control_profile, now=clock)
    return {'operation': 'execute' if execute else 'prepare', 'kind': 'combat_inspection',
        'decision_policy': policy,
        'context': deepcopy(draft['context']), 'inventory': inventory, 'observation': observation,
        'control_profile': control_profile, 'reasoning': draft['reasoning'],
        'source': deepcopy(checked['source']), 'review': deepcopy(observation['review'])}


def _offline_source(clock):
    return {'frame_id': 'private-inspection-validation-only',
        'source': {'sha256': 'f' * 64, 'captured_at': clock.isoformat()},
        'review': {'frame_id': 'private-inspection-validation-only', 'image_sha256': 'f' * 64,
                   'complete': True, 'reviewer': 'Offline schema validation'}}


@_checked
def validate_inspection_draft(draft, control_profile=CONTROL_PROFILE):
    clock = datetime.now(timezone.utc)
    _packet(_json(draft), _offline_source(clock), control_profile, clock, False)
    return {'draft_valid': True, 'validation_only': True, 'dispatchable': False,
            'requires_exact_fresh_capture_review': True, 'controller_authorized': False}


def _write(packet, output):
    destination = Path(output).expanduser().absolute()
    data = json.dumps(packet, sort_keys=True, allow_nan=False).encode()
    _require(len(data) <= MAX_BYTES, 'inspection packet exceeds size bound')
    created = False
    try:
        with destination.open('xb') as stream:
            created = True
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {'request_file': str(destination)}


@_checked
def write_inspection_request(draft, *, capture, reviewer, evidence_note, reviewed, output,
                             control_profile=CONTROL_PROFILE, execute=False, now=None):
    from .play_requests import reviewed_capture_source
    draft = _json(draft)
    validate_inspection_draft(draft, control_profile)
    _require(type(execute) is bool, 'execute must be explicit boolean')
    checked = reviewed_capture_source(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now)
    packet = _packet(draft, checked, control_profile, now or datetime.now(timezone.utc), execute)
    _require(checked == reviewed_capture_source(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now),
             'inspection capture changed during packaging')
    return _write(packet, output)


def _parse_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            _require(key not in result, 'duplicate inspection JSON key')
            result[key] = value
        return result
    _require(len(raw) <= MAX_BYTES, 'inspection JSON exceeds size bound')
    value = json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite inspection JSON')))
    _require(isinstance(value, dict), 'inspection JSON object required')
    return value


def read_inspection_json(path):
    with Path(path).open('rb') as stream:
        return _parse_json(stream.read(MAX_BYTES + 1))


def _pending(session, action_id):
    path = Path(session)
    if path.is_dir():
        path = path / 'state.json'
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    value = _parse_json(raw)
    pending = value.get('pending')
    _require(value.get('schema') == 'veda.reviewed-play.v1' and isinstance(pending, dict)
             and pending.get('action_id') == action_id and pending.get('status') == 'attempted'
             and pending['request']['kind'] == 'combat_inspection'
             and pending['request']['context']['run_id'] == value['run_id'],
             'exact attempted inspection required; finalize verified results without replay')
    return pending, hashlib.sha256(raw).hexdigest()


def _result_packet(draft, pending, checked, clock):
    _require(isinstance(draft, dict) and set(draft) == {'schema', 'action_id', 'ui', 'observed_result'}
             and draft['schema'] == RESULT_SCHEMA and draft['action_id'] == pending['action_id']
             and _text(draft['observed_result']), 'exact inspection result with unchanged facts required')
    before = pending['request']
    after = deepcopy(before)
    after.pop('operation', None); after.pop('reasoning', None)
    after['source'] = deepcopy(checked['source'])
    obs = after['observation']
    obs['frame'] = {'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
                    'observed_at': checked['source']['captured_at']}
    obs['review'] = dict(checked['review'], kind='reviewed_choice_ui', outcome={
        'action_id': pending['action_id'], 'before_frame_id': before['observation']['frame']['frame_id'],
        'before_sha256': before['source']['sha256'], 'inspection': 'clear_tooltip',
        'observed_result': draft['observed_result']})
    after['review'] = deepcopy(obs['review'])
    obs['ui'] = deepcopy(draft['ui'])
    _require(_time(checked['source']['captured_at']) > _time(pending['attempted_at']),
             'inspection result capture must follow dispatch')
    verify_tooltip_clear(pending['proposal'], before['observation'], obs, now=clock)
    return {'operation': 'verify', 'action_id': pending['action_id'],
        'operation_id': str(uuid5(NAMESPACE_URL, pending['action_id'] + ':' + checked['frame_id'])),
        'after': after, 'telemetry': {}}


@_checked
def validate_inspection_result(draft, *, session, action_id, control_profile=CONTROL_PROFILE):
    _require(control_profile == CONTROL_PROFILE, 'explicit default PS5 control profile required')
    pending, _ = _pending(session, action_id)
    clock = _time(pending['attempted_at']) + timedelta(seconds=1)
    checked = _offline_source(clock)
    if checked['source']['sha256'] == pending['request']['source']['sha256']:
        checked['source']['sha256'] = checked['review']['image_sha256'] = 'e' * 64
    _result_packet(_json(draft), pending, checked, clock)
    return {'result_valid': True, 'validation_only': True, 'dispatchable': False,
            'requires_exact_fresh_capture_review': True, 'controller_authorized': False}


@_checked
def write_inspection_result(draft, *, session, action_id, capture, reviewer, evidence_note, reviewed, output,
                            control_profile=CONTROL_PROFILE, now=None):
    from .play_requests import reviewed_capture_source
    draft = _json(draft)
    validate_inspection_result(draft, session=session, action_id=action_id, control_profile=control_profile)
    pending, digest = _pending(session, action_id)
    checked = reviewed_capture_source(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now)
    packet = _result_packet(draft, pending, checked, now or datetime.now(timezone.utc))
    _require(checked == reviewed_capture_source(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now),
             'inspection capture changed during packaging')
    _require(_pending(session, action_id)[1] == digest, 'pending inspection changed during packaging')
    return _write(packet, output)
