"""Merchant decision packaging. Reads declarations; never buys or controls a game."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .map_survey import read_json
from .menu_requests import _inventory, _require, _text, _ui, validate_menu_draft
from .menu_controls import CONTROL_PROFILE
from .menu_results import _unbound_ui
from .shop_controls import FAMILIES
from .shop_observation import validate_stock, shopping_notes

SCHEMA = 'veda.shop-snapshot.v1'


def session_context(path):
    path = Path(path)
    state = read_json(path/'state.json' if path.is_dir() else path)
    _require(state.get('schema') == 'veda.reviewed-play.v1' and not state.get('pending'),
             'reconcile the exact pending action before preparing another merchant action')
    context = (state.get('evidence_continuity') or {}).get('context')
    context = context or state.get('last_verified', {}).get('receipt', {}).get('next_context')
    _require(isinstance(context, dict) and context.get('run_id') == state.get('run_id'),
             'session needs its canonical verified context; do not type an ID from memory')
    return deepcopy(context)


def resolve_snapshot(snapshot, session):
    value = deepcopy(snapshot)
    context = session_context(session)
    _require(value.get('context') == 'session' or value.get('context') == context,
             'snapshot context differs from canonical session context')
    value['context'] = context
    return value


def snapshot_from_result(packet, session):
    """Reuse actual verified state, never predictions from a submitted action."""
    path = Path(session)
    state = read_json(path/'state.json' if path.is_dir() else path)
    last = state.get('last_verified', {})
    _require(packet.get('operation') == 'verify' and packet.get('action_id') == last.get('action_id')
             and not state.get('pending'), 'exact already-verified result required')
    after = packet['after']
    from .evidence_continuity import verified_result_digest
    _require(last.get('verified_after_digest') == verified_result_digest(after),
             'result contents differ or this legacy result lacks a seal; inspect a current snapshot')
    _require(all(after['source'][k] == last['source'][k] for k in ('sha256', 'captured_at')),
             'result source differs from the verified source')
    obs = after['observation']
    ui = _unbound_ui(obs['ui'])
    ui.pop('control_layout', None)
    if obs['ui'].get('confirm'):
        binding = obs['ui']['confirm']
        _require(binding['evidence']['kind'] == 'visible_hint', 'actual removal confirmation hint required')
        ui['confirm_hint'] = {'button': binding['button'], 'hint_text': binding['evidence']['hint_text']}
    return {'schema': SCHEMA, 'context': session_context(session), 'inventory': deepcopy(after['inventory']),
            'resources': deepcopy(obs['resources']), 'facts': deepcopy(obs['facts']), 'ui': ui}


def decision_key(snapshot):
    ui = snapshot['ui']
    data = {k: snapshot[k] for k in ('context', 'inventory', 'resources', 'facts')}
    fields = ('menu_family', 'choice_id', 'options', 'phase', 'selected_ids', 'pending_ids')
    if ui.get('menu_family') == 'shop_stock':
        # The same purchase decision covers focus, select and confirmation.
        fields = ('menu_family', 'choice_id', 'options')
    data['ui'] = {k: ui.get(k) for k in fields}
    # Focus and learned directional transitions do not change buying strategy.
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def plan_shop(snapshot, decision=None):
    _require(isinstance(snapshot, dict) and set(snapshot) == {'schema', 'context', 'inventory', 'resources', 'facts', 'ui'}
             and snapshot['schema'] == SCHEMA, 'reviewed merchant snapshot required')
    value = deepcopy(snapshot)
    inventory = _inventory(value['inventory'], 'learning')
    ui = _ui(value['ui'])
    _require(ui['menu_family'] in FAMILIES, 'merchant screen family required')
    validate_stock(ui)
    # Normalize optional choose-phase fields so reconstructed results keep a key.
    value['ui'] = ui
    key = decision_key(value)
    options = [o for o in ui['options'] if o['enabled']]
    selected, reason = None, None
    if decision is not None:
        _require(isinstance(decision, dict) and set(decision) == {'option_id', 'reason', 'decision_key'}
                 and decision['decision_key'] == key and _text(decision['reason']),
                 'shop choice, inventory, prices or resources changed; bind the inspected current choice')
        selected = next((o for o in options if o['id'] == decision['option_id']), None)
        _require(selected is not None, 'chosen merchant option is no longer available')
        reason = decision['reason']
    elif ui['menu_family'] == 'shop_entry':
        selected = next((o for o in options if o.get('role') == 'open'), None)
        reason = 'Open the merchant and inspect stock, prices and removal before deciding.'
    elif ui['menu_family'] == 'shop_exit':
        selected = next((o for o in options if o.get('role') == 'proceed'), None)
        reason = 'Merchant closed; use the visible Proceed control to return to the map.'
    if selected is None:
        return {'status': 'strategy_required', 'decision_key': key, 'options': options,
                'notes': shopping_notes(value), 'controller_input_sent': False,
                'instruction': 'Compare affordable stock and removal against the deck, potions and route. Choose once and retain the decision through focus taps; leaving is a strategic choice, not a workaround for navigation.'}
    resources, after_inventory = deepcopy(value['resources']), deepcopy(inventory)
    role = selected.get('role')
    cost = selected['costs']
    _require(set(cost) <= {'gold'}, 'merchant options use the inspected gold cost')
    price = cost.get('gold', 0)
    _require(type(price) is int and 0 <= price <= resources['gold'], 'purchase exceeds observed gold or price is unread')
    resources['gold'] -= price
    screen, phase = 'shop', 'choose'
    if role in {'open', 'skip', 'leave', 'proceed'}:
        _require(not cost and selected.get('shortcut_hint') is not None, 'free merchant boundary needs its visible hint')
        if role == 'proceed':
            screen, phase = 'map', 'result'
    elif role in {'card', 'relic', 'potion'}:
        name = selected.get('offer', {}).get('name')
        _require(_text(name, 256) and 'unidentified' not in name.casefold(), 'observed item identity required')
        if role == 'potion':
            capacity = value['facts'].get('potion_capacity')
            if (inventory['coverage']['potion'] != 'complete' or type(capacity) is not int
                    or len(inventory['current']['potion']) >= capacity
                    or 'Sozu' in inventory['current']['relic']):
                return {'status': 'strategy_required', 'decision_key': key, 'options': options,
                        'controller_input_sent': False,
                        'instruction': 'Review potion capacity/Sozu and choose another purchase or handle the potion belt explicitly; do not discard automatically.'}
        after_inventory['current'][role].append(name)
        if role == 'card' and 'deck_size' in resources:
            resources['deck_size'] += 1
    elif role == 'remove_service':
        screen = 'selection'
    elif role == 'remove_card':
        name = selected.get('offer', {}).get('name')
        _require(inventory['coverage']['card'] == 'complete' and name in inventory['current']['card'],
                 'removal requires the observed selected card in the deck')
        after_inventory['current']['card'].remove(name)
        if 'deck_size' in resources:
            resources['deck_size'] -= 1
    else:
        raise ValueError('inspect merchant option semantics')
    # _ui is normalized; keep only its permitted source-free fields.
    ui.pop('control_layout', None)
    draft = {'schema': 'veda.menu-draft.v1', 'decision_policy': 'learning',
             **{k: value[k] for k in ('context', 'inventory', 'resources', 'facts')}, 'ui': ui,
             'choice': {'kind': 'selection' if ui['menu_family'] == 'shop_remove' else 'shop',
                        'option_ids': [selected['id']], 'postconditions': {
                            'screen': screen, 'phase': phase, 'resources': resources,
                            'inventory': 'unchanged' if after_inventory == inventory else after_inventory,
                            'facts': deepcopy(value['facts']), 'allow_changed_facts': []}}, 'reasoning': reason}
    validate_menu_draft(draft, CONTROL_PROFILE)
    return {'status': 'planned', 'draft': draft, 'controller_input_sent': False,
            'decision': {'option_id': selected['id'], 'reason': reason, 'decision_key': key}}
