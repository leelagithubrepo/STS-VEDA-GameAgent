"""Reviewed focus navigation; no tactical waiver, capture, input or ledger writes.

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

from .choice_execution import ChoiceError, _checked, _context, _json, _require, _review, _same_json, _text, _time
from .menu_controls import CONTROL_PROFILE

OBSERVATION_SCHEMA = 'veda.combat-inspection-observation.v1'
PLAN_SCHEMA = 'veda.combat-inspection-step.v1'
DRAFT_SCHEMA = 'veda.combat-inspection-draft.v1'
RESULT_SCHEMA = 'veda.combat-inspection-result.v1'
MAX_BYTES = 1_000_000
MAX_AGE = 30
_UI_KEYS = {'screen', 'phase', 'tooltip_visible', 'focused_card_id', 'selected_card_id',
            'focused_target_id', 'selected_target_id', 'tooltip_subject_id'}
_FOCUS_KEYS = {'focus_domain', 'tooltip_kind', 'focused_subject_id', 'focus_evidence_note'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _core(value):
    return {key: value[key] for key in ('context', 'inventory_digest', 'resources', 'facts')}


def inspection_scope(observation):
    """Stable retry scope; new frame identities and timestamps cannot reset it."""
    return hashlib.sha256(json.dumps(observation['context'], sort_keys=True).encode()).hexdigest()


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
    explicit = isinstance(ui, dict) and _FOCUS_KEYS <= set(ui)
    _require(isinstance(ui, dict) and set(ui) in (_UI_KEYS, _UI_KEYS | _FOCUS_KEYS) and ui['screen'] == 'combat'
             and ui['phase'] in {'tooltip', 'inspect', 'hand'} and type(ui['tooltip_visible']) is bool
             and ui['selected_card_id'] is None and ui['focused_target_id'] is None
             and ui['selected_target_id'] is None
             and (ui['focused_card_id'] is None or _text(ui['focused_card_id']))
             and (ui['tooltip_subject_id'] is None or _text(ui['tooltip_subject_id'])),
             'inspection requires explicit focus without card or target selection')
    if explicit:
        from .combat_input import RuntimeStop, validate_combat_focus
        try:
            focus = validate_combat_focus(ui, [c.get('id') for c in facts['hand'] if isinstance(c, dict)], require_explicit=True)
        except RuntimeStop as error:
            raise ChoiceError(str(error)) from error
        _require(ui['tooltip_visible'] == (focus['tooltip_kind'] != 'none'),
                 'tooltip visibility must match the inspected tooltip kind')
        subject = focus['focused_card_id'] if focus['tooltip_kind'] == 'card_keyword' else focus['subject_id']
        _require(ui['tooltip_subject_id'] == (subject if ui['tooltip_visible'] else None),
                 'tooltip subject must match the actual focused domain')
    elif ui['phase'] == 'tooltip':
        # Retained pre-correction packets may be read only for reconciliation.
        _require(ui['tooltip_visible'], 'legacy tooltip phase needs visible tooltip')
        _require(ui['focused_card_id'] is None and ui['tooltip_subject_id'] is not None,
                 'visible tooltip subject required; do not infer a hand focus')
    else:
        _require(ui['phase'] == 'hand' and not ui['tooltip_visible'], 'legacy hand phase must have no tooltip')
        _require(ui['tooltip_subject_id'] is None, 'cleared hand phase cannot retain tooltip subject')
        _require(ui['focused_card_id'] is None or any(isinstance(card, dict)
                 and card.get('id') == ui['focused_card_id'] for card in facts['hand']),
                 'inspected hand focus must identify a known hand card')
    return value


def _progress(progress, scope):
    empty = {'schema': 'veda.combat-focus-progress.v1', 'scope': scope, 'attempts': []}
    if progress is None:
        return empty
    _require(isinstance(progress, dict) and isinstance(progress.get('scope'), str)
             and len(progress['scope']) == 64, 'invalid persisted inspection budget')
    if progress.get('schema') == 'veda.combat-inspection-progress.v1':
        _require(set(progress) == {'schema', 'scope', 'attempted_action_ids'}
                 and isinstance(progress['attempted_action_ids'], list)
                 and len(progress['attempted_action_ids']) <= 1
                 and all(_text(i) for i in progress['attempted_action_ids']), 'invalid legacy inspection budget')
        if progress['scope'] == scope:
            empty['attempts'] = [{'action_id': i, 'focus': {'domain': 'unknown', 'subject_id': 'legacy-up'}, 'button': 'up'}
                                 for i in progress['attempted_action_ids']]
        return empty
    _require(set(progress) == {'schema', 'scope', 'attempts'} and progress['schema'] == empty['schema']
             and isinstance(progress['attempts'], list) and len(progress['attempts']) <= 8
             and all(isinstance(a, dict) and set(a) - {'result'} == {'action_id', 'focus', 'button'}
                     and _text(a['action_id']) and isinstance(a['focus'], dict)
                     and set(a['focus']) == {'domain', 'subject_id'}
                     and a['button'] in {'down', 'circle', 'up'}
                     and ('result' not in a or isinstance(a['result'], dict) and
                          a['result'].get('effect') in {'unchanged', 'focus_changed', 'focus_observed'})
                     for a in progress['attempts'])
             and len({a['action_id'] for a in progress['attempts']}) == len(progress['attempts']),
             'invalid persisted focus inspection budget')
    return deepcopy(progress) if progress['scope'] == scope else empty


def _focus_key(observation):
    ui = observation['ui']
    _require(_FOCUS_KEYS <= set(ui), 'review the actual focus domain; generic tooltip Up is no longer a navigation plan')
    _require(ui['focus_domain'] in {'player_status', 'relic', 'potion', 'enemy', 'none', 'unknown'},
             'a raised hand card is already in hand; navigate cards without dismissing its keyword tooltip')
    return {'domain': ui['focus_domain'], 'subject_id': ui['focused_subject_id']}


def _next_navigation(budget, focus_key):
    _require(len(budget['attempts']) < 8,
             'focus inspection budget reached; choose a different reviewed navigation strategy')
    attempts = [a for a in budget['attempts'] if a['focus'] == focus_key]
    if not attempts:
        return 'down', 1
    _require(len(attempts) == 1 and attempts[0]['button'] == 'down'
             and attempts[0].get('result', {}).get('effect') == 'unchanged',
             'this focus was already attempted; first verify the result, then choose a different reviewed navigation strategy')
    return 'circle', 2


@_checked
def record_inspection_attempt(progress, observation, action_id):
    """Return the budget to persist before dispatch; never dispatch from here."""
    value = validate_inspection_observation(observation, max_age_seconds=None)
    _require(_text(action_id), 'focus navigation action identity required')
    focus_key = _focus_key(value)
    result = _progress(progress, inspection_scope(value))
    existing = next((a for a in result['attempts'] if a['action_id'] == action_id), None)
    if existing:
        _require(existing['focus'] == focus_key, 'inspection action identity already used for another focus')
        return result
    button, _ = _next_navigation(result, focus_key)
    result['attempts'].append({'action_id': action_id, 'focus': focus_key, 'button': button})
    return result


@_checked
def record_inspection_result(progress, action_id, focus_result):
    """Persist only the adapter's already verified focus transition; no input."""
    _require(isinstance(progress, dict), 'inspection result needs its persisted attempt')
    result = _progress(progress, progress.get('scope'))
    _require(isinstance(focus_result, dict) and focus_result.get('effect') in
             {'unchanged', 'focus_changed', 'focus_observed'}, 'verified focus transition required')
    attempt = next((a for a in result['attempts'] if a['action_id'] == action_id), None)
    _require(attempt is not None, 'inspection result has no matching attempted action')
    _require('result' not in attempt or _same_json(attempt['result'], focus_result),
             'inspection result conflicts with retained evidence')
    attempt['result'] = deepcopy(focus_result)
    return result


def _proposal(before, control_profile, max_age_seconds, action_id, button, ordinal):
    proposal = {'schema': PLAN_SCHEMA, 'action_id': action_id, 'step_kind': 'inspect_focus',
        'control_profile': control_profile, 'before_digest': _digest(before), 'scope': inspection_scope(before),
        'before_frame': deepcopy(before['frame']), 'max_age_seconds': max_age_seconds,
        'command': {'action': 'tap', 'buttons': [button], 'request_id': action_id},
        'navigation_basis': 'bounded_exploration', 'navigation_attempt': ordinal, 'expected_hand_return': False,
        'runtime_authorized': False, 'controller_authorized': False}
    proposal['proposal_digest'] = _digest(proposal)
    return proposal


@_checked
def plan_tooltip_clear(observation, *, control_profile, now=None, max_age_seconds=MAX_AGE, action_id=None, progress=None):
    _require(control_profile == CONTROL_PROFILE, 'explicit default PS5 control profile required')
    _require(type(max_age_seconds) in (int, float) and 0 < max_age_seconds <= MAX_AGE,
             'inspection freshness must remain bounded to 30 seconds')
    before = validate_inspection_observation(observation, now=now, max_age_seconds=max_age_seconds)
    focus_key = _focus_key(before)
    budget = _progress(progress, inspection_scope(before))
    button, ordinal = _next_navigation(budget, focus_key)
    action_id = action_id or uuid4().hex
    _require(_text(action_id), 'inspection action identity required')
    _require(not any(a['action_id'] == action_id for a in budget['attempts']), 'inspection action identity already attempted')
    return _proposal(before, control_profile, max_age_seconds, action_id, button, ordinal)


@_checked
def validate_inspection_proposal(proposal, before, *, now=None, allow_legacy=False, progress=None, for_result=False):
    value = _json(proposal)
    seal = value.pop('proposal_digest', None)
    _require(_digest(value) == seal, 'inspection proposal changed')
    if value.get('step_kind') == 'clear_tooltip':
        _require(allow_legacy is True, 'legacy Up proposal is reconciliation-only; do not dispatch it again')
        before = validate_inspection_observation(before, now=now, max_age_seconds=value['max_age_seconds'])
        _require(value['control_profile'] == CONTROL_PROFILE and before['ui']['phase'] == 'tooltip'
                 and _text(value['action_id']), 'invalid retained legacy tooltip proposal')
        expected = {'schema': PLAN_SCHEMA, 'action_id': value['action_id'], 'step_kind': 'clear_tooltip',
            'control_profile': CONTROL_PROFILE, 'before_digest': _digest(before), 'scope': _digest(_core(before)),
            'before_frame': deepcopy(before['frame']), 'max_age_seconds': value['max_age_seconds'],
            'command': {'action': 'tap', 'buttons': ['up'], 'request_id': value['action_id']},
            'runtime_authorized': False, 'controller_authorized': False}
        expected['proposal_digest'] = _digest(expected)
    elif for_result:
        before = validate_inspection_observation(before, now=now, max_age_seconds=value['max_age_seconds'])
        _focus_key(before)
        ordinal = value.get('navigation_attempt')
        _require(value['control_profile'] == CONTROL_PROFILE and type(ordinal) is int and ordinal in {1, 2}
                 and _text(value['action_id']), 'invalid retained focus navigation proposal')
        expected = _proposal(before, CONTROL_PROFILE, value['max_age_seconds'], value['action_id'],
                             'down' if ordinal == 1 else 'circle', ordinal)
    else:
        expected = plan_tooltip_clear(before, control_profile=value['control_profile'], now=now,
            max_age_seconds=value['max_age_seconds'], action_id=value['action_id'], progress=progress)
    _require(expected['proposal_digest'] == seal, 'inspection proposal differs from reviewed source')
    return expected


@_checked
def verify_tooltip_clear(proposal, before, after, *, now=None):
    before = validate_inspection_observation(before, max_age_seconds=None)
    proposal = validate_inspection_proposal(proposal, before, now=_time(before['frame']['observed_at']),
                                            allow_legacy=True, for_result=True)
    after = validate_inspection_observation(after, now=now, max_age_seconds=proposal['max_age_seconds'])
    _require(before['frame']['frame_id'] != after['frame']['frame_id']
             and _time(after['frame']['observed_at']) > _time(before['frame']['observed_at']),
             'focus result requires a distinct later capture')
    _require(_same_json(_core(before), _core(after)),
             'tooltip inspection cannot change context, resources, inventory or known facts')
    _require(_FOCUS_KEYS <= set(after['ui']), 'actual navigation result needs an explicit inspected focus domain')
    outcome = after['review'].get('outcome', {})
    _require(outcome.get('action_id') == proposal['action_id']
             and outcome.get('before_frame_id') == before['frame']['frame_id']
             and outcome.get('before_sha256') == before['frame']['image_sha256']
             and outcome.get('inspection') == proposal['step_kind'] and _text(outcome.get('observed_result')),
             'source-bound tooltip result review required')
    from .combat_input import focus_transition
    transition = focus_transition(before['ui'], after['ui'], [c['id'] for c in after['facts']['hand']])
    legacy_observed = (proposal['step_kind'] == 'clear_tooltip' and transition['effect'] == 'focus_observed'
                       and transition['before']['domain'] == 'unknown' and not transition['returned_to_hand']
                       and transition['after']['domain'] in {'player_status', 'relic', 'potion', 'enemy'})
    _require(before['frame']['image_sha256'] != after['frame']['image_sha256']
             or transition['effect'] == 'unchanged' or legacy_observed,
             'identical image bytes cannot prove changed focus or hand return')
    returned = transition['returned_to_hand']
    return {'schema': 'veda.combat-inspection-verification.v1', 'action_id': proposal['action_id'],
        'step_verified': True, 'logical_action_complete': False, 'requires_new_combat_review': True,
        'returned_to_hand': returned, 'navigation_effect': 'returned_to_hand' if returned else
            'unchanged_focus' if transition['effect'] == 'unchanged' else 'observed_other_focus',
        'focus_transition': transition, 'observed_focus': transition['after'],
        'observed_mismatches': ([{'field': 'focus_domain', 'predicted': 'hand', 'observed': transition['after']['domain']}]
                               if proposal['step_kind'] == 'clear_tooltip' and not returned else []),
        'navigation_basis': proposal.get('navigation_basis', 'legacy_up_observation'),
        'controller_authorized': False, 'runtime_authorized': False}


def _inventory(value, policy='strict'):
    from .menu_requests import _inventory as normalize
    return normalize(value, policy)


def _inventory_digest(value):
    from .reviewed_play import inventory_digest
    return inventory_digest(value)


def _packet(draft, checked, control_profile, clock, execute):
    _require(isinstance(draft, dict) and set(draft) - {'decision_policy'} == {'schema', 'context', 'inventory', 'resources', 'facts', 'ui', 'reasoning'}
             and draft['schema'] == DRAFT_SCHEMA, 'exact source-free inspection draft required')
    _require(_text(draft['reasoning']), 'inspection reasoning must be nonempty text of at most 256 characters')
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
        'before_sha256': before['source']['sha256'], 'inspection': pending['proposal']['step_kind'],
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
