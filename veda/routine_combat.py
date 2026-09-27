"""Bounded local planning over the same evidence and rules as checked advice.

Only the first action is executable. Lookahead is a ranking aid, never permission
to replay controller inputs without fresh observation after each input.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

from .advisory import (DIRECT, REVIEWED_SPECIAL_CARDS, SPIKE_SLIMES,
                       _reviewed_spike_intent, _verified_costless_status,
                       current_run_relic_reasons, metallicize_block, reviewed_collector_attack_roster,
                       check_plan, current_cost, reviewed_card_type, validate_snapshot)
from .calibration import COMBAT_CRITICAL_FIELDS, CalibrationReport
from .combat import CardEffect, CombatEnemy, SequenceCheck, _scaled
from .combat_state import verify_combat_state
from .encounters import check_encounter
from .vision import StructuredGameState


ROUTINE_CRITICAL_FIELDS = COMBAT_CRITICAL_FIELDS | frozenset({
    "hand_complete", "hand_details", "player_strength", "player_weak", "player_frail",
})
# These have no card-resolution trigger except Puzzle on HP loss. HP-loss cards
# already terminate at an observation boundary; their draw is never predicted.
# Enemy-turn benefits (e.g. Bronze Scales) are omitted from conservative ranking.
_PASSIVE_RELICS = frozenset({
    "Burning Blood", "Black Blood", "Neow's Lament", "Anchor", "Centennial Puzzle",
    "Juzu Bracelet", "Bronze Scales", "Bag of Preparation", "Vajra",
    "Oddly Smooth Stone", "Toy Ornithopter", "Eternal Feather", "Pantograph",
    "Shovel", "White Beast Statue", "Astrolabe", "Golden Idol", "Bloody Idol",
})


@dataclass(frozen=True)
class RoutinePlan:
    ready: bool
    reasons: tuple[str, ...]
    sequence: tuple[CardEffect, ...] = ()
    prediction: SequenceCheck | None = None
    next_action: dict[str, Any] | None = None
    checked_result: dict[str, Any] | None = None
    evaluated_candidates: int = 0
    search_truncated: bool = False
    support_notes: tuple[str, ...] = ()
    supported_card_count: int = 0
    deferred_card_count: int = 0

    @property
    def instructions(self) -> tuple[str, ...]:
        if self.next_action and self.next_action['kind'] == 'end_turn':
            return ('End Turn',)
        return tuple(f"Play {card.name}" + (f" targeting {card.target}" if card.target else "") for card in self.sequence)


def _context_reasons(observation: StructuredGameState, context: dict, *,
                     require_supported_effects: bool = True) -> list[str]:
    state = context.get('state') or {}
    reasons: list[str] = []
    try:
        validate_snapshot(state)
    except (KeyError, TypeError, ValueError) as error:
        return [f"invalid advisory context: {error}"]
    if not context.get('fresh') or context.get('unknowns'):
        reasons.append('fresh checked advisory context is required')
    for visual, field in (('hp', 'hp'), ('max_hp', 'max_hp'), ('energy', 'energy'),
                          ('block', 'block'), ('player_strength', 'strength'),
                          ('player_weak', 'weak'), ('player_vulnerable', 'vulnerable'),
                          ('player_frail', 'frail'), ('end_turn_damage', 'end_turn_damage')):
        if getattr(observation, visual) is None or getattr(observation, visual) != state.get(field):
            reasons.append(f'observation and advisory context disagree on {field}')
    if observation.ascension != state.get('ascension'):
        reasons.append('observation and advisory context disagree on Ascension')
    hand = state.get('hand', [])
    if observation.hand_complete is not True or state.get('hand_complete') is not True:
        reasons.append('the complete current hand is unconfirmed')
    if (tuple(c['name'] for c in hand) != observation.hand
            or len(observation.hand_details) != len(hand)):
        reasons.append('ordered hand details do not match the confirmed hand')
    else:
        for observed, card in zip(observation.hand_details, hand):
            if not isinstance(observed, dict):
                reasons.append('a hand detail is unread')
                continue
            if any(observed.get(key) != card.get(key) or observed.get(key) is None
                   for key in ('name', 'upgraded', 'title_color')):
                reasons.append(f"{card['name']}: title, color or upgrade evidence disagrees")
            if (type(card.get('upgraded')) is not bool
                    or card['name'].endswith('+') != card['upgraded']
                    or card.get('title_color') not in ('white', 'green', 'teal')
                    or (card['title_color'] != 'white') != card['upgraded']):
                reasons.append(f"{card['name']}: verify title color and upgrade state")
            cost = observed.get('current_cost')
            if (cost is None and not _verified_costless_status(card)) or cost != card.get('cost'):
                reasons.append(f"{card['name']}: current cost is unread or inconsistent")
            if type(card.get('playable')) is not bool or card.get('type') is None:
                reasons.append(f"{card['name']}: playability or card type is unread")
    enemies = state.get('enemies', [])
    if len(enemies) != len(observation.enemies):
        reasons.append('enemy count disagrees with the current observation')
    else:
        for visible, enemy in zip(observation.enemies, enemies):
            if any(getattr(visible, key) != enemy.get(key) for key in ('name', 'hp', 'max_hp', 'block', 'intent')):
                reasons.append('enemy identity, resources or intent disagree with the observation')
            if (visible.intent_hits is None or enemy.get('intent_hits') is None
                    or tuple(enemy['intent_hits']) != visible.intent_hits
                    or sum(visible.intent_hits) != visible.intent_total_damage):
                reasons.append('enemy intent hits disagree or are unread')
            if any(enemy.get(k) is None for k in ('vulnerable', 'artifact')):
                reasons.append('enemy Vulnerable or Artifact is unread')
    if state.get('powers_complete') is not True or not isinstance(state.get('powers'), dict):
        reasons.append('active powers are unconfirmed')
    elif require_supported_effects and any(v for k, v in state['powers'].items() if k != 'Metallicize'):
        reasons.append('active power interactions require a separately supported planner')
    if require_supported_effects and state.get('unmodeled_effects') != []:
        reasons.append('unmodeled combat effects require escalation')
    if require_supported_effects and state.get('end_turn_damage') != 0:
        reasons.append('end-of-turn damage is outside the routine forecast')
    for field in ('dexterity', 'no_block'):
        if state.get(field) is None:
            reasons.append(f'{field} is unread')
    inventory = context.get('inventory', {})
    for kind in ('relic', 'potion'):
        if inventory.get('coverage', {}).get(kind) != 'complete':
            reasons.append(f'confirmed {kind} inventory is incomplete')
    relics = inventory.get('current', {}).get('relic', [])
    reasons.extend(current_run_relic_reasons(state, relics, inventory.get('current', {}).get('potion', [])))
    if metallicize_block(state) is None:
        reasons.append('Metallicize intensity is unknown or invalid')
    unsupported = sorted(set(relics) - _PASSIVE_RELICS - {'Shuriken', 'Red Mask', 'Potion Belt'})
    if require_supported_effects and unsupported:
        reasons.append('unsupported relic interactions: ' + ', '.join(unsupported))
    return reasons


def routine_observation_reasons(observation: StructuredGameState, context: dict | None, *,
                                require_supported_effects: bool = False) -> tuple[str, ...]:
    """Validate a settled frame against its context without proposing any action.

    The runtime uses this after every input, including focus/selection changes.
    Newly observed unsupported effects can still be reconciled; planning then
    requires the stricter effect-coverage checks before another game action.
    """
    reasons = list(verify_combat_state(observation).reasons)
    if context is None:
        reasons.append('verified advisory context with current costs, powers and inventory is required')
    else:
        reasons.extend(_context_reasons(observation, context,
                                        require_supported_effects=require_supported_effects))
    return tuple(dict.fromkeys(reasons))


def _encounter_reasons(state: StructuredGameState, context: dict, name: str | None) -> list[str]:
    enemies = context['state']['enemies']
    # Only a matching reviewed typed manifest can bypass the small old profile
    # list. A caller's claim that an arbitrary encounter is reviewed is not enough.
    if enemies and all(e['name'] in SPIKE_SLIMES and _reviewed_spike_intent(context['state'], e) for e in enemies):
        return []
    if name == 'The Collector' and reviewed_collector_attack_roster(context['state']):
        return []
    profile = check_encounter(name, act=state.act)
    if not profile.known:
        return list(profile.cautions)
    if profile.profile and profile.profile.kind != 'enemy':
        return [f'{profile.profile.name} requires its encounter-specific reactive-effect planner']
    if len(enemies) != 1 or enemies[0]['name'] != name:
        return ['encounter profile does not match every observed enemy']
    if profile.profile and profile.profile.split_at_half_hp:
        return ['Split needs a matching reviewed typed intent before routine planning']
    if any(e.get('intent_effects') for e in enemies):
        return ['enemy intent effects need a matching reviewed manifest']
    return []


def _effect_for(card: dict, state: dict, target: str | None) -> CardEffect:
    """Adapter only: base values and reviewed variants live in advisory.py."""
    name = card['name']
    special = REVIEWED_SPECIAL_CARDS.get(name, {})
    damage, block = DIRECT.get(name, (special.get('base_damage', 0), special.get('base_block', 0)))
    if damage is None:
        damage = state['block']  # Only the first, freshly observed action is emitted.
    damage = max(0, damage + state['strength']) if card['type'] == 'Attack' else 0
    block = max(0, block + state['dexterity']) if block and not state['no_block'] else 0
    return CardEffect(name, current_cost(card, state['powers']), card['type'],
                      attack_damage=damage, block=block, weak=special.get('weak', 0),
                      vulnerable=special.get('vulnerable', 0), target=target)


def _prediction(checked: dict, state: dict) -> SequenceCheck | None:
    forecast = checked.get('forecast')
    if forecast is None:
        return None
    energy = checked['steps'][-1]['energy_after']
    return SequenceCheck(checked['allowed'], tuple(checked['reasons']), state['energy'] - energy,
        energy, forecast['block'], tuple(CombatEnemy(e.get('id', e['name']), e['hp'], e['block'],
        e.get('artifact', 0), e['vulnerable']) for e in forecast['enemies']),
        forecast['player_hp'], forecast['incoming_displayed'], 0, forecast['player_hp'] <= 0)


def _boundary_rank(state: dict, card: dict, action: dict) -> tuple | None:
    """Conservative ranking only, not a post-effect or enemy-turn prediction.

    Draws, generated/exhausted cards and debuffs cannot extend this action. Their
    possible benefit is ignored. Immediate HP loss is never blocked by Block.
    """
    spec = REVIEWED_SPECIAL_CARDS.get(card['name'])
    if card['name'] in DIRECT:
        damage, gained = DIRECT[card['name']]
        spec = {'base_damage': state['block'] if damage is None else damage, 'base_block': gained}
    if spec is None:
        return None
    hp = state['hp'] - spec.get('hp_loss', 0)
    gained = spec.get('base_block', 0)
    gained = max(0, gained + state['dexterity']) if gained and not state['no_block'] else 0
    gained = gained * 3 // 4 if state['frail'] else gained
    block = state['block'] + gained + (metallicize_block(state) or 0)
    remaining = 0
    for enemy in state['enemies']:
        damage = 0
        if enemy.get('id', enemy['name']) == action.get('target') and 'base_damage' in spec:
            damage = _scaled(spec['base_damage'] + state['strength'],
                             weak=bool(state['weak']), vulnerable=bool(enemy['vulnerable']))
        remaining += max(0, enemy['hp'] - max(0, damage - enemy['block']))
    incoming = sum(sum(e['intent_hits']) for e in state['enemies']) if remaining else 0
    hp -= max(0, incoming - block)
    if hp <= 0:
        return None
    return remaining, -hp, current_cost(card, state['powers']), 1


def plan_routine_combat(state: StructuredGameState, calibration: CalibrationReport, *,
                        encounter_name: str | None, context: dict | None = None,
                        max_candidates: int = 256, max_depth: int = 6) -> RoutinePlan:
    """Return one checked action, or explicit support/evidence gaps.

    ``context`` is the fresh full advisory context for this exact frame. The
    legacy call remains valid but cannot authorize play without those fields.
    Search is deterministic and bounded by both candidate count and hand depth.
    """
    reasons: list[str] = []
    if not calibration.authorized_for(ROUTINE_CRITICAL_FIELDS):
        reasons.append('local vision has not earned combat-planning authorization')
    reasons.extend(routine_observation_reasons(state, context, require_supported_effects=True))
    if context is not None and not reasons:
        reasons.extend(_encounter_reasons(state, context, encounter_name))
    if type(max_candidates) is not int or not 1 <= max_candidates <= 4096 or type(max_depth) is not int or not 1 <= max_depth <= 10:
        reasons.append('search limits require 1–4096 candidates and depth 1–10')
    if reasons:
        return RoutinePlan(False, tuple(dict.fromkeys(reasons)))
    current = context['state']
    hand = {c['id']: c for c in current['hand']}
    actions: list[dict] = []
    deferred: Counter[str] = Counter()
    supported = 0
    for card in current['hand']:
        name = card['name']
        if card.get('playable') is not True:
            if not _verified_costless_status(card):
                deferred[f'{name}: not confirmed playable'] += 1
            continue
        if reviewed_card_type(name) != card['type'] or (name not in DIRECT and name not in REVIEWED_SPECIAL_CARDS):
            deferred[f'{name}: no reviewed numeric immediate effect; draw/exhaust/choice effects require escalation'] += 1
            continue
        if name == 'Headbutt' and current.get('piles', {}).get('discard') != []:
            deferred['Headbutt: confirmed discard-return choice is required'] += 1
            continue
        supported += 1
        targets = [e.get('id', e['name']) for e in current['enemies'] if e['hp'] > 0] if card['type'] == 'Attack' or name == 'Spot Weakness' else [None]
        for target in targets:
            action = {'kind': 'card', 'card_id': card['id']}
            if target is not None:
                action['target'] = target
            actions.append(action)
    notes = tuple(f'{reason} ({count} card(s))' for reason, count in sorted(deferred.items()))
    # Ending a turn is a decision under exactly the same evidence guards,
    # not a runtime fallback around failed planning. An unsupported but
    # affordable card still requires escalation rather than silent discard.
    affordable = [card for card in hand.values() if card.get('playable') is True
                  and current_cost(card, current['powers']) <= current['energy']]
    if not affordable:
        action = {'kind': 'end_turn'}
        checked = check_plan(context, {'steps': [action]})
        if checked['allowed'] and checked.get('forecast') is not None:
            return RoutinePlan(True, (), (), _prediction(checked, current), action, checked,
                               1, False, notes, supported, sum(deferred.values()))
        return RoutinePlan(False, tuple(checked['reasons']), evaluated_candidates=1,
                           support_notes=notes, supported_card_count=supported,
                           deferred_card_count=sum(deferred.values()))
    queue = deque((action,) for action in actions)
    evaluated = 0
    best = None
    first_checks: dict[str, dict] = {}
    rejection_reasons: list[str] = []
    while queue and evaluated < max_candidates:
        line = queue.popleft()
        checked = check_plan(context, {'steps': list(line)})
        evaluated += 1
        key = repr(line[0])
        if len(line) == 1:
            first_checks[key] = checked
        if not checked['allowed']:
            rejection_reasons.extend(checked['reasons'])
            continue
        forecast = checked.get('forecast')
        rank = ((sum(e['hp'] for e in forecast['enemies']), -forecast['player_hp'],
                 current['energy'] - checked['steps'][-1]['energy_after'], len(line)) if forecast else
                _boundary_rank(current, hand[line[0]['card_id']], line[0]) if len(line) == 1 else None)
        if rank is not None and (best is None or rank < best[0]):
            best = rank, line[0], first_checks[key]
        if (forecast and not forecast['lethal_to_enemies'] and len(line) < max_depth
                and not checked['steps'][-1]['observe_after']):
            used = {step['card_id'] for step in line}
            energy = checked['steps'][-1]['energy_after']
            # Only direct effects may extend a forecast. All special actions
            # are separately considered from the original fresh frame.
            queue.extend((*line, action) for action in actions
                         if action['card_id'] not in used and hand[action['card_id']]['name'] in DIRECT
                         and current_cost(hand[action['card_id']], current['powers']) <= energy)
    truncated = bool(queue)
    if truncated:
        notes += ('candidate budget reached; ranking is incomplete',)
    if best is None:
        reasons = tuple(dict.fromkeys(rejection_reasons)) or ('no affordable supported action has a verified survival bound',)
        return RoutinePlan(False, reasons, evaluated_candidates=evaluated, search_truncated=truncated,
                           support_notes=notes, supported_card_count=supported, deferred_card_count=sum(deferred.values()))
    _, action, checked = best
    effect = _effect_for(hand[action['card_id']], current, action.get('target'))
    if checked['steps'][-1]['observe_after']:
        notes += ('effect boundary: observe HP, hand, piles and intent before replanning',)
    return RoutinePlan(True, (), (effect,), _prediction(checked, current), dict(action), checked,
                       evaluated, truncated, notes, supported, sum(deferred.values()))
