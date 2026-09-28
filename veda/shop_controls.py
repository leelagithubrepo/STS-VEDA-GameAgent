"""Merchant controls, with observed outcomes for directional focus predictions."""
from copy import deepcopy

from .choice_execution import _require, _text, _same_json

FAMILIES = {'shop_entry', 'shop_stock', 'shop_exit', 'shop_remove'}
SELECT_RULE = 'ps5-shop-focused-select-v1'
FOCUS_RULE = 'ps5-shop-directional-focus-v1'
RULES = {SELECT_RULE, FOCUS_RULE}


def checked_ui(obs):
    ui = obs['ui']
    _require(ui.get('menu_family') in FAMILIES and ui.get('control_layout') == 'ps5_default'
             and obs['context']['combat_id'] is None and obs['context']['turn_id'] is None,
             'merchant controls require an inspected noncombat merchant menu')
    _require(ui['screen'] == ('selection' if ui['menu_family'] == 'shop_remove' else 'shop')
             and ui['phase'] in {'choose', 'confirm'} and ui['required_count'] == 1,
             'merchant controls need one actionable choice, not a recorded result phase')
    _require(obs['facts'].get('node_type') == 'merchant', 'merchant identity must be reviewed')
    if ui['phase'] == 'confirm':
        _require(ui['menu_family'] == 'shop_remove' and ui.get('confirm') is not None
                 and len(ui['selected_ids']) == 1 and ui['pending_ids'] == ui['selected_ids'],
                 'card removal confirmation needs the actual selected card and visible confirm hint')
    else:
        _require(not ui['selected_ids'] and not ui['pending_ids'], 'unresolved merchant selection')
    return ui


def directions(ui):
    """Nearest visible directional candidate, corrected by observed transitions.

    This predicts focus only. It never treats a directional tap as a purchase.
    Positions omit global Leave/Skip shortcuts and sold/unavailable items.
    """
    points = ui.get('shop_positions', [])
    _require(isinstance(points, list) and len(points) <= 128, 'bounded reviewed shop positions required')
    positions = {}
    for p in points:
        _require(isinstance(p, dict) and set(p) == {'id', 'x', 'y'}
                 and p['id'] in ui['order'] and p['id'] not in positions
                 and all(type(p[k]) is int and 0 <= p[k] <= 32768 for k in ('x', 'y')),
                 'actual unique visible shop item positions required')
        positions[p['id']] = (p['x'], p['y'])
    _require(len(set(positions.values())) == len(positions), 'shop positions overlap')
    edges = {}
    for source, (x, y) in positions.items():
        for button, (dx, dy) in {'left': (-1, 0), 'right': (1, 0), 'up': (0, -1), 'down': (0, 1)}.items():
            candidates = []
            for target, (tx, ty) in positions.items():
                forward, side = (tx-x)*dx + (ty-y)*dy, abs((ty-y)*dx - (tx-x)*dy)
                if forward > 0:
                    # Strongly prefer aligned items without assuming a regular grid.
                    candidates.append((side / forward, forward*forward + side*side, target))
            if candidates:
                edges[(source, button)] = min(candidates)[2]
    history = ui.get('shop_navigation', [])
    _require(isinstance(history, list) and len(history) <= 512, 'bounded observed shop navigation required')
    for row in history:
        _require(isinstance(row, dict) and set(row) == {'from', 'button', 'to'}
                 and row['from'] in positions and row['to'] in positions
                 and row['button'] in {'up', 'down', 'left', 'right'}, 'observed shop focus transition required')
        key = (row['from'], row['button'])
        edges.pop(key, None)
        if row['to'] != row['from']:
            edges[key] = row['to']
    return edges


def bind(obs):
    from .menu_controls import _binding
    ui = checked_ui(obs)
    if ui['phase'] == 'confirm':
        return
    for option in ui['options']:
        if option.get('role') in {'open', 'skip', 'leave', 'proceed'}:
            _require(option.get('shortcut') is not None, 'merchant boundary uses its actual visible button hint')
        elif option['enabled'] and option.get('activate') is None:
            option['activate'] = _binding(obs, 'cross', 'activate:' + option['id'], SELECT_RULE)
    ui['navigation'] = [dict(_binding(obs, button, f'focus:{source}->{target}', FOCUS_RULE),
                             **{'from': source, 'to': target})
                        for (source, button), target in directions(ui).items()]


def validate_binding(binding, obs, meaning, navigation):
    ui = checked_ui(obs)
    if binding['evidence']['rule_id'] == SELECT_RULE:
        _require(not navigation and binding['button'] == 'cross'
                 and any(meaning == 'activate:' + o['id'] and o.get('role') not in {'open', 'skip', 'leave', 'proceed'}
                         for o in ui['options']), 'merchant select only activates its focused stock/card option')
    else:
        source, target = binding.get('from'), binding.get('to')
        _require(navigation and directions(ui).get((source, binding['button'])) == target
                 and meaning == f'focus:{source}->{target}', 'shop focus uses one directional prediction or observed transition')


def record_focus(ui, button, target):
    ui = deepcopy(ui)
    _require(target in {p['id'] for p in ui.get('shop_positions', [])}, 'inspect the actual focused shop item')
    source = ui['focused_id']
    history = [h for h in ui.get('shop_navigation', []) if (h['from'], h['button']) != (source, button)]
    history.append({'from': source, 'button': button, 'to': target})
    ui['shop_navigation'] = history
    ui['focused_id'] = target
    return ui


def verify_focus(proposal, before, after):
    """Learn actual shop focus without weakening purchases or other screens."""
    _require(before['ui'].get('menu_family') in {'shop_stock', 'shop_remove'}
             and proposal['step_kind'] == 'focus', 'only merchant directional focus can learn this transition')
    for key in ('context', 'resources', 'inventory_digest', 'facts'):
        _require(_same_json(before[key], after[key]), 'shop focus changed gameplay state')
    target = after['ui']['focused_id']
    expected = record_focus(before['ui'], proposal['command']['buttons'][0], target)
    def semantic(ui):
        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items() if k != 'evidence'}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value
        return clean({k: v for k, v in ui.items() if k != 'navigation'})
    _require(semantic(expected) == semantic(after['ui']), 'shop focus changed stock, prices, hints or unrelated UI')
    _require(before['frame']['image_sha256'] != after['frame']['image_sha256']
             or before['ui']['focused_id'] == target, 'identical pixels cannot prove changed shop focus')
    outcome = after['review'].get('outcome', {})
    _require(outcome.get('action_id') == proposal['action_id']
             and outcome.get('before_frame_id') == before['frame']['frame_id']
             and outcome.get('before_sha256') == before['frame']['image_sha256']
             and outcome.get('choice_id') == proposal['choice']['choice_id']
             and outcome.get('option_ids') == proposal['choice']['option_ids']
             and _text(outcome.get('observed_result')), 'exact observed shop focus outcome required')
    return {'step_verified': True, 'choice_complete': False,
            'shop_focus_transition': {'from': before['ui']['focused_id'],
                                      'button': proposal['command']['buttons'][0], 'to': target},
            'observed_mismatches': [] if target == proposal['expectation']['focused_id'] else [{
                'field': 'shop_focus', 'expected': proposal['expectation']['focused_id'], 'observed': target}]}
