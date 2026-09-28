"""Compact combat reviews: pure validation, then exact inspected-source binding.

No capture, controller, session write or database is used. One declared combat
snapshot supplies both ordinary visual and advisory representations. This avoids
copying game facts, not reviewing them. Unknown effects retain ordinary checks.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .decision_policy import POLICIES, assess_combat, assess_observation, plan_shape_reasons
from .choice_execution import _checked, _context, _observation, _require
from .combat_input import CombatInputAdapter, FOCUS_FIELDS, validate_combat_focus
from .controller_state_machine import ControllerStateMachine
from .execution import Reading
from .menu_requests import _inventory, _json, _text, read_menu_draft
from .play_requests import reviewed_capture_source
from .play_telemetry import validate_outcome_request
from .reviewed_play import MAX_BYTES, SCHEMA as SESSION_SCHEMA, ReviewedPlaySession, inventory_digest
from .vision import StructuredGameState, VisibleEnemy

SCHEMA = 'veda.combat-draft.v1'
RESULT_SCHEMA = 'veda.combat-result.v1'
_OFFLINE = datetime(2000, 1, 1, tzinfo=timezone.utc)
_EXTRAS = {'boss_manifest', 'rules'}
_CHANGES = {'inventory_events', 'inventory_baseline', 'zone_events', 'zone_baseline', 'zone_coverage', 'transitions'}


class _Checks(CombatInputAdapter):
    """Only pure methods of the existing adapter, with no session authority."""
    _review = ReviewedPlaySession._review
    _mutations = ReviewedPlaySession._mutations
    _transitions = staticmethod(ReviewedPlaySession._transitions)
    _combat_boundary = ReviewedPlaySession._combat_boundary

    def __init__(self, policy='strict'):
        self.machine = ControllerStateMachine()
        self.decision_policy = policy


read_combat_draft = read_menu_draft


def _confidence(value):
    return type(value) in (int, float) and 0 <= value <= 1


def _combat_inventory(value, policy):
    if policy == 'strict':
        return _inventory(value)
    _require(isinstance(value, dict) and isinstance(value.get('current'), dict)
             and isinstance(value.get('coverage'), dict)
             and set(value['coverage']) == {'card', 'relic', 'potion'}
             and all(value['coverage'][k] in {'unknown', 'partial', 'complete'} for k in value['coverage'])
             and all(isinstance(value['current'].get(k), list)
                     and all(_text(n, 256) for n in value['current'][k]) for k in value['coverage'])
             and isinstance(value.get('properties', {}), dict), 'explicit inventory items and coverage required')
    inventory_digest(value)
    return deepcopy(value)


def _reading(value, checked):
    policy = value.get('decision_policy', 'strict')
    _require(policy in POLICIES, 'decision_policy must be strict or learning')
    ids = value['context']; _context(ids)
    _require(all(_text(ids[k], 256) for k in ids), 'combat needs current run, floor, combat and turn IDs')
    state = deepcopy(value['state'])
    _require(isinstance(state, dict) and not set(state) & {
        'schema', 'observed_at', 'frame_id', 'image_sha256', 'source', 'review', 'context', 'fresh',
        'run_id', 'floor_id', 'combat_id', 'turn_id'},
        'one source-free combat state required; schema, source time and IDs are derived')
    state.update(schema='spire.advisory.v1', observed_at=checked['source']['captured_at'])
    inventory = _combat_inventory(value['inventory'], policy)
    encounter = value['encounter']; perception = value['perception']
    _require(isinstance(encounter, dict) and set(encounter) == {'name', 'type', 'confidence'}
             and _text(encounter['name'], 128) and encounter['type'] in {'enemy', 'elite', 'boss'}
             and _confidence(encounter['confidence']), 'explicit encounter name, enemy/elite/boss type and confidence required')
    _require(isinstance(perception, dict) and set(perception) == {'confidence', 'end_turn_damage_confidence'}
             and all(_confidence(v) for v in perception.values()), 'explicit combat and end-turn-damage review confidence required')
    _require(isinstance(value['unknowns'], list) and all(_text(v, 256) for v in value['unknowns']),
             'unknowns must be explicit bounded descriptions')
    ui = deepcopy(value['ui'])
    _require(isinstance(ui, dict) and not set(ui) - {'screen_type', 'phase', 'focused_card_id',
        'selected_card_id', 'focused_target_id', 'hand_order', 'target_order', 'control_profile', 'recovery_direction'} - FOCUS_FIELDS
        and ui.get('screen_type', 'combat') == 'combat'
        and ui.get('phase') in {'hand', 'card_selected', 'targeting', 'tooltip', 'inspect'}
        and {'phase', 'focused_card_id', 'selected_card_id', 'focused_target_id', 'hand_order'} <= set(ui),
        'combat UI needs explicit phase, focus, selection and hand order; no source or controls')
    ui['screen_type'] = 'combat'
    shape = assess_observation(None, {'state': state, 'inventory': inventory, 'fresh': True,
                                    'unknowns': value['unknowns']}, policy=policy)
    _require(shape['allowed'], 'invalid reviewed combat: ' + '; '.join(shape['hard_reasons']))
    cards = state['hand']; enemies = state['enemies']
    validate_combat_focus(ui, [card['id'] for card in cards], require_explicit=policy == 'learning')
    visible = []
    for enemy in enemies:
        hits = enemy.get('intent_hits')
        _require(_confidence(enemy.get('intent_damage_confidence')), 'each enemy needs explicit intent damage confidence')
        total = None if hits is None else sum(hits)
        _require('intent_total_damage' not in enemy or enemy['intent_total_damage'] == total,
                 'displayed enemy total conflicts with reviewed individual hits')
        visible.append(VisibleEnemy(enemy['name'], enemy.get('hp'), enemy.get('max_hp'), enemy.get('intent'),
            enemy.get('block'), None if hits is None else tuple(hits), total, enemy['intent_damage_confidence']))
    visual = StructuredGameState('COMBAT', perception['confidence'], character=state.get('character'),
        ascension=state.get('ascension'), act=state.get('act'), floor=state.get('floor'),
        hp=state.get('hp'), max_hp=state.get('max_hp'), energy=state.get('energy'), block=state.get('block'),
        gold=state.get('gold'), player_strength=state.get('strength'), player_weak=state.get('weak'),
        player_vulnerable=state.get('vulnerable'), player_frail=state.get('frail'),
        encounter_kind=encounter['type'], encounter_confidence=encounter['confidence'],
        boss_name=encounter['name'] if encounter['type'] == 'boss' else None,
        boss_confidence=encounter['confidence'] if encounter['type'] == 'boss' else 0,
        hand=tuple(card['name'] for card in cards), hand_complete=state.get('hand_complete'),
        hand_details=tuple({'name': c['name'], 'title_color': c.get('title_color'),
            'upgraded': c.get('upgraded'), 'current_cost': c.get('cost')} for c in cards),
        enemies=tuple(visible), end_turn_damage=state.get('end_turn_damage'),
        end_turn_damage_confidence=perception['end_turn_damage_confidence'])
    context = {'state': state, 'inventory': inventory, 'fresh': True, 'unknowns': deepcopy(value['unknowns']),
        'decision_policy': policy,
        'encounter_type': encounter['type'], 'encounter_name': encounter['name'],
        **{key: deepcopy(value[key]) for key in _EXTRAS if key in value}}
    assessment = assess_observation(visual, context, policy=policy)
    _require(assessment['allowed'], 'incomplete reviewed combat: ' + '; '.join(assessment['hard_reasons']))
    return Reading(checked['frame_id'], checked['source']['sha256'], visual, context, ui,
        encounter['name'], ids['run_id'], ids['floor_id'], ids['turn_id'])


def _draft(value):
    value = _json(value)
    required = {'schema', 'context', 'state', 'inventory', 'encounter', 'perception', 'ui', 'unknowns', 'plan', 'reasoning'}
    _require(isinstance(value, dict) and required <= set(value) and not set(value) - required - _EXTRAS - {'decision_policy'}
             and value['schema'] == SCHEMA, 'source-free veda.combat-draft.v1 required')
    _require(_text(value['reasoning']), 'reasoning must be nonempty text of at most 4096 UTF-8 bytes')
    _require(value.get('decision_policy', 'strict') in POLICIES, 'decision_policy must be strict or learning')
    reasons = plan_shape_reasons(value['plan'])
    _require(not reasons, '; '.join(reasons))
    return value


def _request(draft, checked):
    reading = _reading(draft, checked)
    policy = draft.get('decision_policy', 'strict')
    result = assess_combat(reading.context, draft['plan'], policy=policy, visual=reading.state)
    _require(result['allowed'], 'move rejected: ' + '; '.join(result['reasons']))
    step, expected = _Checks(policy)._input(reading, draft['plan']['steps'][0])
    return {'operation': 'prepare', 'kind': 'combat', 'context': deepcopy(draft['context']),
        'source': deepcopy(checked['source']), 'review': deepcopy(checked['review']), 'reading': asdict(reading),
        'plan': deepcopy(draft['plan']), 'reasoning': draft['reasoning'], 'decision_policy': policy}, step, expected


def _synthetic(*, time=_OFFLINE, sha='0' * 64):
    frame = 'private-combat-validation-only'
    return {'frame_id': frame,
        'source': {'sha256': sha, 'captured_at': time.isoformat(), 'path': '/validation-only/no-image.png',
                   'origin': 'reviewer', 'evidence_note': 'Offline schema validation only.'},
        'review': {'complete': True, 'reviewer': 'offline combat schema validation only',
                   'frame_id': frame, 'image_sha256': sha}}


@_checked
def validate_combat_draft(value):
    draft = _draft(value)
    request, step, expected = _request(draft, _synthetic())
    assessment = assess_combat(request['reading']['context'], draft['plan'], policy=request['decision_policy'],
                              visual=Reading.from_dict(request['reading']).state)
    return {'schema': 'veda.combat-draft-validation.v1', 'draft_valid': True,
        'draft_digest': hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest(),
        'validation_only': True, 'source_bound': False, 'dispatchable': False, 'controller_input_sent': False,
        'next_atomic_input_preview': {'buttons': step['buttons'], 'expected_kind': expected['kind']},
        'decision_policy': request['decision_policy'], 'assessment': assessment,
        'requires_exact_fresh_capture_review': True}


def _write(packet, output, *, protected=()):
    _require(isinstance(output, (str, Path)) and _text(str(output)), 'new output path required')
    path = Path(output).expanduser().absolute()
    _require(path.resolve() not in {Path(p).resolve() for p in protected}, 'output cannot overwrite source or session')
    data = (json.dumps(packet, allow_nan=False, sort_keys=True, indent=2) + '\n').encode()
    _require(len(data) <= MAX_BYTES, 'combat packet exceeds adapter byte bound')
    created = False
    try:
        with path.open('xb') as stream:
            created = True; stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise
    return {'request_file': str(path)}


@_checked
def write_combat_request(value, *, capture, reviewer, evidence_note, reviewed, output, now=None, execute=False, session=None):
    draft = _draft(value); validate_combat_draft(draft)
    args = dict(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now,
                max_age_seconds=None if session is not None else 30)
    checked = reviewed_capture_source(**args)
    request, _, _ = _request(draft, checked)
    if session is not None:
        from .evidence_continuity import bind_session
        request['evidence_binding'] = bind_session(session, draft['context'], checked['source'])
    _require(type(execute) is bool, 'execute must be an explicit boolean')
    if execute:
        request['operation'] = 'execute'
    _require(reviewed_capture_source(**args) == checked, 'capture changed during combat packaging')
    image = Path(checked['source']['path'])
    return _write(request, output, protected=(image, image.with_suffix('.capture.json')))


def _unique(items):
    result = {}
    for key, item in items:
        _require(key not in result, 'duplicate session JSON key'); result[key] = item
    return result


def read_pending(session, action_id):
    path = Path(session)
    if path.is_dir():
        path = path / 'state.json'
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    _require(len(raw) <= MAX_BYTES, 'session exceeds byte bound')
    def nonfinite(_):
        raise ValueError('session must contain finite JSON')
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=nonfinite)
    pending = value.get('pending')
    _require(value.get('schema') == SESSION_SCHEMA and isinstance(pending, dict)
             and pending.get('status') == 'attempted' and pending.get('action_id') == action_id,
             'exact attempted action required; verified_pending_log needs finalize, never replay')
    _require(pending['request']['kind'] == 'combat' and pending['request']['context']['run_id'] == value['run_id'],
             'pending combat action required')
    policy = value.get('decision_policy', 'strict')
    _require(policy in POLICIES and pending['request'].get('decision_policy', 'strict') == policy
             and pending['request']['reading']['context'].get('decision_policy', 'strict') == policy,
             'pending combat policy differs from its session')
    return pending, hashlib.sha256(raw).hexdigest()


def _result_draft(value, pending):
    value = _json(value)
    required = {'schema', 'action_id', 'inventory', 'observed_result'}
    combat = {'state', 'ui', 'encounter', 'perception', 'unknowns'}
    extras = {'context', 'telemetry', 'card_destination', 'next_turn', 'decision_policy'} | _EXTRAS
    _require(isinstance(value, dict) and required <= set(value) and value['schema'] == RESULT_SCHEMA
             and value['action_id'] == pending['action_id'] and _text(value['observed_result'], 256),
             'compact veda.combat-result.v1 and exact pending action required')
    if 'boundary' in value:
        _require(not set(value) - required - {'boundary', 'telemetry', 'decision_policy'}, 'boundary review cannot carry a combat snapshot or controls')
    else:
        _require(combat <= set(value) and not set(value) - required - combat - extras,
                 'actual combat result needs state, UI, encounter, perception and unknowns; no source fields')
    _require(value.get('decision_policy', pending['request'].get('decision_policy', 'strict')) ==
             pending['request'].get('decision_policy', 'strict'), 'result cannot change the pending decision policy')
    return value


def _boundary(draft, pending, checked, inventory):
    boundary = draft['boundary']; old = pending['request']
    _require(isinstance(boundary, dict) and set(boundary) == {
        'screen', 'resources', 'facts', 'choice_id', 'layout_id', 'options', 'focused_id'},
        'boundary needs exact observed result screen/resources/facts/options/focus and UI identities')
    ui = {k: deepcopy(boundary[k]) for k in ('screen', 'choice_id', 'layout_id', 'options', 'focused_id')}
    _require(ui['screen'] in {'selection', 'reward', 'card_reward', 'result'}
             and isinstance(ui['options'], list) and all(isinstance(o, dict) and set(o) == {
                 'id', 'label', 'enabled', 'costs'} for o in ui['options']), 'boundary result options cannot carry controls')
    ui.update(phase='result', order=[o['id'] for o in ui['options']], selection_mode='immediate',
              required_count=0, selected_ids=[], pending_ids=[], navigation=[])
    context = deepcopy(old['context'])
    if ui['screen'] != 'selection':
        context.update(combat_id=None, turn_id=None)
    review = dict(checked['review'], kind='reviewed_choice_ui', outcome={
        'action_id': pending['action_id'], 'before_frame_id': old['reading']['frame_id'],
        'before_sha256': old['source']['sha256'], 'action': deepcopy(old['plan']['steps'][0]),
        'observed_result': draft['observed_result']})
    obs = {'schema': 'veda.choice-observation.v1', 'frame': {'frame_id': checked['frame_id'],
        'image_sha256': checked['source']['sha256'], 'observed_at': checked['source']['captured_at']},
        'review': review, 'context': context, 'inventory_digest': inventory_digest(inventory),
        'resources': deepcopy(boundary['resources']), 'facts': deepcopy(boundary['facts']), 'ui': ui}
    _observation(obs, datetime.fromisoformat(checked['source']['captured_at']), 30)
    after = {'kind': 'choice', 'context': context, 'source': checked['source'], 'review': review,
             'observation': obs, 'inventory': inventory}
    after['decision_policy'] = old.get('decision_policy', 'strict')
    _Checks(after['decision_policy'])._combat_boundary(pending, after)
    transitions = []
    if ui['screen'] != 'selection':
        closing = {'screen': ui['screen'], **deepcopy(boundary['resources']), **deepcopy(boundary['facts'])}
        outcome = 'victory' if boundary['facts']['combat_outcome'] == 'win' else 'defeat'
        transitions = [{'kind': 'end_turn', 'closing_state': closing, 'evidence_note': draft['observed_result']},
            {'kind': 'end_combat', 'outcome': outcome, 'closing_state': deepcopy(closing), 'evidence_note': draft['observed_result']}]
    return after, transitions


def _result_request(draft, pending, checked):
    old = pending['request']; original = old['reading']; old_context = original['context']
    policy = old.get('decision_policy', 'strict')
    inventory = _combat_inventory(old_context['inventory'] if draft['inventory'] == 'unchanged' else draft['inventory'], policy)
    changes = deepcopy(draft.get('telemetry', {}))
    _require(isinstance(changes, dict) and not set(changes) - _CHANGES, 'unknown combat telemetry override')
    checks = _Checks(policy)
    if 'boundary' in draft:
        after, transitions = _boundary(draft, pending, checked, inventory)
        _require('transitions' not in changes, 'boundary lifecycle is derived from the actual reviewed result')
        if transitions:
            changes['transitions'] = transitions
        complete = True
        outcome_state = {k: after['observation'][k] for k in ('resources', 'facts', 'ui')}
    else:
        state = deepcopy(old_context['state'] if draft['state'] == 'unchanged' else draft['state'])
        if draft['state'] == 'unchanged':
            state.pop('schema', None); state.pop('observed_at', None)
        encounter = {'name': original['encounter_name'], 'type': old_context['encounter_type'],
                     'confidence': original['state']['encounter_confidence']}
        value = {**deepcopy(draft), 'state': state, 'inventory': inventory,
            'decision_policy': policy,
            'context': deepcopy(draft.get('context', old['context'])),
            'encounter': encounter if draft['encounter'] == 'unchanged' else draft['encounter']}
        if 'next_turn' in draft:
            transition = draft['next_turn']
            _require(isinstance(transition, dict) and set(transition) == {'turn_number'}
                     and type(transition['turn_number']) is int and transition['turn_number'] >= 2
                     and pending['proposal']['expected']['kind'] == 'end_turn'
                     and old['plan']['steps'][0]['kind'] == 'end_turn'
                     and draft['state'] != 'unchanged' and state.get('turn') == transition['turn_number']
                     and ('turn' not in old_context['state'] or
                          transition['turn_number'] == old_context['state']['turn'] + 1)
                     and 'context' not in draft and 'transitions' not in changes,
                     'next_turn requires the observed sequential new turn after committed End Turn; IDs/lifecycle are derived')
            value['context']['turn_id'] = 'observed-turn-' + str(uuid5(NAMESPACE_URL, pending['action_id']))
        for key in _EXTRAS:
            _require(key not in old_context or key in value,
                     key + ' needs an explicit actual value or reviewed unchanged declaration')
            if value.get(key) == 'unchanged':
                _require(key in old_context, 'unchanged ' + key + ' has no prior reviewed value')
                value[key] = deepcopy(old_context[key])
        reading = _reading(value, checked)
        from .reviewed_play import verify_combat_observation
        verification = verify_combat_observation(Reading.from_dict(original), reading, old['plan']['steps'][0],
                                 pending['proposal']['expected'], pending['proposal']['checked'], policy=policy)
        complete = verification['logical_action_complete']
        after = {'kind': 'combat', 'context': value['context'], 'source': deepcopy(checked['source']),
                 'review': deepcopy(checked['review']), 'reading': asdict(reading), 'decision_policy': policy}
        outcome_state = reading.context['state']
        if 'next_turn' in draft:
            changes['transitions'] = [
                {'kind': 'end_turn', 'closing_state': {'last_observed_before_end_turn': deepcopy(old_context['state'])},
                 'evidence_note': 'Retained last observation before End Turn; ' + draft['observed_result']},
                {'kind': 'start_turn', 'turn_number': draft['next_turn']['turn_number'], 'phase': 'combat',
                 'opening_state': deepcopy(outcome_state), 'evidence_note': draft['observed_result']}]
        if 'card_destination' in draft:
            expected = pending['proposal']['expected']
            _require(complete and expected['kind'] == 'advance' and 'card' in expected
                     and draft['card_destination'] in {'discard', 'exhaust'} and 'zone_events' not in changes,
                     'observed card destination requires one completed card and no duplicate zone events')
            changes['zone_events'] = [{'kind': 'play', 'card_name': expected['card']['name'],
                'from_zone': 'hand', 'to_zone': draft['card_destination'], 'evidence_note': draft['observed_result']}]
    if not complete:
        _require(changes.get('zone_coverage', 'complete') == 'complete',
                 'verified unchanged combat navigation preserves prior zone knowledge')
        changes['zone_coverage'] = 'complete'
    if changes:
        after['mutation_review'] = dict(checked['review'], kind='reviewed_mutation', changes=deepcopy(changes))
    checks._transitions(old['context'], after['context'], changes.get('transitions', []))
    checks._mutations(old, after, changes, complete)
    operation = str(uuid5(NAMESPACE_URL, 'veda.combat-result:' + pending['action_id'] + ':' + checked['frame_id']))
    validate_outcome_request({'schema': 'veda.play-telemetry.v1', 'operation_id': operation,
        'context': old['context'], 'decision_id': pending['decision_id'], 'source': checked['source'],
        'state': outcome_state, 'status': 'verified', 'evidence_note': draft['observed_result'], **changes})
    return {'operation': 'verify', 'action_id': pending['action_id'], 'operation_id': operation,
            'after': after, 'telemetry': changes}, complete


@_checked
def validate_combat_result(value, *, session, action_id):
    pending, digest = read_pending(session, action_id); draft = _result_draft(value, pending)
    _, complete = _result_request(draft, pending, _synthetic(sha='f' * 64))
    return {'schema': 'veda.combat-result-validation.v1', 'result_valid': True,
        'validation_only': True, 'source_bound': False, 'dispatchable': False, 'controller_input_sent': False,
        'logical_action_complete': complete, 'pending_digest': digest}


@_checked
def write_combat_result(value, *, session, action_id, capture, reviewer, evidence_note, reviewed, output, now=None):
    pending, digest = read_pending(session, action_id); draft = _result_draft(value, pending)
    validate_combat_result(draft, session=session, action_id=action_id)
    args = dict(capture=capture, reviewer=reviewer, evidence_note=evidence_note, reviewed=reviewed, now=now, max_age_seconds=None)
    checked = reviewed_capture_source(**args)
    _require(checked['source']['sha256'] != pending['request']['source']['sha256']
             and datetime.fromisoformat(checked['source']['captured_at']) > datetime.fromisoformat(pending['attempted_at']),
             'combat result needs a distinct image captured after actual dispatch')
    packet, _ = _result_request(draft, pending, checked)
    _require(reviewed_capture_source(**args) == checked, 'capture changed during combat result packaging')
    _require(read_pending(session, action_id)[1] == digest, 'pending action changed during combat result packaging')
    image = Path(checked['source']['path'])
    return _write(packet, output, protected=(session, image, image.with_suffix('.capture.json')))
