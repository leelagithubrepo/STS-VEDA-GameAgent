"""Deterministic routine loot choices; card quality remains one strategic choice.

Only consumes reviewed facts. No model, capture, database or controller calls.
The returned draft uses the same single-input/result-verification adapter.
"""
from copy import deepcopy
import hashlib
import json
from .menu_requests import _inventory, _ui, _text, validate_menu_draft
from .menu_controls import CONTROL_PROFILE


def decision_key(snapshot):
    # Focus moves do not change the choice. Resources, inventory, available
    # rewards or run/floor changes do, so a strategic choice cannot leak across them.
    ui = snapshot['ui']
    value = {k: snapshot[k] for k in ('context', 'inventory', 'resources', 'facts')}
    value['ui'] = {k: ui[k] for k in ('menu_family', 'choice_id', 'options')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def plan_loot(snapshot, decision=None):
    required = {'schema', 'context', 'inventory', 'resources', 'facts', 'ui'}
    if not isinstance(snapshot, dict) or set(snapshot) != required or snapshot['schema'] != 'veda.loot-snapshot.v1':
        raise ValueError('reviewed veda.loot-snapshot.v1 required')
    value = deepcopy(snapshot)
    inventory = _inventory(value['inventory'], 'learning')
    ui = _ui(value['ui'])
    if ui['menu_family'] not in {'loot_rewards', 'loot_cards'} or value['facts'].get('reward_source') != 'combat':
        raise ValueError('routine loot applies only to reviewed combat reward screens')
    options = [o for o in ui['options'] if o['enabled']]
    key = decision_key(value)
    selected, reason, routine = None, None, True
    if decision is not None:
        if (not isinstance(decision, dict) or set(decision) != {'option_id', 'reason', 'decision_key'}
                or decision['decision_key'] != key or not _text(decision['reason'])):
            raise ValueError('loot decision changed; assess this reward set once before reusing a choice')
        selected = next((o for o in options if o['id'] == decision['option_id']), None)
        if selected is None:
            raise ValueError('chosen loot is no longer available')
        reason, routine = decision['reason'], False
    elif ui['menu_family'] == 'loot_rewards':
        free = [o for o in options if o['costs'] == {}]
        selected = next((o for o in free if o.get('role') == 'gold'), None)
        if selected:
            reason = 'Collect available gold.'
        else:
            # A full/unknown inventory or Sozu needs a strategic decision, not a discard.
            capacity = value['facts'].get('potion_capacity')
            if (type(capacity) is int and capacity > len(inventory['current']['potion'])
                    and inventory['coverage']['potion'] == 'complete'
                    and inventory['coverage']['relic'] == 'complete'
                    and 'Sozu' not in inventory['current']['relic']):
                selected = next((o for o in free if o.get('role') == 'potion'), None)
                if selected:
                    reason = 'Collect the potion into an empty slot.'
            if selected is None:
                selected = next((o for o in free if o.get('role') == 'card_reward'), None)
                if selected:
                    reason = 'Open the card offers; choose or skip after inspecting them.'
            if selected is None and len(options) == 1 and options[0].get('role') == 'proceed' and options[0]['costs'] == {}:
                selected, reason = options[0], 'Rewards handled; continue.'
    if selected is None:
        return {'status': 'strategy_required', 'decision_key': key, 'options': options,
                'instruction': ('Choose one card or skip once; reuse that decision while navigating.'
                    if ui['menu_family'] == 'loot_cards' else
                    'Review the remaining reward tradeoffs and choose an available option once; for a full potion belt, decide replacement or leave.'),
                'controller_input_sent': False}
    if selected['costs'] != {}:
        raise ValueError('paid or tradeoff rewards use the ordinary strategic menu flow')
    role = selected.get('role')
    reward = selected.get('reward', {})
    post_inventory, resources = deepcopy(inventory), deepcopy(value['resources'])
    screen = ui['screen']
    if role == 'gold' and ui['menu_family'] == 'loot_rewards':
        amount = reward.get('amount')
        if type(amount) is not int or amount <= 0 or type(resources.get('gold')) is not int:
            raise ValueError('gold collection needs the observed amount and current total')
        resources['gold'] += amount
    elif role in {'potion', 'relic', 'card'}:
        if not _text(reward.get('name'), 256):
            raise ValueError('item reward requires its observed name')
        if role == 'card' and ui['menu_family'] != 'loot_cards':
            raise ValueError('inspect the actual card offers before selecting a card')
        if role == 'potion':
            capacity = value['facts'].get('potion_capacity')
            if (type(capacity) is not int or inventory['coverage']['potion'] != 'complete'
                    or len(inventory['current']['potion']) >= capacity):
                return {'status': 'strategy_required', 'decision_key': key, 'options': options,
                        'instruction': 'Inspect the potion belt and decide replacement or leave; never discard automatically.',
                        'controller_input_sent': False}
        post_inventory['current'][role].append(reward['name'])
        if role == 'card':
            screen = 'reward'
    elif role == 'card_reward' and ui['menu_family'] == 'loot_rewards':
        screen = 'card_reward'
    elif role == 'skip' and ui['menu_family'] == 'loot_cards':
        screen = 'reward'
    elif role == 'proceed' and ui['menu_family'] == 'loot_rewards':
        screen = 'map'
    else:
        raise ValueError('reward semantics need strategic review')
    post = {'screen': screen, 'phase': 'choose', 'resources': resources,
            'inventory': 'unchanged' if post_inventory == inventory else post_inventory,
            'facts': deepcopy(value['facts']), 'allow_changed_facts': []}
    draft = {'schema': 'veda.menu-draft.v1', 'decision_policy': 'learning',
             **{k: value[k] for k in ('context', 'inventory', 'resources', 'facts', 'ui')},
             'choice': {'kind': 'reward', 'option_ids': [selected['id']], 'postconditions': post},
             'reasoning': reason}
    validate_menu_draft(draft, CONTROL_PROFILE)
    return {'status': 'planned', 'routine': routine, 'draft': draft,
            'decision': {'option_id': selected['id'], 'reason': reason, 'decision_key': key},
            'controller_input_sent': False}
