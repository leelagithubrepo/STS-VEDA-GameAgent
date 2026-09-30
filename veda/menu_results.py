"""Compact explicit menu-result reviews correlated to a durable pending action.

These helpers do not observe pixels, send inputs or change the session/ledger.
An ``unchanged`` declaration means the caller inspected and confirmed that
field, not that a successful controller acknowledgement proves it unchanged.
"""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .choice_execution import SCHEMA as OBSERVATION_SCHEMA, _checked, _digest, _require, verify_choice_step
from .menu_controls import CONTROL_PROFILE, bind_reviewed_menu_controls
from .menu_requests import _inventory, _json, _text, _ui
from .play_requests import reviewed_capture_source
from .reviewed_play import MAX_BYTES, SCHEMA as SESSION_SCHEMA, inventory_digest, verify_choice_observation

SCHEMA = 'veda.menu-result.v1'


def read_pending(path, action_id):
    path = Path(path)
    if path.is_dir():
        path = path / 'state.json'
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    _require(len(raw) <= MAX_BYTES, 'session exceeds byte bound')
    # Ordinary sessions are bounded by MAX_BYTES; use the same strict JSON parser.
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
    pending = value.get('pending')
    _require(value.get('schema') == SESSION_SCHEMA and isinstance(pending, dict)
             and pending.get('status') == 'attempted' and pending.get('action_id') == action_id,
             'exact attempted action required; verified_pending_log needs finalize or metadata repair')
    _require(pending['request']['kind'] == 'choice'
             and pending['request']['context']['run_id'] == value['run_id'], 'pending menu action required')
    return pending, hashlib.sha256(raw).hexdigest()


def _unique(items):
    value = {}
    for key, item in items:
        _require(key not in value, 'duplicate session JSON key')
        value[key] = item
    return value


def _nonfinite(_):
    raise ValueError('session must contain finite JSON')


def _draft(value, pending):
    draft = _json(value)
    required = {'schema', 'action_id', 'resources', 'inventory', 'facts', 'result', 'observed_result'}
    _require(isinstance(draft, dict) and required <= set(draft)
             and not set(draft) - required - {'context', 'telemetry', 'decision_policy', 'no_op_reconciliation'} and draft['schema'] == SCHEMA,
             'compact veda.menu-result.v1 required; no source, hashes, reviews or frame IDs')
    _require('decision_policy' not in draft
             or draft['decision_policy'] == pending['request'].get('decision_policy', 'strict'),
             'result cannot change the pending action decision policy')
    _require(draft['action_id'] == pending['action_id'], 'result action ID must match the pending action')
    _require(_text(draft['observed_result'], 2048),
             'observed_result must be nonempty text of at most 2048 UTF-8 bytes')
    _require(isinstance(draft['result'], dict), 'explicit observed result required')
    return draft


def _unbound_ui(value):
    ui = deepcopy(value)
    for key in ('navigation', 'confirm'):
        ui.pop(key, None)
    for option in ui['options']:
        activate = option.pop('activate', None)
        # A focus result declares the other visible option facts unchanged.
        # Preserve the actual hint's meaning, then bind it to the inspected
        # after-frame instead of silently changing Square/Triangle to Cross.
        if ui.get('menu_family') in {'loot_rewards', 'loot_cards', 'campfire_options', 'campfire_exit'} and activate:
            proof = activate.get('evidence', {})
            if proof.get('kind') == 'visible_hint':
                option['activate_hint'] = {'button': activate['button'], 'hint_text': proof['hint_text']}
        shortcut = option.pop('shortcut', None)
        if (ui.get('menu_family', '').startswith('shop_') or ui.get('menu_family') == 'loot_rewards') and shortcut:
            proof = shortcut.get('evidence', {})
            if proof.get('kind') == 'visible_hint':
                option['shortcut_hint'] = {'button': shortcut['button'], 'hint_text': proof['hint_text']}
    return ui


def _observed_ui(draft, pending, checked):
    result = draft['result']
    kind = result.get('kind')
    before_ui = pending['request']['observation']['ui']
    if kind == 'room_entry':
        _require(before_ui.get('menu_family') == 'map_nodes'
                 and pending['proposal']['step_kind'] == 'commit', 'pending map-node activation required')
        ui = {'screen': result['screen'], 'phase': 'result',
              'choice_id': 'arrival-' + result['node_id'], 'layout_id': 'reviewed-room-arrival-v1',
              'options': [], 'order': [], 'focused_id': None, 'selection_mode': 'immediate',
              'required_count': 0, 'selected_ids': [], 'pending_ids': [], 'navigation': []}
    elif kind == 'map_view':
        _require(set(result) == {'kind', 'view'} and pending['proposal']['step_kind'] == 'inspect'
                 and before_ui.get('menu_family') == 'map_inspect', 'pending map inspection required')
        ui = {'screen': 'map', 'phase': 'result', 'choice_id': before_ui['choice_id'],
              'layout_id': before_ui['layout_id'], 'options': [], 'order': [], 'focused_id': None,
              'selection_mode': 'immediate', 'required_count': 0, 'selected_ids': [],
              'pending_ids': [], 'navigation': [], 'map_view': deepcopy(result['view'])}
    elif kind == 'map_reobservation':
        _require(set(result) == {'kind', 'ui'} and pending['proposal']['step_kind'] == 'focus'
                 and pending['request'].get('decision_policy') == 'learning'
                 and before_ui.get('menu_family') == 'map_nodes'
                 and result['ui'].get('menu_family') == 'map_nodes',
                 'map reobservation requires pending learning-mode map focus and actual node UI')
        ui = _ui(result['ui'])
    elif kind == 'map_noop':
        _require(set(result) == {'kind', 'ui'} and pending['proposal']['step_kind'] == 'commit'
                 and before_ui.get('menu_family') == 'map_nodes'
                 and result['ui'].get('menu_family') == 'map_nodes',
                 'map no-op reconciliation requires pending map activation and actual node UI')
        ui = _ui(result['ui'])
    elif kind == 'focus':
        _require(set(result) == {'kind', 'focused_id'} and pending['proposal']['step_kind'] == 'focus',
                 'focus review requires the pending focus step')
        ui = _unbound_ui(before_ui)
        if before_ui.get('menu_family') in {'shop_stock', 'shop_remove'}:
            from .shop_controls import record_focus
            ui = record_focus(ui, pending['command']['buttons'][0], result['focused_id'])
        ui['focused_id'] = result['focused_id']
        ui = _ui(ui)
    elif kind in {'shop_confirmation', 'loot_confirmation'}:
        _require(set(result) == {'kind', 'selected_id', 'confirm_hint'}
                 and pending['proposal']['step_kind'] == 'select'
                 and before_ui.get('menu_family') == ('shop_stock' if kind == 'shop_confirmation' else 'loot_cards')
                 and pending['request']['choice']['option_ids'] == [result['selected_id']],
                 'observe the exact pending selection and confirmation hint')
        ui = _unbound_ui(before_ui)
        ui.update(phase='confirm', focused_id=result['selected_id'],
                  selected_ids=[result['selected_id']], pending_ids=[result['selected_id']],
                  confirm_hint=deepcopy(result['confirm_hint']))
        ui = _ui(ui)
    elif kind == 'upgrade_preview':
        _require(set(result) == {'kind', 'selected_id', 'observed_upgrade_text', 'confirm_hint'}
                 and pending['proposal']['step_kind'] == 'select'
                 and before_ui.get('menu_family') == 'card_upgrade', 'pending upgrade selection required')
        selected = result['selected_id']
        options = [o for o in before_ui['options'] if o['id'] == selected]
        _require(len(options) == 1, 'observed preview card must be in the reviewed picker')
        option = _unbound_ui({'options': options})['options'][0]
        ui = _ui({'menu_family': 'card_upgrade', 'choice_id': before_ui['choice_id'],
            'layout_id': before_ui['layout_id'], 'phase': 'confirm', 'focused_id': selected,
            'options': [option], 'selected_ids': [selected], 'pending_ids': [selected],
            'upgrade_preview': {'option_id': selected, 'before_name': option['card']['name'],
                'after_name': option['card']['upgrade_name'], 'observed_upgrade_text': result['observed_upgrade_text']},
            'confirm_hint': result['confirm_hint']})
    else:
        _require(kind == 'menu' and set(result) == {'kind', 'ui'} and isinstance(result['ui'], dict),
                 'result kind must be focus, upgrade_preview or an explicitly observed menu')
        value = result['ui']
        if value.get('phase') == 'result':
            _require(set(value) == {'screen', 'phase', 'choice_id', 'layout_id', 'options', 'focused_id'},
                     'result-only UI needs screen, phase, choice/layout IDs, options and focus; no controls')
            ui = deepcopy(value)
            _require(isinstance(ui['options'], list) and all(isinstance(o, dict) and
                     set(o) == {'id', 'label', 'enabled', 'costs'} for o in ui['options']),
                     'result-only options cannot carry input controls')
            ui.update(order=[o['id'] for o in ui['options']], selection_mode='immediate',
                      required_count=0, selected_ids=[], pending_ids=[], navigation=[])
        else:
            ui = _ui(value)
    hint = ui.pop('confirm_hint', None)
    if hint is not None:
        _require(isinstance(hint, dict) and set(hint) == {'button', 'hint_text'}, 'actual visible confirmation hint required')
        ui['confirm'] = {'button': hint['button'], 'evidence': {
            'kind': 'visible_hint', 'reviewer': checked['review']['reviewer'],
            'meaning': 'confirm:' + ui['choice_id'], 'layout_id': ui['layout_id'],
            'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
            'hint_text': hint['hint_text']}}
    for option in ui['options']:
        hint = option.pop('shortcut_hint', None)
        if hint is not None:
            option['shortcut'] = {'button': hint['button'], 'evidence': {
                'kind': 'visible_hint', 'reviewer': checked['review']['reviewer'],
                'meaning': 'activate:' + option['id'], 'layout_id': ui['layout_id'],
                'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
                'hint_text': hint['hint_text']}}
    return ui


def _room_entry(draft, pending, resources, facts, inventory):
    """Package observed room lifecycle, never infer a monster or opening hand."""
    result = draft['result']
    _require(set(result) <= {'kind', 'screen', 'node_id', 'encounter', 'opening_hand'}
             and {'kind', 'screen', 'node_id'} <= set(result)
             and pending['request']['observation']['ui'].get('menu_family') == 'map_nodes'
             and pending['proposal']['step_kind'] == 'commit', 'observed map-room entry required')
    before = pending['request']
    _require(before['choice']['option_ids'] == [result['node_id']], 'arrival must name selected map node')
    selected = next(o for o in before['observation']['ui']['options'] if o['id'] == result['node_id'])['node']
    _require(isinstance(facts, dict) and all(facts.get(key) == selected[key] for key in ('act', 'floor'))
             and facts.get('current_node_id') == result['node_id'], 'actual room floor/node differs from map selection')
    revealed_kind = {'combat': 'enemy', 'rest': 'rest', 'shop': 'merchant',
                     'treasure': 'treasure', 'event': 'event', 'reward': 'reward'}.get(result['screen'])
    learning = before.get('decision_policy', 'strict') == 'learning'
    kind_matches = (selected['kind'] == 'event' and facts.get('node_type') == revealed_kind
                    or selected['kind'] != 'event' and facts.get('node_type') == selected['kind'])
    _require(revealed_kind is not None and
             (kind_matches or learning and selected['kind'] != 'event'),
             'actual room kind conflicts with the selected map node')
    post = before['choice']['postconditions']
    branches = [post] if 'alternatives' not in post else [b['postconditions'] for b in post['alternatives']]
    # Use the room actually observed in learning, preserving the selected node.
    if learning:
        from .menu_requests import map_arrival_context
        contexts = [map_arrival_context(before['context'], result['node_id'], result['screen'])]
    else:
        contexts = [b['context'] for b in branches if b['screen'] == result['screen'] and b['phase'] == 'result']
    _require(contexts and all(c == contexts[0] for c in contexts), 'room screen needs one unambiguous declared context')
    context = deepcopy(contexts[0])
    _require(context['floor_id'] != before['context']['floor_id'], 'room entry needs a new provisional floor context')
    _require('context' not in draft or draft['context'] == context, 'room context is derived from checked arrival')
    note = draft['observed_result']
    state = {'screen': result['screen'], 'act': facts['act'], 'floor': facts['floor'],
             'node_id': result['node_id'], **deepcopy(resources)}
    transitions = [{'kind': 'advance_floor', 'act': facts['act'], 'floor': facts['floor'],
        'node_type': facts['node_type'], 'previous_outcome': 'departed',
        'previous_ending_state': {'screen': 'map', **deepcopy(before['observation']['resources'])},
        'starting_state': deepcopy(state), 'evidence_note': note}]
    changes = {'transitions': transitions}
    if result['screen'] == 'combat':
        encounter = result.get('encounter')
        _require(isinstance(encounter, dict) and set(encounter) == {'name', 'type', 'opening_state'}
                 and _text(encounter['name'], 128) and encounter['type'] in {'enemy', 'elite', 'boss'}
                 and isinstance(encounter['opening_state'], dict), 'observed encounter identity and state required')
        _require(selected['kind'] == 'event' or encounter['type'] == selected['kind'],
                 'map room classification conflicts with observed encounter type')
        _require(selected['kind'] != 'event' or encounter['type'] == 'enemy',
                 'question room needs a normal combat or separately reviewed exceptional transition')
        _require(context['combat_id'] is not None and context['turn_id'] is not None,
                 'combat arrival needs provisional combat and turn context')
        opening = deepcopy(encounter['opening_state'])
        _require(type(opening.get('turn')) is int and opening['turn'] == 1 and opening.get('screen') == 'combat'
                 and all(opening.get(k) == v for k, v in resources.items())
                 and not set(opening) & {'run_id', 'floor_id', 'combat_id', 'turn_id', 'observed_at'},
                 'observed opening turn must match resources and omit database IDs/source timestamps')
        for key in ('act', 'floor', 'ascension'):
            if key in opening:
                _require(key in facts and type(opening[key]) is int and opening[key] == facts[key],
                         'opening state conflicts with inspected ' + key)
        transitions.extend([
            {'kind': 'start_combat', 'opening_state': opening, 'encounter_name': encounter['name'],
             'encounter_type': encounter['type'], 'evidence_note': note},
            {'kind': 'start_turn', 'turn_number': 1, 'phase': 'combat', 'opening_state': deepcopy(opening),
             'evidence_note': note}])
        if 'opening_hand' in result:
            _require(inventory['coverage']['card'] == 'complete', 'opening zone baseline needs complete known deck')
            hand = result['opening_hand']
            _require(isinstance(hand, list) and all(_text(name, 128) for name in hand),
                     'opening hand must explicitly list observed card names')
            if 'hand' in opening:
                _require(isinstance(opening['hand'], list), 'opening state hand must be a list')
                names = [card.get('name') if isinstance(card, dict) else card for card in opening['hand']]
                _require(all(_text(name, 128) for name in names) and Counter(names) == Counter(hand),
                         'opening state hand conflicts with zone baseline')
            changes.update(zone_coverage='complete', zone_baseline={
                'deck': deepcopy(inventory['current']['card']), 'hand': deepcopy(result['opening_hand']),
                'complete': True, 'opening': True, 'evidence_note': note})
    else:
        _require('encounter' not in result and 'opening_hand' not in result
                 and context['combat_id'] is None and context['turn_id'] is None,
                 'noncombat arrival cannot assert combat or card-zone facts')
    _require(not draft.get('telemetry'), 'room lifecycle is derived from the inspected arrival, not overridden')
    return context, changes


def _request(draft, pending, checked, control_profile, clock):
    _require(control_profile == CONTROL_PROFILE, 'explicit default PS5 control profile required')
    before = pending['request']
    policy = before.get('decision_policy', 'strict')
    old = before['observation']
    def observed(name, previous):
        value = draft[name]
        return deepcopy(previous if value == 'unchanged' else value)
    if draft['inventory'] == 'selected_upgrade_applied':
        _require(old['ui'].get('menu_family') == 'card_upgrade'
                 and pending['proposal']['step_kind'] == 'commit', 'selected upgrade declaration requires its pending confirm')
        target = next(o for o in old['ui']['options'] if o['id'] == before['choice']['option_ids'][0])
        inventory = _inventory(before['inventory'], policy)
        _require(inventory['coverage']['card'] == 'complete'
                 and target['card']['name'] in inventory['current']['card'], 'complete before inventory must contain selected card')
        inventory['current']['card'].remove(target['card']['name'])
        inventory['current']['card'].append(target['card']['upgrade_name'])
    else:
        inventory = _inventory(observed('inventory', before['inventory']), policy)
    review = dict(checked['review'], kind='reviewed_choice_ui', outcome={
        'action_id': pending['action_id'], 'before_frame_id': old['frame']['frame_id'],
        'before_sha256': before['source']['sha256'], 'choice_id': before['choice']['choice_id'],
        'option_ids': before['choice']['option_ids'], 'observed_result': draft['observed_result']})
    if draft['result'].get('kind') == 'map_reobservation':
        review['outcome']['map_reobservation'] = True
    if draft['result'].get('kind') == 'map_noop':
        review['outcome']['map_noop'] = True
    if draft.get('no_op_reconciliation'):
        _require(policy == 'learning' and pending['proposal']['step_kind'] == 'commit',
                 'no-op reconciliation is learning-mode commit recovery only')
        review['outcome']['no_op_reconciliation'] = True
    resources = observed('resources', old['resources'])
    facts = observed('facts', old['facts'])
    context = deepcopy(draft.get('context', before['context']))
    room_changes = None
    room_entry_draft = draft
    # A settled map activation can reveal a noncombat room before the player
    # has built a room-entry packet. In learning mode, promote that exact
    # observed result into the room lifecycle once, preserving the pending
    # action and avoiding a second map click or a manual adapter repair.
    actual_ui = draft.get('result', {}).get('ui', {})
    if (policy == 'learning' and draft.get('result', {}).get('kind') == 'menu'
            and old['ui'].get('menu_family') == 'map_nodes'
            and actual_ui.get('screen') in {'rest', 'shop', 'treasure', 'event'}):
        room_entry_draft = deepcopy(draft)
        room_entry_draft['result'] = {
            'kind': 'room_entry',
            'screen': actual_ui['screen'],
            'node_id': before['choice']['option_ids'][0],
        }
    if room_entry_draft['result'].get('kind') == 'room_entry':
        context, room_changes = _room_entry(room_entry_draft, pending, resources, facts, inventory)
    observation = {'schema': OBSERVATION_SCHEMA,
        'frame': {'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
                  'observed_at': checked['source']['captured_at']},
        'review': review, 'context': context, 'inventory_digest': inventory_digest(inventory),
        'resources': resources, 'facts': facts,
        'ui': _observed_ui(draft, pending, checked)}
    if observation['ui'].get('menu_family'):
        observation = bind_reviewed_menu_controls(observation, control_profile=control_profile, now=clock, max_age_seconds=None)
    proposal_for_verify = pending['proposal']
    if draft['result'].get('kind') == 'room_entry' and policy == 'learning':
        selected_id = pending['request']['choice']['option_ids'][0]
        selected_node = next(o['node'] for o in old['ui']['options'] if o['id'] == selected_id)
        if selected_node.get('kind') == 'event':
            # A question-mark node is provisional. For the actual observed
            # room branch, verify the revealed facts rather than requiring the
            # icon's placeholder kind to survive as a game fact.
            proposal_for_verify = deepcopy(pending['proposal'])
            branches = proposal_for_verify['choice'].get('postconditions', {}).get('alternatives')
            if branches is None:
                branches = [{'postconditions': proposal_for_verify['choice']['postconditions']}]
            for branch in branches:
                post = branch['postconditions']
                if post.get('screen') == observation['ui']['screen']:
                    post['facts'] = deepcopy(facts)
            sealed = deepcopy(proposal_for_verify)
            sealed.pop('proposal_digest', None)
            proposal_for_verify['proposal_digest'] = _digest(sealed)
    verified = verify_choice_observation(proposal_for_verify, old, observation,
                                         policy=policy, now=clock, historical=True,
                                         allow_noop_reconciliation=bool(draft.get('no_op_reconciliation')))
    changes = deepcopy(draft.get('telemetry', {}))
    _require(isinstance(changes, dict) and not set(changes) - {
        'inventory_events', 'inventory_baseline', 'zone_events', 'zone_baseline',
        'zone_coverage', 'transitions'}, 'unknown telemetry override')
    if room_changes is not None:
        changes = room_changes
    if old['ui'].get('menu_family') == 'card_upgrade' and pending['proposal']['step_kind'] == 'commit':
        target = next(o for o in old['ui']['options'] if o['id'] == before['choice']['option_ids'][0])
        event = {'kind': 'card', 'action': 'replaced', 'item': target['card']['name'],
                 'related_item': target['card']['upgrade_name'], 'evidence_note': draft['observed_result']}
        _require(not changes, 'upgrade telemetry is derived from the reviewed selected-card result')
        changes = {'inventory_events': [event]}
    if old['ui'].get('menu_family') in {'loot_rewards', 'loot_cards', 'shop_stock', 'shop_remove'} and pending['proposal']['step_kind'] == 'commit':
        _require(not changes, 'inventory events are derived from the actual reviewed inventory')
        events = []
        for kind in ('card', 'relic', 'potion'):
            prior = Counter(before['inventory']['current'][kind])
            actual = Counter(inventory['current'][kind])
            for item, count in (actual - prior).items():
                events.extend({'kind': kind, 'action': 'acquired', 'item': item,
                               'evidence_note': draft['observed_result']} for _ in range(count))
            for item, count in (prior - actual).items():
                events.extend({'kind': kind, 'action': 'removed', 'item': item,
                               'evidence_note': draft['observed_result']} for _ in range(count))
        if events:
            changes = {'inventory_events': events}
    after = {'kind': 'choice', 'decision_policy': policy, 'context': context, 'source': checked['source'],
             'review': deepcopy(review), 'observation': observation, 'inventory': inventory}
    if room_changes is not None:
        from .map_transitions import validate_map_arrival
        validate_map_arrival(before, after, changes, policy=policy)
    if changes:
        after['mutation_review'] = dict(checked['review'], kind='reviewed_mutation', changes=deepcopy(changes))
    operation_id = str(uuid5(NAMESPACE_URL, pending['action_id'] + ':' + checked['frame_id']))
    from .play_telemetry import validate_outcome_request
    validation_source = dict(checked['source'])
    # Only schema validation uses a private synthetic source; it never exits
    # validate_menu_result or becomes a request_file.
    validation_source.setdefault('path', '/validation-only/no-image.png')
    validation_source.setdefault('origin', 'reviewer')
    validation_source.setdefault('evidence_note', 'Offline structure validation only.')
    validate_outcome_request({'schema': 'veda.play-telemetry.v1', 'operation_id': operation_id,
        'context': before['context'], 'decision_id': pending['decision_id'], 'source': validation_source,
        'state': {k: observation[k] for k in ('resources', 'facts', 'ui')},
        'status': 'verified', 'evidence_note': draft['observed_result'], **changes})
    _require(verified['choice_complete'] or not changes, 'navigation cannot assert game mutations')
    return {'operation': 'verify', 'action_id': pending['action_id'], 'operation_id': operation_id,
            'after': after, 'telemetry': changes}


@_checked
def validate_menu_result(draft, *, session, action_id, control_profile):
    pending, digest = read_pending(session, action_id)
    draft = _draft(draft, pending)
    clock = datetime.fromisoformat(pending['attempted_at']) + timedelta(seconds=1)
    sha = 'f' * 64 if pending['request']['source']['sha256'] != 'f' * 64 else 'e' * 64
    checked = {'frame_id': 'private-result-validation-only',
        'source': {'sha256': sha, 'captured_at': clock.isoformat()},
        'review': {'complete': True, 'reviewer': 'offline result schema validation',
                   'frame_id': 'private-result-validation-only', 'image_sha256': sha}}
    _request(draft, pending, checked, control_profile, clock)
    return {'result_valid': True, 'validation_only': True, 'dispatchable': False,
            'source_bound': False, 'controller_input_sent': False, 'pending_digest': digest}


@_checked
def write_menu_result(draft, *, session, action_id, capture, reviewer, evidence_note,
                      reviewed, control_profile, output, now=None):
    pending, digest = read_pending(session, action_id)
    draft = _draft(draft, pending)
    checked = reviewed_capture_source(capture=capture, reviewer=reviewer,
        evidence_note=evidence_note, reviewed=reviewed, now=now, max_age_seconds=None)
    _require(datetime.fromisoformat(checked['source']['captured_at']) > datetime.fromisoformat(pending['attempted_at']),
             'result capture must follow the actual dispatch')
    packet = _request(draft, pending, checked, control_profile, now or datetime.now(timezone.utc))
    data = (json.dumps(packet, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    _require(len(data) <= MAX_BYTES, 'result packet exceeds byte bound')
    _require(reviewed_capture_source(capture=capture, reviewer=reviewer,
        evidence_note=evidence_note, reviewed=reviewed, now=now, max_age_seconds=None) == checked, 'capture changed during result packaging')
    _require(read_pending(session, action_id)[1] == digest, 'pending action changed during result packaging')
    _require(isinstance(output, (str, Path)) and _text(str(output)), 'new output path required')
    destination = Path(output).expanduser().absolute()
    # Exclusive output cannot replace a draft, receipt, pending journal or old request.
    created = False
    try:
        with destination.open('xb') as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {'request_file': str(destination)}
