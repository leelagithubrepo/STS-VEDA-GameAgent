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
SPIKE_SLIME_SOURCE = 'https://slay-the-spire.fandom.com/wiki/Spike_Slime'
SPIKE_SLIMES = ('Spike Slime (L)', 'Spike Slime (M)')
COLLECTOR_SOURCE = 'https://slaythespire-archive.fandom.com/wiki/The_Collector'
COLLECTOR_ENEMIES = ('The Collector', 'Torch Head')
COLLECTOR_RELICS = frozenset({
    'Burning Blood', "Neow's Lament", 'Anchor', 'Centennial Puzzle',
    'Juzu Bracelet', 'Bronze Scales', 'Red Mask', 'Shuriken',
    'Bag of Preparation', 'Potion Belt',
})
COLLECTOR_EFFECTS = {
    'Fireball': [],
    'Buff': [{'kind': 'gain_strength', 'target': 'all_enemies', 'amount': 3},
             {'kind': 'gain_block', 'target': 'self', 'amount': 15}],
    'Mega Debuff': [{'kind': 'apply_debuff', 'debuff': name, 'amount': 3}
                    for name in ('weak', 'vulnerable', 'frail')],
    'Spawn': [{'kind': 'summon_to_limit', 'enemy': 'Torch Head', 'amount': 2}],
    'Revive': [{'kind': 'summon_to_limit', 'enemy': 'Torch Head', 'amount': 2}],
}


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
    if state.get('ascension') is not None and (type(state['ascension']) is not int or not 0 <= state['ascension'] <= 20):
        raise ValueError('Ascension must be an integer from 0 to 20 or null')
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
        if enemy.get('hp') is not None and enemy.get('max_hp') is not None and enemy['hp'] > enemy['max_hp']:
            raise ValueError('enemy HP exceeds maximum HP')
        split = enemy.get('split')
        if split is not None:
            if (not isinstance(split, dict) or enemy['name'] != 'Spike Slime (L)'
                    or type(split.get('threshold_percent')) is not int or split['threshold_percent'] != 50
                    or type(split.get('smaller_slimes')) is not int or split['smaller_slimes'] != 2
                    or split.get('hp') != 'current HP'
                    or enemy.get('max_hp') is None or enemy['max_hp'] <= 0):
                raise ValueError('Split needs the reviewed large Spike Slime 50%/two/current-HP rule and confirmed maximum HP')
        effects = enemy.get('intent_effects')
        if effects is not None:
            if not isinstance(effects, list):
                raise ValueError('enemy intent effects must be a typed list or null')
            for effect in effects:
                if not isinstance(effect, dict):
                    raise ValueError('each enemy intent effect must be an object')
                kind = effect.get('kind')
                count = effect.get('count') if kind == 'generate_status' else effect.get('amount')
                if kind not in ('generate_status', 'apply_debuff', 'gain_strength', 'gain_block', 'summon_to_limit') or type(count) is not int or count <= 0:
                    raise ValueError('enemy intent effect needs a reviewed kind and positive integer amount')
                if kind == 'generate_status' and (not effect.get('card') or effect.get('to_zone') not in ('draw', 'discard', 'hand')):
                    raise ValueError('generated status needs a named card and zone')
                if kind == 'apply_debuff' and not effect.get('debuff'):
                    raise ValueError('debuff effect needs a name')
                if kind in ('gain_strength', 'gain_block') and effect.get('target') not in ('self', 'all_enemies'):
                    raise ValueError('enemy gain effect needs a reviewed target')
                if kind == 'summon_to_limit' and not effect.get('enemy'):
                    raise ValueError('summon effect needs a named enemy')
        if enemy.get('evidence') is not None and not isinstance(enemy['evidence'], dict):
            raise ValueError('enemy intent evidence must be an object or null')


def state_digest(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def current_cost(card: dict, powers: dict) -> int | None:
    if card.get('cost') is None or card.get('type') is None:
        return None
    # Unplayable statuses/curses stay unplayable. Corruption applies only to Skills.
    return 0 if card['type'] == 'Skill' and powers.get('Corruption') is True else card['cost']


def _verified_costless_dazed(card: dict) -> bool:
    # Dazed has no energy cost. A fully inspected, inherently unplayable Dazed
    # cannot hide a zero-cost play; other unread costs must still stop review.
    return (card.get('name') == 'Dazed' and card.get('type') == 'Status'
            and 'cost' in card and card['cost'] is None
            and card.get('upgraded') is False and card.get('title_color') == 'white'
            and card.get('playable') is False and card.get('unplayable') is True
            and card.get('ethereal') is True)


def boss_manifest(name: str | None, ascension: int | None) -> dict | None:
    if type(ascension) is not int or not 0 <= ascension <= 20:
        return None
    pack = rule_pack()
    boss = pack.get('bosses', {}).get(name)
    if boss is None:
        return None
    variant = next((v for v in boss['variants'] if v['min_ascension'] <= ascension <= v['max_ascension']), None)
    if variant is None:
        return None
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

# Exact inspected variants only. These effects terminate a checked line: the
# generated card and HP change must be observed before another recommendation.
# This metadata is a prediction, never a combat-zone or inventory mutation.
REVIEWED_SPECIAL_CARDS = {
    'Anger': {'type': 'Attack', 'base_damage': 6,
              'generate': {'card': 'Anger', 'count': 1, 'zone': 'discard'}},
    # Exact text retained in the current A2 inventory evidence. Do not derive
    # an uninspected variant by stripping '+' or substituting its usual cost.
    'Hemokinesis': {'type': 'Attack', 'base_damage': 15, 'hp_loss': 2},
    'Hemokinesis+': {'type': 'Attack', 'base_damage': 20, 'hp_loss': 2},
    'Bash': {'type': 'Attack', 'base_damage': 8, 'vulnerable': 2},
    'Bash+': {'type': 'Attack', 'base_damage': 10, 'vulnerable': 3},
    'Clothesline': {'type': 'Attack', 'base_damage': 12, 'weak': 2},
    'Headbutt': {'type': 'Attack', 'base_damage': 9, 'discard_to_draw': 1},
    # Both block variants are documented by data/act1_reference_pack.json.
    'Shrug It Off': {'type': 'Skill', 'base_block': 8, 'draw': 1},
    'Shrug It Off+': {'type': 'Skill', 'base_block': 11, 'draw': 1},
    'Slimed': {'type': 'Status', 'exhaust': True},
    # Exact September 27 inventory variants. Draws, choices, powers, generated
    # statuses and changed enemy debuffs always stop the proposed line.
    'Power Through+': {'type': 'Skill', 'base_block': 20,
                       'generate': {'card': 'Wound', 'count': 2, 'zone': 'hand'}},
    'Metallicize': {'type': 'Power', 'end_turn_block': 3},
    'Uppercut': {'type': 'Attack', 'base_damage': 13, 'weak': 1, 'vulnerable': 1},
    'Warcry': {'type': 'Skill', 'draw': 1, 'hand_to_draw': 1, 'exhaust': True},
    'Spot Weakness': {'type': 'Skill', 'strength_if_target_attacks': 3},
    # Archived current-deck text: Block first, then random other-card exhaust.
    # The identity/zone change is deliberately unknown until a fresh reading.
    'True Grit': {'type': 'Skill', 'base_block': 7, 'random_exhaust': 1},
}


def reviewed_card_type(name: str) -> str | None:
    """Return the reviewed type without granting support to unread variants."""
    if name in REVIEWED_SPECIAL_CARDS:
        return REVIEWED_SPECIAL_CARDS[name]['type']
    base = name.rstrip('+')
    if name not in DIRECT and base not in BOUNDARIES:
        return None
    if base in ('Corruption', 'Barricade', 'Feel No Pain', 'Inflame'):
        return 'Power'
    if base in ('Strike', 'Iron Wave', 'Carnage', 'Body Slam', 'Headbutt',
                'Pommel Strike', 'Reckless Charge', 'Bash', 'Clothesline'):
        return 'Attack'
    return 'Skill'


def _reviewed_spike_intent(state: dict, enemy: dict) -> bool:
    """Bounded A2 interpretation; the screen's damage is never replaced by a table."""
    if enemy.get('name') not in SPIKE_SLIMES:
        # Other encounters retain their existing evidence requirements. A new
        # typed effect cannot silently claim review through the Slime registry.
        return 'intent_effects' not in enemy or enemy['intent_effects'] == []
    evidence = enemy.get('evidence') or {}
    if (state.get('ascension') != 2 or evidence.get('source') != SPIKE_SLIME_SOURCE
            or evidence.get('kind') != 'reviewed_reference' or evidence.get('observed_intent') is not True):
        return False
    large = enemy['name'] == 'Spike Slime (L)'
    if large and enemy.get('split') is None:
        return False
    move = enemy.get('move')
    hits = enemy.get('intent_hits')
    if large and enemy.get('hp') is not None and enemy['hp'] * 2 <= enemy['max_hp'] and move != 'Split':
        return False  # Below-threshold HP with an old attack is not a fresh Split intent.
    if move == 'Flame Tackle':
        expected = [{'kind': 'generate_status', 'card': 'Slimed',
                     'count': 2 if large else 1, 'to_zone': 'discard'}]
        return isinstance(hits, list) and len(hits) == 1 and enemy.get('intent_effects') == expected
    if move == 'Lick':
        expected = [{'kind': 'apply_debuff', 'debuff': 'frail', 'amount': 2 if large else 1}]
        return hits == [] and enemy.get('intent_effects') == expected
    if move == 'Split' and large and enemy.get('hp') is not None:
        return (hits == [] and enemy.get('intent_effects') == []
                and 0 < enemy['hp'] * 2 <= enemy['max_hp'])
    return False


def _verified_costless_status(card: dict) -> bool:
    return _verified_costless_dazed(card) or (
        card.get('name') == 'Wound' and card.get('type') == 'Status'
        and 'cost' in card and card['cost'] is None
        and card.get('upgraded') is False and card.get('title_color') == 'white'
        and card.get('playable') is False and card.get('unplayable') is True)


def current_run_relic_reasons(state: dict, relics: list[str], potions: list[str]) -> list[str]:
    """Observed state, never relic ownership alone, establishes settled effects."""
    reasons = []
    if 'Shuriken' in relics:
        counters = state.get('counters')
        count = counters.get('shuriken') if isinstance(counters, dict) else None
        if type(count) is not int or not 0 <= count <= 2:
            reasons.append('Shuriken current-turn attack counter is unknown or outside 0–2')
    if 'Red Mask' in relics and any(
            type(e.get('weak')) is not int or type(e.get('artifact')) is not int
            for e in state.get('enemies', [])):
        reasons.append('observe every enemy Weak/Artifact after Red Mask; never apply its opening effect from ownership alone')
    # Potion Belt has no combat-resolution trigger. Complete potion inventory
    # is checked by the caller; this function never invents slot contents.
    return reasons


def metallicize_block(state: dict) -> int | None:
    """A known intensity grants end-turn Block, independently of card modifiers."""
    powers = state.get('powers')
    if not isinstance(powers, dict):
        return None
    value = powers.get('Metallicize', 0)
    if type(value) is not int or value < 0:
        return None
    # Panic Button's No Block, like Frail/Dexterity, changes card Block only.
    return value


def reviewed_collector_attack_roster(state: dict) -> bool:
    """Compatibility predicate for the unchanged displayed-hit subset."""
    return reviewed_collector_roster(state) and all(e['intent_hits'] for e in state['enemies'])


def reviewed_collector_roster(state: dict) -> bool:
    """Match observed identities/moves/effects, never a turn-count prediction."""
    enemies = state.get('enemies', [])
    if (type(state.get('ascension')) is not int or state['ascension'] != 2
            or state.get('enemies_complete') is not True or not isinstance(enemies, list)
            or not 1 <= len(enemies) <= 3
            or any(not isinstance(e, dict) for e in enemies)
            or sum(e.get('name') == 'The Collector' for e in enemies) != 1):
        return False
    ids = [e.get('id') for e in enemies]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        return False
    for e in enemies:
        evidence = e.get('evidence') or {}
        if (e.get('name') not in COLLECTOR_ENEMIES
                or not isinstance(evidence, dict)
                or evidence.get('source') != COLLECTOR_SOURCE
                or evidence.get('kind') != 'reviewed_reference'
                or evidence.get('observed_intent') is not True
                or any(type(e.get(k)) is not int or e[k] < 0
                       for k in ('hp', 'max_hp', 'block', 'weak', 'vulnerable', 'artifact'))
                or e['hp'] <= 0 or e['hp'] > e['max_hp']
                or type(e.get('strength')) is not int):
            return False
        move = e.get('move')
        attack = move == ('Fireball' if e['name'] == 'The Collector' else 'Tackle')
        effects = COLLECTOR_EFFECTS.get(move) if e['name'] == 'The Collector' else []
        if ((e['name'] == 'The Collector' and move not in COLLECTOR_EFFECTS)
                or (e['name'] == 'Torch Head' and not attack)
                or e.get('intent_effects') != effects
                or not isinstance(e.get('intent_hits'), list)
                or len(e['intent_hits']) != (1 if attack else 0)
                or any(type(h) is not int or h < 0 for h in e['intent_hits'])):
            return False
    return True


def _displayed_raw_upper(hit: int, weak: bool, vulnerable: bool) -> int | None:
    """Largest nonnegative integer raw hit compatible with final-floor damage.

    d = floor(raw*n/q) implies raw < (d+1)*q/n, so the integer
    maximum is ((d+1)*q-1)//n. Dividing the rounded display directly
    loses candidates; e.g. Weak 5 can represent raw 7.
    """
    numerator = (3 if weak else 4) * (3 if vulnerable else 2)
    upper = ((hit + 1) * 8 - 1) // numerator
    return upper if upper * numerator // 8 == hit else None


def collector_turn_bound(state: dict, relics: list[str] | None) -> dict | None:
    """One enemy-turn upper bound, not exact order or future state.

    At most three observed enemies and two hypothetical summon slots. Unknown
    modifiers stay unsupported; favorable Artifact/Thorns/debuff expiry is not
    credited. See the source/versioned A2 manifest and focused proof tests.
    """
    if (not reviewed_collector_roster(state) or not isinstance(relics, list)
            or any(r not in COLLECTOR_RELICS for r in relics)
            or state.get('unmodeled_effects') != [] or state.get('end_turn_damage') != 0
            or state.get('powers_complete') is not True or not isinstance(state.get('powers'), dict)
            or any((k not in ('Metallicize', 'Artifact', 'Thorns') and v != 0)
                   or (k in ('Metallicize', 'Artifact', 'Thorns') and (type(v) is not int or v < 0))
                   for k, v in state['powers'].items())
            or state['powers'].get('Thorns', 0) not in (0, 3)
            or (state['powers'].get('Thorns', 0) and 'Bronze Scales' not in relics)
            or metallicize_block(state) is None
            or any(type(state.get(k)) is not int or state[k] < 0
                   for k in ('weak', 'vulnerable', 'frail', 'no_block'))
            or any(type(state.get(k)) is not int for k in ('strength', 'dexterity'))):
        return None
    enemies = state['enemies']
    boss = next(e for e in enemies if e['name'] == 'The Collector')
    move = boss['move']
    shown = sum(sum(e['intent_hits']) for e in enemies)
    upper, terms = 0, []
    for e in enemies:
        if not e['intent_hits']:
            continue
        hit = e['intent_hits'][0]
        if move in ('Buff', 'Mega Debuff') and e['name'] == 'Torch Head':
            raw = _displayed_raw_upper(hit, e['weak'] > 0, state['vulnerable'] > 0)
            if raw is None:
                return None  # Observed hit and claimed modifiers are inconsistent.
            raw += 3 if move == 'Buff' else 0
            vulnerable = state['vulnerable'] > 0 or move == 'Mega Debuff'
            bound = max(hit, raw * (3 if e['weak'] else 4) * (3 if vulnerable else 2) // 8)
        else:
            bound = hit
        upper += bound
        terms.append({'enemy_id': e['id'], 'displayed': hit, 'damage_upper_bound': bound})
    vacancies = 2 - sum(e['name'] == 'Torch Head' for e in enemies)
    # A torch may hit, die to Scales, and be replaced by the later boss action.
    # Retain its old hit AND allow its replacement; "ignore beneficial deaths"
    # alone is insufficient when an observed resummon can refill that slot.
    possible_thorns_vacancies = sum(e['name'] == 'Torch Head' and e['hp'] <= 3
                                    for e in enemies) if 'Bronze Scales' in relics else 0
    summon_slots = vacancies + possible_thorns_vacancies
    spawned_hit = None
    if move in ('Spawn', 'Revive'):
        # The community simulator resets summons' Strength. Allow inherited
        # observed Strength too, and immediate attacks, to avoid assuming its
        # summon timing/implementation is identical to the console build.
        spawn_strength = max(0, *(e['strength'] for e in enemies))
        spawned_hit = (7 + spawn_strength) * (3 if state['vulnerable'] else 2) // 2
        if summon_slots:
            upper += summon_slots * spawned_hit
            terms.append({'hypothetical_summon_slots': summon_slots,
                          'possible_thorns_vacancies': possible_thorns_vacancies,
                          'damage_upper_bound_per_slot': spawned_hit,
                          'strength_upper_bound': spawn_strength})
    return {'kind': 'conservative_survival_bound', 'scope': 'one_enemy_turn_only',
            'boss_move_observed': move, 'incoming_displayed': shown,
            'incoming_upper_bound': upper, 'terms': terms,
            'summon_damage_upper_bound_per_slot': spawned_hit,
            'assumptions': ['boss effects may precede all Torch Head attacks',
                'ignore favorable Artifact and player Vulnerable expiry; allow Thorns deaths to open replacement summon slots',
                'summons may attack immediately with maximum observed enemy Strength',
                'no future roster, status, roll or minion-death outcome is asserted']}


def _explosive_effect(state: dict, relics: list[str], step: dict) -> tuple[dict | None, list[str]]:
    enemies = state['enemies']
    ids = [e.get('id', e['name']) for e in enemies if e.get('hp', 0)]
    targets = step.get('targets')
    if (state.get('enemies_complete') is not True or not isinstance(targets, list)
            or any(not isinstance(t, str) for t in targets) or len(set(targets)) != len(targets)
            or set(targets) != set(ids)):
        return None, ['Explosive Potion requires the complete current living target roster']
    if 'Sacred Bark' in relics:
        return None, ['Explosive Potion modifier Sacred Bark is outside this reviewed variant']
    if (state.get('unmodeled_effects') != [] or any(
            type(e.get('hp')) is not int or type(e.get('block')) is not int
            or e.get('potion_damage_modifiers') != [] or e.get('damage_reactions') != []
            for e in enemies)):
        return None, ['Explosive Potion needs known HP/Block and explicitly reviewed damage modifiers/reactions for every enemy']
    outcomes = []
    for e in enemies:
        absorbed = min(e['block'], 10) if e['hp'] else 0
        outcomes.append({'id': e.get('id', e['name']), 'hp': max(0, e['hp'] - 10 + absorbed),
                         'block': e['block'] - absorbed})
    return {'base_damage': 10, 'targeting': 'all_living_enemies', 'enemies': outcomes,
            'scope': 'immediate_damage_only', 'observe_after': True}, []


def check_plan(context: dict, plan: dict) -> dict:
    """Check legality and dependencies; approval never means automatic input."""
    if not isinstance(plan, dict):
        return {'allowed': False, 'reasons': ['plan must be an object'], 'notes': [], 'steps': [], 'forecast': None}
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
    reasons.extend(current_run_relic_reasons(state, relics, potions))
    if metallicize_block(state) is None:
        reasons.append('Metallicize intensity is unknown or invalid')
    counters = state.get('counters', {})
    boss = context.get('boss_manifest')
    if context.get('encounter_type') == 'boss' and boss is None:
        reasons.append('load a reviewed manifest for this boss and Ascension')
    if any(e.get('name') in COLLECTOR_ENEMIES for e in enemies):
        expected = boss_manifest('The Collector', state.get('ascension'))
        if expected is None or boss != expected or context.get('encounter_type') != 'boss':
            reasons.append('Collector requires its matching reviewed Ascension manifest and boss context')
    time_eater = any(e.get('name') == 'Time Eater' for e in enemies)
    time_count = counters.get('time_warp') if time_eater else 0
    choker = counters.get('velvet_choker') if 'Velvet Choker' in relics else 0
    if time_eater and (type(time_count) is not int or not 0 <= time_count < 12):
        reasons.append('Time Warp count is unknown or outside 0–11')
    if 'Velvet Choker' in relics and (type(choker) is not int or not 0 <= choker <= 6):
        reasons.append('Velvet Choker play count is unknown')
    steps = plan.get('steps', [])
    if not isinstance(steps, list) or not steps or any(not isinstance(step, dict) for step in steps):
        reasons.append('an ordered next-action plan is required')
        steps = []
    if reasons:
        return {'allowed': False, 'reasons': list(dict.fromkeys(reasons)), 'notes': notes, 'steps': [], 'forecast': None}
    hand = {c['id']: dict(c) for c in state['hand']}
    powers = state.get('powers', {})
    energy, block = state['energy'], state['block']
    forecast_enemies = [dict(e) for e in enemies]
    shuriken_count = counters.get('shuriken') if 'Shuriken' in relics else None
    numeric = state.get('unmodeled_effects') == [] and len(enemies) == 1
    numeric = numeric and not powers.get('Corruption') and not powers.get('Feel No Pain') and not any(
        r in relics for r in ('Kunai', 'Pen Nib', 'Necronomicon', 'Nunchaku', 'Abacus', 'Ink Bottle'))
    if len(steps) == 1 and steps[0].get('kind') == 'end_turn' and state.get('unmodeled_effects') == []:
        numeric = True  # No card triggers: use existing Block as a conservative survival bound.
    numeric = numeric and all(e.get('hp') is not None and e.get('block') is not None and e.get('vulnerable') is not None for e in enemies)
    collector_present = any(e.get('name') in COLLECTOR_ENEMIES for e in enemies)
    collector_bound = collector_turn_bound(state, relics) if collector_present else None
    if collector_present and collector_bound is None:
        numeric = False
        notes.append('Collector turn lacks a reviewed complete A2 roster, typed current effects or supported modifiers; no survival bound is available')
    if not collector_present and any(not _reviewed_spike_intent(state, e) for e in enemies):
        numeric = False
        notes.append('Enemy intent effects lack a matching reviewed move, Ascension and evidence; no survival forecast is available')
    checked = []
    boundary = False
    forced_end = False
    pays_hp = False
    for index, step in enumerate(steps):
        if boundary:
            reasons.append('plan crosses an observation boundary; observe before the next action')
            break
        kind = step.get('kind', 'card')
        split_boundary = False
        if kind == 'potion':
            name = step.get('name')
            if name not in potions or name == 'Fairy in a Bottle':
                reasons.append('potion is absent or cannot be drunk manually')
            checked.append({'kind': kind, 'name': name, 'energy_after': None, 'observe_after': True})
            if name == 'Explosive Potion':
                effect, gaps = _explosive_effect(state, relics, step)
                reasons.extend(gaps)
                if effect is not None:
                    checked[-1]['reviewed_effect'] = effect
            boundary = True
            numeric = False
            continue
        if kind == 'end_turn':
            reviews = plan.get('zero_cost_review', {})
            if not isinstance(reviews, dict):
                reviews = {}
            free_cards = [c for c in hand.values() if c.get('playable') is True and current_cost(c, powers) == 0]
            for card in free_cards:
                if not isinstance(reviews.get(card['id']), str) or not reviews[card['id']].strip():
                    reasons.append(f"review playable zero-cost {card['name']} before End Turn")
                if card['name'].rstrip('+') == 'Body Slam':
                    notes.append(f"Body Slam base damage now is {block}; include Strength, Weak, enemy Vulnerable and turn limits")
            if any(c.get('playable') is None or (
                    current_cost(c, powers) is None and not _verified_costless_status(c))
                    for c in hand.values()):
                reasons.append('unread hand card prevents the zero-cost review')
            checked.append({'kind': kind, 'energy_after': energy, 'observe_after': True})
            boundary = True
            continue
        if kind != 'card' or step.get('card_id') not in hand:
            reasons.append('card is absent from the confirmed remaining hand')
            continue
        card = hand.pop(step['card_id'])
        name = card['name']; base = name.rstrip('+')
        expected_type = reviewed_card_type(name)
        if expected_type is None:
            reasons.append(f'{name} has no reviewed immediate-effect check; inspect it before advice')
            continue
        upgraded = card.get('upgraded')
        color = card.get('title_color')
        if upgraded is None or color not in ('green', 'teal', 'white') or ((color in ('green', 'teal')) != upgraded) or name.endswith('+') != upgraded:
            reasons.append(f'{name}: verify title color and upgrade state')
        if card.get('playable') is not True:
            reasons.append(f'{name} is not confirmed playable')
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
        if name == 'Spot Weakness':
            target = next((e for e in enemies if e.get('id', e['name']) == step.get('target') and type(e.get('hp')) is int and e['hp'] > 0), None)
            if target is None or not target.get('intent_hits'):
                reasons.append('Spot Weakness needs a confirmed living target with a current attack intent')
        if name == 'Power Through+' and len(hand) + 2 > 10:
            reasons.append('Power Through+ status overflow requires a separately reviewed hand-limit outcome')
        if name == 'Warcry' and step.get('return_card_id') is not None:
            reasons.append('Warcry draws before selection; observe the drawn hand before choosing its return card')
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
        immediate_effect = REVIEWED_SPECIAL_CARDS.get(name)
        pays_hp = pays_hp or base in ('Offering', 'Bloodletting') or bool(
            immediate_effect and immediate_effect.get('hp_loss'))
        if immediate_effect and state['hp'] <= immediate_effect.get('hp_loss', 0):
            reasons.append(f'{name} HP cost is not survivable; Block does not prevent HP loss')
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
                    if enemy.get('split') is not None and 0 < enemy['hp'] * 2 <= enemy['max_hp']:
                        split_boundary = True
                        numeric = False
                        notes.append('The attack reaches the observed Split threshold; inspect the new intent before continuing or forecasting the enemy turn')
        else:
            numeric = False
        shuriken_trigger = False
        if shuriken_count is not None and card['type'] == 'Attack':
            shuriken_count = (shuriken_count + 1) % 3
            shuriken_trigger = shuriken_count == 0
            if shuriken_trigger:
                numeric = False
                notes.append('Third attack triggers Shuriken; observe Strength and the reset attack counter before another play')
        boundary = split_boundary or shuriken_trigger or not numeric or name not in DIRECT or base in BOUNDARIES or (time_eater and time_count == 12)
        if time_eater and time_count == 12:
            forced_end = True
            notes.append('The twelfth card ends the turn and adds 2 enemy Strength; recheck the resulting attack')
        checked.append({'kind': kind, 'card_id': card['id'], 'name': name, 'target': step.get('target'),
                        'energy_after': energy, 'time_warp_after': time_count if time_eater else None,
                        'choker_after': choker if 'Velvet Choker' in relics else None, 'observe_after': boundary})
        if shuriken_count is not None:
            checked[-1]['shuriken_after'] = shuriken_count
            checked[-1]['shuriken_strength_gain'] = 1 if shuriken_trigger else 0
        if immediate_effect:
            checked[-1]['reviewed_effect'] = json.loads(json.dumps(immediate_effect))
        if split_boundary:
            checked[-1]['observation_reason'] = 'Split threshold reached; intent may have changed'
    forecast = None
    if numeric and not reasons:
        kill = all(e['hp'] == 0 for e in forecast_enemies)
        incoming = 0 if kill else sum(sum(e['intent_hits']) for e in enemies)
        if collector_bound is not None and not kill:
            incoming = collector_bound['incoming_upper_bound']
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
            end_block = metallicize_block(state)
            survival_block = block + (end_block if not kill else 0)
            forecast = {'block': block, 'end_turn_block': end_block if not kill else 0,
                        'survival_block': survival_block, 'incoming_displayed': incoming,
                        'player_hp': state['hp'] - max(0, incoming - survival_block),
                        'enemies': forecast_enemies, 'lethal_to_enemies': kill}
            if collector_bound is not None:
                forecast.update(collector_bound)
                forecast['incoming_upper_bound'] = 0 if kill else incoming
                forecast['player_hp_lower_bound'] = forecast['player_hp']
                forecast['enemy_state_scope'] = 'after_player_actions_before_enemy_turn'
                notes.append('Collector survival uses a conservative one-turn damage upper bound, not exact enemy order or future state; observe the resolved roster and statuses before continuing')
            if any(e.get('intent_effects') or e.get('move') == 'Split' for e in enemies):
                notes.append('The survival bound covers this displayed enemy turn only; observe status/debuff changes and any Split children before further advice')
            if forecast['player_hp'] <= 0:
                reasons.append('the checked line does not survive the displayed incoming damage')
    if (forced_end or any(s.get('kind') == 'end_turn' for s in steps)) and forecast is None:
        reasons.append('ending the turn requires a verified survival forecast; inspect unmodeled effects first')
    if plan.get('claims_lethal') and not (forecast and forecast['lethal_to_enemies']):
        reasons.append('lethal is not verified for the complete proposed line')
    # A stored rule is not enough: make the potion comparison part of the
    # checked decision before committing HP or spending the twelfth card.
    ends_turn = forced_end or any(s.get('kind') == 'end_turn' for s in steps)
    exposes_hp = ends_turn and (forecast is None or forecast['player_hp'] < state['hp'])
    if pays_hp or forced_end or exposes_hp:
        reviews = plan.get('potion_review', {})
        if not isinstance(reviews, dict):
            reviews = {}
        for name in dict.fromkeys(potions):
            if name == 'Fairy in a Bottle':
                notes.append('Fairy in a Bottle triggers automatically; it cannot be drunk and is not included in this survival forecast')
            elif not isinstance(reviews.get(name), str) or not reviews[name].strip():
                reasons.append(f'review available {name} before committing HP or the forced end of turn')
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
