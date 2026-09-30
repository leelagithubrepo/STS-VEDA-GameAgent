"""Cross-check map arrival telemetry against its reviewed room observation."""
from collections import Counter

from .choice_execution import _require, _same_json, _text


def validate_map_arrival(before, after, changes, *, policy='strict'):
    """Reject contradictory lifecycle/zone declarations before durable freeze.

    This is not a recognizer: it verifies that all declared versions of the
    selected room and opening hand agree. The adapter still owns live evidence.
    """
    selected = before['choice']['option_ids'][0]
    node = next(o['node'] for o in before['observation']['ui']['options'] if o['id'] == selected)
    obs = after['observation']
    facts, resources = obs['facts'], obs['resources']
    combat = obs['ui']['screen'] == 'combat'
    _require(set(changes) <= {'transitions', 'zone_baseline', 'zone_coverage'},
             'map arrival cannot assert unrelated inventory or zone events')
    transitions = changes.get('transitions', [])
    expected = ['advance_floor', 'start_combat', 'start_turn'] if combat else ['advance_floor']
    _require([t.get('kind') for t in transitions] == expected, 'map arrival requires its exact ordered lifecycle')
    floor = transitions[0]
    revealed_kind = {'combat': 'enemy', 'rest': 'rest', 'shop': 'merchant',
                     'treasure': 'treasure', 'event': 'event', 'reward': 'reward'}.get(obs['ui']['screen'])
    expected_kind = facts.get('node_type') if node['kind'] == 'event' else node['kind']
    kind_matches = floor.get('node_type') == expected_kind
    # A normal map icon can reveal a different noncombat room in the live
    # screen after activation (for example, a stale map declaration said
    # Enemy but the settled room is Rest). Learning mode records the actual
    # room and continues; strict mode retains the original contradiction gate.
    recovered_room_kind = (policy == 'learning' and node['kind'] != 'event'
                           and facts.get('node_type') == revealed_kind
                           and floor.get('node_type') == facts.get('node_type'))
    _require(revealed_kind is not None and facts.get('node_type') == revealed_kind
             and floor.get('act') == node['act'] and floor.get('floor') == node['floor']
             and (kind_matches or recovered_room_kind) and facts.get('current_node_id') == selected,
             'map arrival lifecycle conflicts with selected or revealed room')
    _require(floor.get('previous_outcome') == 'departed'
             and _same_json(floor.get('previous_ending_state'),
                            {'screen': 'map', **before['observation']['resources']})
             and _same_json(floor.get('starting_state'), {'screen': obs['ui']['screen'],
                 'act': facts['act'], 'floor': facts['floor'], 'node_id': selected, **resources}),
             'floor lifecycle states must match actual reviewed map and room resources')
    if not combat:
        _require(not changes.get('zone_baseline') and changes.get('zone_coverage', 'unknown') == 'unknown',
                 'noncombat arrival cannot establish combat zones')
        return
    start, turn = transitions[1:]
    _require(start.get('encounter_type') == ('enemy' if node['kind'] == 'event' else node['kind'])
             and _text(start.get('encounter_name')) and type(turn.get('turn_number')) is int
             and turn['turn_number'] == 1 and turn.get('phase') == 'combat',
             'map arrival encounter/turn conflicts with selected room')
    opening = start.get('opening_state')
    _require(isinstance(opening, dict) and _same_json(opening, turn.get('opening_state'))
             and opening.get('screen') == 'combat' and type(opening.get('turn')) is int and opening['turn'] == 1
             and all(k in opening and _same_json(opening[k], v) for k, v in resources.items())
             and not set(opening) & {'run_id', 'floor_id', 'combat_id', 'turn_id', 'observed_at'},
             'opening combat/turn states must agree with resources and source context')
    for key in ('act', 'floor', 'ascension'):
        _require(key not in opening or key in facts and _same_json(opening[key], facts[key]),
                 'opening state conflicts with inspected ' + key)
    baseline = changes.get('zone_baseline')
    if baseline is not None:
        inventory = after['inventory']
        _require(inventory['coverage']['card'] == 'complete'
                 and Counter(baseline.get('deck', [])) == Counter(inventory['current']['card']),
                 'opening zones must use the reviewed actual deck')
        hand = opening.get('hand')
        _require(isinstance(hand, list), 'opening zone review requires observed hand in combat state')
        names = [card.get('name') if isinstance(card, dict) else card for card in hand]
        _require(all(_text(name) for name in names) and Counter(names) == Counter(baseline.get('hand', [])),
                 'opening state hand conflicts with zone baseline')
