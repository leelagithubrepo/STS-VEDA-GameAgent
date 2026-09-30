"""Deterministic routine loot choices; card quality remains one strategic choice.

Only consumes reviewed facts. No model, capture, database or controller calls.
The returned draft uses the same single-input/result-verification adapter.
"""
from copy import deepcopy
import hashlib
import json
import re
from .menu_requests import _inventory, _ui, _text, validate_menu_draft
from .menu_controls import CONTROL_PROFILE

SCHEMA = 'veda.loot-snapshot.v1'


def canonical_reward_role(option):
    """Infer routine reward semantics from reviewed labels when role metadata is absent."""
    if not isinstance(option, dict):
        return None
    role = option.get('role')
    if role in {'gold', 'potion', 'card_reward', 'card', 'skip', 'proceed', 'relic'}:
        return role
    text = str(option.get('id', '')).lower().replace('_', '-').strip()
    label = str(option.get('label', '')).lower()
    if text in {'gold', 'gold-reward'} or re.fullmatch(r'\d+\s+gold', label):
        return 'gold'
    if text in {'potion', 'potion-reward'}:
        return 'potion'
    if text in {'colorless-potion', 'colorless_potion'} or 'colorless potion' in label:
        return 'potion'
    if text in {'card-reward', 'cards', 'card'}:
        return 'card_reward' if text != 'card' else 'card'
    if text in {'skip', 'skip-rewards'}:
        return 'skip'
    if text in {'skip-potion', 'skip_potion'} or 'skip potion' in label:
        return 'skip'
    if text in {'proceed', 'continue'}:
        return 'proceed'
    return None


def resolve_snapshot(snapshot, session):
    from .shop import resolve_snapshot as resolve
    return resolve(snapshot, session)


def snapshot_from_result(packet, session):
    from .shop import snapshot_from_result as reuse
    value = reuse(packet, session)
    if value['ui'].get('menu_family') not in {'loot_rewards', 'loot_cards'}:
        raise ValueError('verified result is no longer a loot screen; use the observed next room')
    value['schema'] = SCHEMA
    return value


def decision_key(snapshot):
    # Focus moves do not change the choice. Resources, inventory, available
    # rewards or run/floor changes do, so a strategic choice cannot leak across them.
    ui = _ui(snapshot['ui'])
    value = {k: snapshot[k] for k in ('context', 'inventory', 'resources', 'facts')}
    value['ui'] = {k: ui[k] for k in ('menu_family', 'choice_id')}
    # Control hints rebind to each image; strategy depends on the actual offers.
    value['ui']['options'] = [{k: o[k] for k in ('id', 'label', 'enabled', 'costs', 'role', 'reward') if k in o}
                              for o in _normalized_options(ui['options'])]
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _normalized_options(options):
    result = []
    for option in options:
        normalized = deepcopy(option)
        normalized.setdefault('role', canonical_reward_role(normalized))
        result.append(normalized)
    return result


def plan_loot(snapshot, decision=None, *, fallback=False):
    required = {'schema', 'context', 'inventory', 'resources', 'facts', 'ui'}
    if not isinstance(snapshot, dict) or set(snapshot) != required or snapshot['schema'] != 'veda.loot-snapshot.v1':
        raise ValueError('reviewed veda.loot-snapshot.v1 required')
    value = deepcopy(snapshot)
    inventory = _inventory(value['inventory'], 'learning')
    ui = _ui(value['ui'])
    value['ui']['menu_family'] = ui['menu_family']
    if ui['menu_family'] not in {'loot_rewards', 'loot_cards'} or value['facts'].get('reward_source') != 'combat':
        raise ValueError('routine loot applies only to reviewed combat reward screens')
    normalized_options = _normalized_options(ui['options'])
    options = normalized_options
    options = [o for o in normalized_options if o['enabled']]
    # Carry the normalized role into the emitted draft as well as the local
    # planner view; downstream menu validation must see the same semantics.
    value['ui']['options'] = deepcopy(normalized_options)
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
    elif ui['menu_family'] == 'loot_cards' and ui['phase'] == 'confirm':
        selected = next((o for o in options if ui['selected_ids'] == ui['pending_ids'] == [o['id']]
                         and o.get('role') == 'card'), None)
        reason = 'Confirm the already selected card; retain the reward decision.'
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
            if selected is None and fallback:
                # A watchdog fallback must remain a legal, visible choice.  A
                # full belt cannot be changed safely without naming the exact
                # potion to discard, so skipping is the bounded least-risk
                # action when the strategic replacement pass has timed out.
                selected = next((o for o in free if o.get('role') == 'skip'), None)
                if selected:
                    reason = 'Watchdog fallback: skip the potion and continue after the replacement decision timed out.'
            if selected is None:
                selected = next((o for o in free if o.get('role') == 'card_reward'), None)
                if selected:
                    reason = 'Open the card offers; choose or skip after inspecting them.'
            if selected is None and len(options) == 1 and options[0].get('role') == 'proceed' and options[0]['costs'] == {}:
                selected, reason = options[0], 'Rewards handled; continue.'
    if selected is None:
        fallback_option = next((o for o in options if o.get('role') == 'skip'
                                and ui['menu_family'] == 'loot_rewards'), None)
        return {'status': 'strategy_required', 'decision_key': key, 'options': options,
                'fallback': ({'option_id': fallback_option['id'],
                              'reason': 'Skip the full-belt potion if the bounded replacement review expires.',
                              'decision_budget_seconds': 10}
                             if fallback_option is not None else None),
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
            if 'deck_size' in resources:
                resources['deck_size'] += 1
    elif role == 'card_reward' and ui['menu_family'] == 'loot_rewards':
        screen = 'card_reward'
    elif role == 'skip' and ui['menu_family'] in {'loot_cards', 'loot_rewards'}:
        screen = 'reward' if ui['menu_family'] == 'loot_cards' else 'map'
        if ui['menu_family'] == 'loot_rewards':
            screen = 'map'
    elif role == 'proceed' and ui['menu_family'] == 'loot_rewards':
        screen = 'map'
    else:
        raise ValueError('reward semantics need strategic review')
    post = {'screen': screen, 'phase': 'result' if screen == 'map' else 'choose', 'resources': resources,
            'inventory': 'unchanged' if post_inventory == inventory else post_inventory,
            'facts': deepcopy(value['facts']), 'allow_changed_facts': []}
    draft = {'schema': 'veda.menu-draft.v1', 'decision_policy': 'learning',
             **{k: value[k] for k in ('context', 'inventory', 'resources', 'facts', 'ui')},
             'choice': {'kind': 'reward', 'option_ids': [selected['id']], 'postconditions': post},
             'reasoning': reason}
    validate_menu_draft(draft, CONTROL_PROFILE)
    # This is deliberately only a routing annotation: the ordinary reviewed
    # request/result contract still owns controller delivery and confirmation.
    # It lets the player bypass a separate model pass for free, known rewards.
    fast_path = None
    if routine and role in {'gold', 'potion', 'card_reward', 'proceed', 'skip'}:
        fast_path = {
            'kind': 'routine_loot',
            'option_id': selected['id'],
            'focused': ui['focused_id'] == selected['id'],
            'next_step': ('commit' if selected.get('shortcut_hint') is not None
                          or ui['focused_id'] == selected['id'] else 'focus_then_commit'),
            'after_commit': 'inspect_one_settled_after_frame_and_continue_from_it',
            'decision_budget_seconds': 10,
        }
    return {'status': 'planned', 'routine': routine, 'draft': draft,
            'decision': {'option_id': selected['id'], 'reason': reason, 'decision_key': key},
            'fast_path': fast_path,
            'controller_input_sent': False}
