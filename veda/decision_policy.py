"""Pure distinction between game advice and permission to attempt one UI action.

Learning policy never fills unknown state or converts a missing forecast to zero.
Source freshness, session ownership and UI mapping remain the caller's checks.
The existing strategic checker is retained, unchanged, as the assessment evidence.
"""
from copy import deepcopy
import json

from .advisory import check_plan, current_cost, validate_snapshot
from .routine_combat import routine_observation_reasons
from .vision import explicit_nonattack_intent

POLICIES = frozenset({'strict', 'learning'})
_PLAN_FIELDS = {'steps', 'queue', 'potion_review', 'zero_cost_review', 'setup_reason', 'claims_lethal'}
_CARD_FIELDS = {'kind', 'card_id', 'target', 'return_card', 'return_card_id', 'exhaust_card_id', 'copy_card_id'}


def _text(value, maximum=4096):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value and len(value.encode()) <= maximum


def plan_shape_reasons(plan):
    """One current action plus a bounded, verified-after-each-step queue."""
    if not isinstance(plan, dict) or set(plan) - _PLAN_FIELDS:
        return ['plan requires one action and only supported advisory fields']
    steps = plan.get('steps')
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        return ['exactly one card or End Turn is required']
    def action_reason(action):
        if not isinstance(action, dict) or action.get('kind') not in {'card', 'end_turn'}:
            return 'only card or End Turn actions are supported'
        if action['kind'] == 'end_turn' and set(action) != {'kind'}:
            return 'End Turn cannot carry card, target or controller fields'
        if action['kind'] == 'card' and (set(action) - _CARD_FIELDS or not _text(action.get('card_id'), 256)
                or any(not _text(v, 256) for k, v in action.items() if k != 'kind' and v is not None)):
            return 'card action needs an observed card ID and bounded named selections'
        return None
    reason = action_reason(steps[0])
    if reason:
        return [reason]
    queue = plan.get('queue', [])
    if not isinstance(queue, list) or len(queue) > 8:
        return ['queue must contain at most eight future actions']
    for action in queue:
        reason = action_reason(action)
        if reason:
            return ['queue: ' + reason]
    for field in ('potion_review', 'zero_cost_review'):
        if field in plan and (not isinstance(plan[field], dict) or len(plan[field]) > 32
                or any(not _text(k, 256) or not _text(v) for k, v in plan[field].items())):
            return [field + ' must be a bounded map of names/IDs to review explanations']
    if 'setup_reason' in plan and not _text(plan['setup_reason']):
        return ['setup_reason must be a bounded explanation']
    if 'claims_lethal' in plan and type(plan['claims_lethal']) is not bool:
        return ['claims_lethal must be a boolean']
    return []


def _shape_reasons(context):
    """Check types and identities without turning absent knowledge into facts."""
    try:
        if not isinstance(context, dict) or not isinstance(context.get('state'), dict):
            return ['combat context needs a structured state']
        # Bound and reject non-JSON values even for direct callers of this API.
        if len(json.dumps(context, allow_nan=False).encode()) > 256000:
            return ['combat context exceeds the byte bound']
        state = context['state']
        if not isinstance(state.get('hand'), list) or not isinstance(state.get('enemies'), list):
            return ['hand and enemies must be explicit lists']
        for item in state['hand'] + state['enemies']:
            if not isinstance(item, dict) or not _text(item.get('name'), 256):
                return ['observed cards and enemies need bounded names']
        if any(not _text(c.get('id'), 256) for c in state['hand']):
            return ['observed hand cards need distinct bounded IDs']
        if any(not _text(e.get('id', e['name']), 256) for e in state['enemies']):
            return ['observed enemies need distinct bounded target IDs']
        for card in state['hand']:
            if card.get('playable') is not None and type(card['playable']) is not bool:
                return ['card playability must be boolean or null']
            if card.get('upgraded') is not None and type(card['upgraded']) is not bool:
                return ['card upgrade must be boolean or null']
        for field in ('powers', 'counters', 'piles'):
            if field in state and not isinstance(state[field], dict):
                return [field + ' must be a structured object']
        for field in ('hand_complete', 'powers_complete'):
            if state.get(field) is not None and type(state[field]) is not bool:
                return [field + ' must be boolean or null']
        for field in ('unmodeled_effects',):
            if state.get(field) is not None and (not isinstance(state[field], list)
                    or any(not _text(v) for v in state[field])):
                return [field + ' must be explicit descriptions or null']
        if not isinstance(context.get('unknowns', []), list) or any(not _text(v) for v in context.get('unknowns', [])):
            return ['unknowns must be explicit bounded descriptions']
        inventory = context.get('inventory', {})
        if not isinstance(inventory, dict) or not isinstance(inventory.get('current', {}), dict) or not isinstance(inventory.get('coverage', {}), dict):
            return ['inventory must contain structured current items and coverage']
        if any(not isinstance(v, list) or any(not _text(n, 256) for n in v) for v in inventory.get('current', {}).values()):
            return ['inventory categories must contain named item lists']
        if any(v not in {'unknown', 'partial', 'complete'} for v in inventory.get('coverage', {}).values()):
            return ['inventory coverage must be unknown, partial or complete']
        validate_snapshot(state)
        if any(explicit_nonattack_intent(e.get('intent')) and e.get('intent_hits') not in (None, [])
               for e in state['enemies']):
            return ['observed nonattack intent contradicts displayed attack hits']
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
        return ['invalid combat state: ' + str(error)]
    return []


def _visual_conflicts(visual, context):
    """Unknown values may stay unknown; two explicit representations cannot differ."""
    state = context['state']; reasons = []
    if visual.screen_type != 'COMBAT':
        reasons.append('reviewed screen is not combat')
    for shown, key in (('hp', 'hp'), ('max_hp', 'max_hp'), ('energy', 'energy'), ('block', 'block'),
            ('player_strength', 'strength'), ('player_weak', 'weak'), ('player_vulnerable', 'vulnerable'),
            ('player_frail', 'frail'), ('end_turn_damage', 'end_turn_damage'), ('ascension', 'ascension'),
            ('act', 'act'), ('floor', 'floor'), ('gold', 'gold'), ('character', 'character')):
        actual = getattr(visual, shown)
        if key in state and actual != state.get(key):
            reasons.append('observation and advisory context disagree on ' + key)
    if tuple(c['name'] for c in state['hand']) != visual.hand:
        reasons.append('ordered observed hand disagrees with the advisory hand')
    if visual.hand_details:
        if len(visual.hand_details) != len(state['hand']):
            reasons.append('observed hand detail count disagrees')
        else:
            for shown, card in zip(visual.hand_details, state['hand']):
                if not isinstance(shown, dict) or any(shown.get(a) != card.get(b) for a, b in (
                        ('name', 'name'), ('title_color', 'title_color'), ('upgraded', 'upgraded'), ('current_cost', 'cost'))):
                    reasons.append('observed card detail disagrees with the advisory hand')
    if len(visual.enemies) != len(state['enemies']):
        reasons.append('enemy count disagrees with the current observation')
    else:
        for shown, enemy in zip(visual.enemies, state['enemies']):
            if any(getattr(shown, key) != enemy.get(key) for key in ('name', 'hp', 'max_hp', 'block', 'intent')):
                reasons.append('enemy identity, resources or intent disagree with the observation')
            hits = enemy.get('intent_hits')
            if shown.intent_hits != (None if hits is None else tuple(hits)) or shown.intent_total_damage != (None if hits is None else sum(hits)):
                reasons.append('observed enemy intent hits contradict the advisory state')
    return reasons


def assess_observation(visual, context, policy='strict'):
    """Completeness/confidence are advice in learning; contradictions remain errors."""
    if not isinstance(policy, str) or policy not in POLICIES:
        return {'allowed': False, 'hard_reasons': ['unknown decision policy'], 'warnings': [], 'policy': policy}
    hard = _shape_reasons(context)
    if not hard and context.get('fresh') is not True:
        hard.append('capture and record a fresh combat snapshot')
    if hard:
        return {'allowed': False, 'hard_reasons': hard, 'warnings': [], 'policy': policy}
    reasons = list(routine_observation_reasons(visual, context)) if visual is not None else []
    if policy == 'strict':
        hard.extend(reasons); warnings = []
    else:
        if visual is not None:
            hard.extend(_visual_conflicts(visual, context))
        warnings = [reason for reason in reasons if reason not in hard]
    return {'allowed': not hard, 'hard_reasons': list(dict.fromkeys(hard)),
            'warnings': list(dict.fromkeys(warnings)), 'policy': policy}


def _known_action_reasons(context, action):
    state = context['state']; reasons = []
    if type(state.get('hp')) is int and state['hp'] <= 0:
        reasons.append('the observed player has no HP; no combat action is legal')
    if action['kind'] == 'end_turn':
        return reasons
    card = next((c for c in state['hand'] if c['id'] == action['card_id']), None)
    if card is None:
        return reasons + ['card is absent from the observed hand']
    if card.get('playable') is False or card.get('unplayable') is True:
        reasons.append(card['name'] + ' is observed unplayable')
    cost = current_cost(card, state.get('powers', {}))
    # A shown numerical cost still constrains legality when the card type is unread.
    if cost is None:
        cost = card.get('cost')
    if type(cost) is int and type(state.get('energy')) is int and cost > state['energy']:
        reasons.append(card['name'] + ' costs more than the observed energy')
    if 'Velvet Choker' in context.get('inventory', {}).get('current', {}).get('relic', []):
        counter = state.get('counters', {}).get('velvet_choker')
        if type(counter) is int and counter >= 6:
            reasons.append('Velvet Choker prevents another card')
    if card.get('type') == 'Attack' or action.get('target') is not None:
        target = next((e for e in state['enemies'] if e.get('id', e['name']) == action.get('target')), None)
        if target is None or target.get('hp') == 0:
            reasons.append('card needs an observed target that is not defeated')
    return reasons


def candidate_action_summary(context, chosen=None):
    """Build a cheap, source-bound ledger of legal alternatives.

    This is deliberately a legality summary, not a guessed outcome simulator.
    It lets learning compare the chosen line with actions that were visible but
    not taken without adding another model round or controller interaction.
    """
    state = context.get('state', {}) if isinstance(context, dict) else {}
    enemies = [enemy for enemy in state.get('enemies', [])
               if isinstance(enemy, dict) and enemy.get('hp') not in (0, None)]
    target = enemies[0].get('id', enemies[0].get('name')) if enemies else None
    chosen_key = (chosen or {}).get('card_id') if isinstance(chosen, dict) else None
    candidates = []
    for card in state.get('hand', []):
        if not isinstance(card, dict) or not card.get('id'):
            continue
        action = {'kind': 'card', 'card_id': card['id']}
        if card.get('type') == 'Attack' and target is not None:
            action['target'] = target
        reasons = _known_action_reasons(context, action)
        candidates.append({'action': action, 'legal': not reasons, 'reasons': reasons,
                           'chosen': card['id'] == chosen_key})
    end_turn = {'kind': 'end_turn'}
    end_reasons = _known_action_reasons(context, end_turn)
    candidates.append({'action': end_turn, 'legal': not end_reasons, 'reasons': end_reasons,
                       'chosen': isinstance(chosen, dict) and chosen.get('kind') == 'end_turn'})
    return candidates


def assess_combat(context, plan, policy='strict', visual=None):
    """Assess one declared action; this result does not authorize controller input.

    ``checked`` is the original advisory result. Unknown forecasts remain None.
    Learning warnings never alter the original state, effects or advice record.
    """
    observed = assess_observation(visual, context, policy)
    hard = observed['hard_reasons'] + plan_shape_reasons(plan)
    checked = {'allowed': False, 'reasons': list(hard), 'notes': [], 'steps': [], 'forecast': None}
    if not _shape_reasons(context) and not plan_shape_reasons(plan) and isinstance(policy, str) and policy in POLICIES:
        try:
            checked = check_plan(context, plan, survival_scope='action_prefix')
        except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as error:
            # Incomplete knowledge can exceed the old checker's arithmetic support.
            # No successful checked effect or forecast is synthesized from that failure.
            checked = {'allowed': False, 'reasons': ['advisory forecast unavailable: ' + str(error)],
                       'notes': [], 'steps': [], 'forecast': None}
        if policy == 'learning':
            hard.extend(_known_action_reasons(context, plan['steps'][0]))
        else:
            hard.extend(checked['reasons'])
    hard = list(dict.fromkeys(hard))
    warnings = list(observed['warnings'])
    if policy == 'learning':
        warnings.extend(r for r in checked['reasons'] if r not in hard)
        warnings.extend(checked.get('notes', []))
        if checked.get('forecast') is None:
            warnings.append('damage, Block and survival forecast is unknown; observe the actual result before another action')
    warnings = list(dict.fromkeys(warnings))
    alternatives = candidate_action_summary(context, plan['steps'][0]) if isinstance(context, dict) else []
    return {**deepcopy(checked), 'allowed': not hard, 'reasons': hard, 'hard_reasons': hard,
        'warnings': warnings, 'checked': deepcopy(checked), 'policy': policy,
        'forecast_status': 'known' if checked.get('forecast') is not None else 'unknown',
        'decision_under_uncertainty': policy == 'learning' and bool(warnings),
        'candidate_actions': alternatives}
