"""Spire's evidence checks for the next human action. Never operates the game.

This is a bounded preflight, not a complete combat simulator. Unsupported
interactions terminate a plan at an observation boundary; they cannot certify
lethal or survival. Screen intent damage is already modified damage.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parents[1] / 'data'
SCHEMA = 'spire.advisory.v1'


@lru_cache(maxsize=8)
def _read_rules(path: str, modified: int) -> dict:
    return json.loads(Path(path).read_text())


def rule_pack() -> dict:
    path = DATA / 'spire_advisory_rules.json'
    return _read_rules(str(path), path.stat().st_mtime_ns)


def relevant_rules(state: dict, inventory: dict, action_names: list[str] | None = None) -> dict:
    pack = rule_pack()
    tags = {'combat'}
    tags.update(card['name'].rstrip('+').casefold() for card in state.get('hand', []))
    tags.update(name.rstrip('+').casefold() for name in action_names or [])
    tags.update(name.casefold() for names in inventory.get('current', {}).values() for name in names)
    tags.update(enemy['name'].casefold() for enemy in state.get('enemies', []))
    tags.update(name.casefold() for name, active in state.get('powers', {}).items() if active)
    return {'version': pack['version'], 'reviewed_at': pack['reviewed_at'],
            'rules': [r for r in pack['rules'] if tags.intersection(r['tags'])]}


def validate_snapshot(state: dict) -> None:
    """Validate shape, preserving unread values as null rather than defaults."""
    if state.get('schema') != SCHEMA:
        raise ValueError(f'snapshot schema must be {SCHEMA}')
    observed = datetime.fromisoformat(state['observed_at'])
    if observed.tzinfo is None or observed > datetime.now(timezone.utc):
        raise ValueError('observation time must be timezone-aware and not in the future')
    for key in ('hp', 'max_hp', 'energy', 'block', 'weak', 'vulnerable', 'frail', 'no_block'):
        value = state.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'{key} must be a nonnegative integer or null')
    for key in ('strength', 'dexterity'):
        if state.get(key) is not None and type(state[key]) is not int:
            raise ValueError(f'{key} must be an integer or null')
    if state.get('hp') is not None and state.get('max_hp') is not None and state['hp'] > state['max_hp']:
        raise ValueError('HP exceeds maximum HP')
    hand = state.get('hand')
    if not isinstance(hand, list) or len(hand) > 10:
        raise ValueError('hand must contain at most ten individually identified cards')
    ids = set()
    for card in hand:
        if not isinstance(card, dict) or not card.get('id') or not card.get('name') or card['id'] in ids:
            raise ValueError('each hand card needs a unique id and observed name')
        ids.add(card['id'])
        if card.get('type') not in ('Attack', 'Skill', 'Power', 'Status', 'Curse', None):
            raise ValueError('invalid card type')
        if card.get('cost') is not None and (type(card['cost']) is not int or card['cost'] < 0):
            raise ValueError('card cost must be observed nonnegative integer or null')
        if card.get('upgraded') not in (True, False, None):
            raise ValueError('card upgrade must be true, false, or null')
    piles = state.get('piles', {})
    for zone in ('draw', 'discard', 'exhaust'):
        cards = piles.get(zone)
        if cards is not None and (not isinstance(cards, list) or any(not isinstance(c, str) or not c for c in cards)):
            raise ValueError('pile contents must be named lists or null')
    order = piles.get('draw_order')
    if order is not None and (piles.get('draw') is None or Counter(order) != Counter(piles['draw'])):
        raise ValueError('a confirmed full draw order must match the confirmed draw pile')
    if not isinstance(state.get('enemies'), list):
        raise ValueError('enemies must be a list')
    if len({e.get('id', e.get('name')) for e in state['enemies']}) != len(state['enemies']):
        raise ValueError('each enemy requires a distinct target id')
    for enemy in state['enemies']:
        for field in ('hp', 'max_hp', 'block', 'weak', 'vulnerable', 'artifact'):
            value = enemy.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'enemy {field} must be nonnegative integer or null')
        if enemy.get('strength') is not None and type(enemy['strength']) is not int:
            raise ValueError('enemy Strength must be integer or null')
        hits = enemy.get('intent_hits')
        if not enemy.get('name') or (hits is not None and (not isinstance(hits, list) or any(type(h) is not int or h < 0 for h in hits))):
            raise ValueError('enemy needs a name and nonnegative displayed intent hits or null')


def state_digest(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def current_cost(card: dict, powers: dict) -> int | None:
    if card.get('cost') is None or card.get('type') is None:
        return None
    # Unplayable statuses/curses stay unplayable. Corruption applies only to Skills.
    return 0 if card['type'] == 'Skill' and powers.get('Corruption') is True else card['cost']


def boss_manifest(name: str | None, ascension: int | None) -> dict | None:
    if ascension is None or not 0 <= ascension <= 20:
        return None
    pack = rule_pack()
    boss = pack.get('bosses', {}).get(name)
    if boss is None:
        return None
    variant = next(v for v in boss['variants'] if v['min_ascension'] <= ascension <= v['max_ascension'])
    return {'name': name, 'ascension': ascension, 'version': pack['version'],
            'reviewed_at': pack['reviewed_at'], 'sources': boss['sources'], **variant, 'behavior': boss['behavior']}


# Only direct effects with understood sequencing can support a numeric forecast.
# Values are base values; observed card costs may differ. Anything else requires
# a new observation before continuing. Damage applies before Bash's Vulnerable.
DIRECT = {
    'Strike': (6, 0), 'Strike+': (9, 0), 'Defend': (0, 5), 'Defend+': (0, 8),
    'Iron Wave': (5, 5), 'Iron Wave+': (7, 7), 'Carnage': (20, 0), 'Carnage+': (28, 0),
    'Body Slam': (None, 0), 'Body Slam+': (None, 0),
}
BOUNDARIES = {'Headbutt', 'True Grit', 'Dual Wield', 'Armaments', 'Offering', 'Shrug It Off',
              'Bloodletting', 'Pommel Strike', 'Battle Trance', 'Second Wind', 'Reckless Charge',
              'Corruption', 'Barricade', 'Feel No Pain', 'Inflame', 'Shockwave', 'Disarm', 'Bash',
              'Clothesline', 'Panic Button', 'Burning Pact'}


def check_plan(context: dict, plan: dict) -> dict:
    """Check legality and dependencies; approval never means automatic input."""
    state = context.get('state') or {}
    if state:
        try:
            validate_snapshot(state)
        except (ValueError, KeyError, TypeError) as error:
            return {'allowed': False, 'reasons': [str(error)], 'notes': [], 'steps': [], 'forecast': None}
    reasons = list(context.get('unknowns', []))
    notes: list[str] = []
    if not context.get('fresh'):
        reasons.append('capture and record a fresh combat snapshot')
    for key in ('hp', 'energy', 'block', 'strength', 'dexterity', 'weak', 'vulnerable', 'frail', 'no_block'):
        if state.get(key) is None:
            reasons.append(f'{key} is unknown')
    if state.get('hand_complete') is not True:
        reasons.append('the complete current hand is unconfirmed')
    if state.get('powers_complete') is not True:
        reasons.append('active powers are unconfirmed')
    enemies = state.get('enemies', [])
    if not enemies or any(e.get('intent_hits') is None or not e.get('intent') for e in enemies):
        reasons.append('current enemy intent is unknown')
    inventory = context.get('inventory', {})
    for category in ('relic', 'potion'):
        if inventory.get('coverage', {}).get(category) != 'complete':
            reasons.append(f'confirmed {category} inventory is incomplete')
    relics = inventory.get('current', {}).get('relic', [])
    potions = inventory.get('current', {}).get('potion', [])
    counters = state.get('counters', {})
    boss = context.get('boss_manifest')
    if context.get('encounter_type') == 'boss' and boss is None:
        reasons.append('load a reviewed manifest for this boss and Ascension')
    time_eater = any(e.get('name') == 'Time Eater' for e in enemies)
    time_count = counters.get('time_warp') if time_eater else 0
    choker = counters.get('velvet_choker') if 'Velvet Choker' in relics else 0
    if time_eater and (type(time_count) is not int or not 0 <= time_count < 12):
        reasons.append('Time Warp count is unknown or outside 0–11')
    if 'Velvet Choker' in relics and (type(choker) is not int or not 0 <= choker <= 6):
        reasons.append('Velvet Choker play count is unknown')
    steps = plan.get('steps', [])
    if not isinstance(steps, list) or not steps:
        reasons.append('an ordered next-action plan is required')
        steps = []
    if reasons:
        return {'allowed': False, 'reasons': list(dict.fromkeys(reasons)), 'notes': notes, 'steps': [], 'forecast': None}
    hand = {c['id']: dict(c) for c in state['hand']}
    powers = state.get('powers', {})
    energy, block = state['energy'], state['block']
    forecast_enemies = [dict(e) for e in enemies]
    numeric = state.get('unmodeled_effects') == [] and len(enemies) == 1
    numeric = numeric and not powers.get('Corruption') and not powers.get('Feel No Pain') and not any(
        r in relics for r in ('Kunai', 'Shuriken', 'Pen Nib', 'Necronomicon', 'Nunchaku', 'Abacus', 'Ink Bottle'))
    if len(steps) == 1 and steps[0].get('kind') == 'end_turn' and state.get('unmodeled_effects') == []:
        numeric = True  # No card triggers: use existing Block as a conservative survival bound.
    numeric = numeric and all(e.get('hp') is not None and e.get('block') is not None and e.get('vulnerable') is not None for e in enemies)
    checked = []
    boundary = False
    forced_end = False
    for index, step in enumerate(steps):
        if boundary:
            reasons.append('plan crosses an observation boundary; observe before the next action')
            break
        kind = step.get('kind', 'card')
        if kind == 'potion':
            name = step.get('name')
            if name not in potions or name == 'Fairy in a Bottle':
                reasons.append('potion is absent or cannot be drunk manually')
            checked.append({'kind': kind, 'name': name, 'energy_after': None, 'observe_after': True})
            boundary = True
            numeric = False
            continue
        if kind == 'end_turn':
            reviews = plan.get('zero_cost_review', {})
            free_cards = [c for c in hand.values() if c.get('playable') is True and current_cost(c, powers) == 0]
            for card in free_cards:
                if not isinstance(reviews.get(card['id']), str) or not reviews[card['id']].strip():
                    reasons.append(f"review playable zero-cost {card['name']} before End Turn")
                if card['name'].rstrip('+') == 'Body Slam':
                    notes.append(f"Body Slam base damage now is {block}; include Strength, Weak, enemy Vulnerable and turn limits")
            if any(c.get('playable') is None or current_cost(c, powers) is None for c in hand.values()):
                reasons.append('unread hand card prevents the zero-cost review')
            checked.append({'kind': kind, 'energy_after': energy, 'observe_after': True})
            boundary = True
            continue
        if kind != 'card' or step.get('card_id') not in hand:
            reasons.append('card is absent from the confirmed remaining hand')
            continue
        card = hand.pop(step['card_id'])
        name = card['name']; base = name.rstrip('+')
        if name not in DIRECT and base not in BOUNDARIES:
            reasons.append(f'{name} has no reviewed immediate-effect check; inspect it before advice')
            continue
        upgraded = card.get('upgraded')
        color = card.get('title_color')
        if upgraded is None or color not in ('green', 'teal', 'white') or ((color in ('green', 'teal')) != upgraded) or name.endswith('+') != upgraded:
            reasons.append(f'{name}: verify title color and upgrade state')
        if card.get('playable') is not True:
            reasons.append(f'{name} is not confirmed playable')
        expected_type = 'Power' if base in ('Corruption', 'Barricade', 'Feel No Pain', 'Inflame') else 'Attack' if base in ('Strike', 'Iron Wave', 'Carnage', 'Body Slam', 'Headbutt', 'Pommel Strike', 'Reckless Charge', 'Bash', 'Clothesline') else 'Skill'
        if card.get('type') != expected_type:
            reasons.append(f'{name}: observed card type conflicts with its reviewed type')
        cost = current_cost(card, powers)
        if cost is None or cost > energy:
            reasons.append(f'{name}: current cost is unknown or exceeds remaining energy {energy}')
            continue
        if choker >= 6 and 'Velvet Choker' in relics:
            reasons.append('Velvet Choker prevents another card')
        if card.get('type') == 'Attack' and step.get('target') not in [e.get('id', e['name']) for e in forecast_enemies if e.get('hp') is not None and e['hp'] > 0]:
            reasons.append(f'{name} needs a confirmed living target')
        if base == 'Headbutt':
            discard = state.get('piles', {}).get('discard')
            if discard is None or (discard and step.get('return_card') not in discard):
                reasons.append('Headbutt needs a target from the confirmed current discard pile')
        if base == 'True Grit' and not upgraded:
            notes.append('True Grit is random until upgraded; do not promise a chosen exhaust')
            if step.get('exhaust_card_id'):
                reasons.append('unupgraded True Grit cannot choose its exhaust target')
        if base == 'True Grit' and upgraded and step.get('exhaust_card_id') not in hand:
            reasons.append('True Grit+ needs an eligible remaining hand card to exhaust')
        if base == 'Dual Wield':
            target = hand.get(step.get('copy_card_id'), {})
            if target.get('type') not in ('Attack', 'Power'):
                reasons.append('Dual Wield needs a confirmed Attack or Power remaining in hand')
            copies = 2 if upgraded else 1
            if len(hand) + copies > 10:
                reasons.append('Dual Wield copies exceed confirmed hand space')
        if base == 'Corruption' and 'Runic Pyramid' in relics:
            draw = state.get('piles', {}).get('draw')
            if draw is None:
                reasons.append('inspect the draw pile before Corruption with Runic Pyramid')
            key_cards = [c['name'] for c in hand.values()] + (draw or [])
            if not powers.get('Barricade') and any(c.rstrip('+') == 'Barricade' for c in key_cards) and not plan.get('setup_reason'):
                reasons.append('compare Barricade setup before Corruption and record why the chosen timing fits this fight')
        if base in ('Offering', 'Bloodletting') and state['hp'] <= (6 if base == 'Offering' else 3):
            reasons.append(f'{name} HP cost is not survivable')
        energy -= cost
        choker += 1
        time_count += 1
        if name in DIRECT and numeric:
            damage, gained = DIRECT[name]
            gained = 0 if state['no_block'] else max(0, gained + state['dexterity']) if gained else 0
            gained = gained * 3 // 4 if state['frail'] else gained
            block += gained
            if card['type'] == 'Attack':
                enemy = next((e for e in forecast_enemies if e.get('id', e['name']) == step.get('target')), None)
                if enemy:
                    raw = max(0, (block if damage is None else damage) + state['strength'])
                    raw = raw * (3 if state['weak'] else 4) * (3 if enemy['vulnerable'] else 2) // 8
                    absorbed = min(enemy['block'], raw)
                    enemy['block'] -= absorbed
                    enemy['hp'] = max(0, enemy['hp'] - raw + absorbed)
        else:
            numeric = False
        boundary = not numeric or name not in DIRECT or base in BOUNDARIES or (time_eater and time_count == 12)
        if time_eater and time_count == 12:
            forced_end = True
            notes.append('The twelfth card ends the turn and adds 2 enemy Strength; recheck the resulting attack')
        checked.append({'kind': kind, 'card_id': card['id'], 'name': name, 'target': step.get('target'),
                        'energy_after': energy, 'time_warp_after': time_count if time_eater else None,
                        'choker_after': choker if 'Velvet Choker' in relics else None, 'observe_after': boundary})
    forecast = None
    if numeric and not reasons:
        kill = all(e['hp'] == 0 for e in forecast_enemies)
        incoming = 0 if kill else sum(sum(e['intent_hits']) for e in enemies)
        if forced_end and not kill:
            enemy = enemies[0]
            intent = enemy.get('move')
            if enemy.get('strength') is None or enemy.get('weak') is None or boss is None:
                numeric = False
            elif intent in ('Reverberate', 'Head Slam'):
                raw_hits = boss['reverberate_base_hits'] if intent == 'Reverberate' else [boss['head_slam_base']]
                incoming = sum(max(0, h + enemy['strength'] + 2) * (3 if enemy['weak'] else 4) * (3 if state['vulnerable'] else 2) // 8 for h in raw_hits)
            elif enemy['intent_hits']:
                numeric = False
        # End-of-turn statuses and relic timing are outside this direct model.
        end_damage = state.get('end_turn_damage')
        if end_damage == 0 and numeric:
            forecast = {'block': block, 'incoming_displayed': incoming, 'player_hp': state['hp'] - max(0, incoming - block),
                        'enemies': forecast_enemies, 'lethal_to_enemies': kill}
            if forecast['player_hp'] <= 0:
                reasons.append('the checked line does not survive the displayed incoming damage')
    if (forced_end or any(s.get('kind') == 'end_turn' for s in steps)) and forecast is None:
        reasons.append('ending the turn requires a verified survival forecast; inspect unmodeled effects first')
    if plan.get('claims_lethal') and not (forecast and forecast['lethal_to_enemies']):
        reasons.append('lethal is not verified for the complete proposed line')
    if forecast is None:
        notes.append('Full damage/Block/survival forecast is unavailable for these interactions; observe before extending the line')
    return {'allowed': not reasons, 'reasons': list(dict.fromkeys(reasons)), 'notes': notes, 'steps': checked,
            'forecast': forecast, 'boss_manifest': boss,
            'abort_if': 'Any shown card, cost, target, counter, intent or resource differs; record a fresh snapshot.'}


def campfire_comparison(state: dict, relics: list[str]) -> dict:
    """Compare the same route with Rest or Smith, counting only future healing."""
    required = ('hp', 'max_hp', 'deck_size', 'entry_heal_applied', 'next_node_is_boss')
    if any(state.get(k) is None for k in required):
        raise ValueError('campfire requires confirmed HP, deck size, entry-heal timing, and next-node classification')
    hp, maximum, size = state['hp'], state['max_hp'], state['deck_size']
    if not (0 < maximum and 0 <= hp <= maximum and size >= 0):
        raise ValueError('invalid campfire HP or deck size')
    feather = (size // 5) * 3 if 'Eternal Feather' in relics and not state['entry_heal_applied'] else 0
    entry = min(maximum, hp + feather)
    rest = maximum * 30 // 100
    boss_heal = 25 if state['next_node_is_boss'] and 'Pantograph' in relics else 0
    # Do not assume any future fight is harmless. Require observed/net projected
    # HP immediately before boss if this is not a direct campfire-to-boss route.
    if state.get('intervening_combats', 0):
        raise ValueError('intervening combat damage is unknown; compare again on a confirmed direct boss route')
    return {'current_hp': hp, 'max_hp': maximum, 'entry_heal': feather, 'campfire_hp': entry,
            'rest_heal': rest, 'burning_blood_future_heal': 0, 'pantograph_heal': boss_heal,
            'smith': {'boss_entry_hp': entry, 'boss_start_hp': min(maximum, entry + boss_heal)},
            'rest': {'boss_entry_hp': min(maximum, entry + rest), 'boss_start_hp': min(maximum, entry + rest + boss_heal)},
            'relic_inputs': relics, 'rule_version': rule_pack()['version']}
