"""Reuse a reviewed map route; package one ordinary checked menu step.

Cache entries are planning memory, never evidence that input succeeded. Each
action still needs an inspected image and the session's current input epoch.
No controller, capture, database or model calls occur in this module.
"""
from copy import deepcopy
from math import ceil
import hashlib
from pathlib import Path

from .map_survey import _merge_survey, _require, _text, survey_digest
from .menu_controls import CONTROL_PROFILE, _MAP_SCREENS
from .menu_requests import _inventory, _json, validate_menu_draft
from .menu_results import read_pending

SNAPSHOT_SCHEMA = 'veda.map-travel-snapshot.v1'
CACHE_SCHEMA = 'veda.map-travel-cache.v1'


def _snapshot(value):
    value = _json(value)
    fields = {'schema', 'context', 'inventory', 'resources', 'facts', 'ui'}
    _require(isinstance(value, dict) and fields <= set(value)
             and not set(value) - fields - {'entry_resources'}
             and value['schema'] == SNAPSHOT_SCHEMA, 'map travel snapshot required')
    _require(value['ui'].get('menu_family') == 'map_nodes', 'current selectable map nodes required')
    _inventory(value['inventory'], 'learning')
    # Exercise the existing map contracts, including all siblings and actual
    # reachability, without manufacturing an executable image or authority.
    options = value['ui']['options']
    _require(isinstance(options, list) and options, 'inspect current selectable nodes')
    known = next((o for o in options if o.get('node', {}).get('kind') in _MAP_SCREENS), None)
    if known is None:
        raise ValueError('inspect a selectable node icon; do not invent a room type')
    validate_menu_draft(_draft(value, known['id'], 'Validate current map facts.'), CONTROL_PROFILE)
    return value


def _draft(snapshot, destination, reason):
    selected = next((o for o in snapshot['ui']['options'] if o['id'] == destination), None)
    _require(selected is not None, 'saved destination is not currently selectable')
    node = selected['node']
    _require(node['kind'] in _MAP_SCREENS, 'inspect the selected room icon before entering')
    facts = dict(snapshot['facts'], act=node['act'], floor=node['floor'],
                 current_node_id=destination, node_type=node['kind'])
    resources = snapshot.get('entry_resources', {}).get(destination, snapshot['resources'])
    outcomes = [{'id': screen, 'postconditions': {
        'screen': screen, 'phase': 'result', 'context': 'next_room',
        'resources': deepcopy(resources), 'inventory': 'unchanged',
        'facts': facts, 'allow_changed_facts': []}}
        for screen in sorted(_MAP_SCREENS[node['kind']])]
    post = outcomes[0]['postconditions'] if len(outcomes) == 1 else {'alternatives': outcomes}
    return {'schema': 'veda.menu-draft.v1', 'decision_policy': 'learning',
            **{k: deepcopy(snapshot[k]) for k in ('context', 'inventory', 'resources', 'facts', 'ui')},
            'choice': {'kind': 'map', 'option_ids': [destination], 'postconditions': post},
            'reasoning': reason}


def _inventory_key(inventory):
    # List order and incidental evidence metadata are not new strategic facts.
    return {'current': {k: sorted(v) for k, v in inventory['current'].items()},
            'coverage': inventory['coverage'], 'properties': inventory.get('properties', {})}


def _options_key(snapshot):
    return sorted([o['id'], o['node']['kind'], o['node']['act'], o['node']['floor'],
                   o['node']['reachable']] for o in snapshot['ui']['options'])


def _topology(survey):
    if survey is None:
        return None
    # Additional images of the same graph don't require strategy again.
    return survey_digest({
        'nodes': sorted(({k: n[k] for k in ('node_id', 'row', 'lane', 'kind', 'outgoing_complete')}
                         for n in survey['nodes']), key=lambda n: n['node_id']),
        'edges': sorted((e['from_node_id'], e['to_node_id']) for e in survey['edges']),
        'boss': survey.get('expected_boss', {}).get('name') if survey.get('expected_boss') else None})


def _read_cache(cache, snapshot):
    if cache is None:
        return {'schema': CACHE_SCHEMA, 'run_id': snapshot['context']['run_id'],
                'act': snapshot['facts']['act'], 'survey': None, 'route': None}
    cache = deepcopy(cache)
    _require(isinstance(cache, dict) and set(cache) == {'schema', 'run_id', 'act', 'survey', 'route'}
             and cache['schema'] == CACHE_SCHEMA, 'map travel cache required')
    _require(cache['run_id'] == snapshot['context']['run_id'] and cache['act'] == snapshot['facts']['act'],
             'cache belongs to another run or act; start a new cache for this map')
    return cache


def _merge_views(cache, current, views):
    survey = deepcopy(cache['survey'])
    if survey is not None:
        _require(survey['run_id'] == cache['run_id'] and survey['act'] == cache['act'],
                 'cached survey identity differs')
        survey['current_node_id'] = current
    for envelope in views:
        _require(envelope.get('schema') == 'veda.bound-map-view.v1'
                 and envelope.get('run_id') == cache['run_id'] and envelope.get('act') == cache['act'],
                 'bound view belongs to another run or act')
        if survey is None:
            survey = {'schema': 'veda.map-survey.v1', 'run_id': cache['run_id'], 'act': cache['act'],
                      'current_node_id': current, 'views': []}
        view = envelope['view']
        existing = next((v for v in survey['views'] if v['view_id'] == view['view_id']), None)
        _require(existing is None or existing == view, 'changed view ID; retain the old evidence and correct the survey explicitly')
        if existing is None:
            survey['views'].append(deepcopy(view))
    if survey is None:
        return None, None
    # A mid-run partial archive need not already contain today's current node.
    # It can still inform planning, but cannot support an unseen route edge.
    merged = _merge_survey(survey, verify_sources=True, require_current=False)
    cache['survey'] = survey
    return survey, merged


def _decision(value, snapshot, survey):
    _require(isinstance(value, dict) and {'node_ids', 'reason'} <= set(value)
             and not set(value) - {'node_ids', 'reason', 'reassess'},
             'choose node_ids and reason once; optional reassess thresholds')
    value = deepcopy(value)
    path = value['node_ids']
    _require(isinstance(path, list) and 2 <= len(path) <= 64
             and all(isinstance(n, str) and n.strip() for n in path)
             and len(set(path)) == len(path) and path[0] == snapshot['facts']['current_node_id'],
             'route starts at the actual current node and follows unique nodes')
    _text(value['reason'], 'route reason', 2048)
    _require(path[1] in {o['id'] for o in snapshot['ui']['options']}, 'choose a visible reachable next node')
    edges = {(e['from_node_id'], e['to_node_id']) for e in survey['edges']} if survey else set()
    _require(all(pair in edges for pair in zip(path[1:-1], path[2:])),
             'future route edges need saved inspected map evidence; choose only the next node if future paths are unknown')
    policy = value.setdefault('reassess', {})
    _require(isinstance(policy, dict) and not set(policy) - {'hp_change', 'gold_change', 'hp_thresholds', 'gold_thresholds'},
             'reassess supports HP/gold changes and explicit crossing thresholds')
    policy.setdefault('hp_change', max(1, ceil(snapshot['resources']['max_hp'] * .15)))
    policy.setdefault('gold_change', 50)
    policy.setdefault('hp_thresholds', [])
    policy.setdefault('gold_thresholds', [])
    _require(all(type(policy[k]) is int and policy[k] > 0 for k in ('hp_change', 'gold_change')),
             'reassessment deltas must be positive integers')
    _require(all(isinstance(policy[k], list) and len(policy[k]) <= 16
                 and all(type(n) is int and n >= 0 for n in policy[k]) for k in ('hp_thresholds', 'gold_thresholds')),
             'reassessment thresholds must be bounded nonnegative integer lists')
    return value


def _reasons(route, snapshot, topology):
    baseline, resources = route['baseline'], snapshot['resources']
    reasons = []
    if _inventory_key(snapshot['inventory']) != baseline['inventory']:
        reasons.append('deck_relics_or_potions_changed')
    if resources['max_hp'] != baseline['resources']['max_hp']:
        reasons.append('maximum_health_changed')
    for key in ('hp', 'gold'):
        before, after = baseline['resources'][key], resources[key]
        if abs(after - before) >= route['decision']['reassess'][key + '_change']:
            reasons.append('material_' + key + '_change')
        if any((before < threshold) != (after < threshold)
               for threshold in route['decision']['reassess'][key + '_thresholds']):
            reasons.append(key + '_threshold_crossed')
    if baseline['topology'] != topology:
        reasons.append('map_knowledge_changed')
    if route['last_node'] == snapshot['facts']['current_node_id']:
        if route['options_key'] != _options_key(snapshot):
            reasons.append('selectable_nodes_changed')
        if route['last_context'] != snapshot['context']:
            reasons.append('map_context_changed')
    return reasons


def plan_map(snapshot, cache=None, decision=None, views=()):
    """Return the same destination through focus taps, or request one new choice.

    Strategy-required is work for the playing advisor, not a stop or permission
    request. A full map and boss identity are never prerequisites for this step.
    """
    snapshot = _snapshot(snapshot)
    cache = _read_cache(cache, snapshot)
    current = snapshot['facts']['current_node_id']
    if len(snapshot['ui']['options']) == 1 and decision is None:
        # _snapshot has already checked complete current siblings/reachability.
        # A forced step cannot benefit from archive replay or route analysis.
        wanted = snapshot['ui']['options'][0]['id']
        draft = _draft(snapshot, wanted, 'Only available path: the complete reviewed reachable set has one node.')
        route = cache['route']
        reused = False
        if route is not None:
            path = route['decision']['node_ids']
            if current in path[route['cursor']:] and path.index(current) + 1 < len(path):
                index = path.index(current)
                if path[index + 1] == wanted:
                    route.update(cursor=index, last_node=current, last_context=deepcopy(snapshot['context']),
                                 options_key=_options_key(snapshot))
                    reused = True
        # Keep the strategic baseline unchanged: changed HP/inventory must
        # still trigger reconsideration at the next actual fork.
        return {'status': 'planned', 'destination': wanted, 'forced_move': True,
                'reused_route': reused, 'draft': draft, 'cache': cache,
                'controller_input_sent': False, 'survey_required': False,
                'expected_boss': None, 'deferred_views': len(views),
                'fast_path': {'kind': 'single_reachable_node',
                              'decision_budget_seconds': 10,
                              'next_step': 'activate_once_then_verify_room'}}
    _, survey = _merge_views(cache, current, views)
    topology = _topology(survey)
    route = cache['route']
    reasons = ['no_saved_route'] if route is None else _reasons(route, snapshot, topology)
    if decision is not None:
        decision = _decision(decision, snapshot, survey)
        route = {'decision': decision, 'cursor': 0, 'last_node': current,
                 'last_context': deepcopy(snapshot['context']), 'options_key': _options_key(snapshot),
                 'baseline': {'inventory': _inventory_key(snapshot['inventory']),
                              'resources': deepcopy(snapshot['resources']), 'topology': topology}}
        cache['route'], reasons = route, []
    if route is not None:
        path = route['decision']['node_ids']
        if current not in path[route['cursor']:]:
            reasons.append('off_saved_route')
        elif path.index(current) == len(path) - 1:
            reasons.append('saved_route_complete')
        else:
            index = path.index(current)
            wanted = path[index + 1]
            if wanted not in {o['id'] for o in snapshot['ui']['options']}:
                reasons.append('saved_destination_unavailable')
    common = {'cache': cache, 'controller_input_sent': False,
              'survey_required': False, 'expected_boss': survey.get('expected_boss') if survey else None}
    if reasons:
        return {'status': 'strategy_required', 'reasons': reasons,
                'options': snapshot['ui']['options'], **common,
                'instruction': 'Choose a reachable next node once, or extend the saved route. Inspect more map only if it could change that choice; incomplete future coverage is not a stop.'}
    route.update(cursor=index, last_node=current, last_context=deepcopy(snapshot['context']),
                 options_key=_options_key(snapshot))
    draft = _draft(snapshot, wanted, route['decision']['reason'])
    validate_menu_draft(draft, CONTROL_PROFILE)
    return {'status': 'planned', 'destination': wanted, 'reused_route': decision is None,
            'draft': draft, **common}


def focus_snapshot(snapshot, focused_id, *, unchanged=False):
    """Caller declares it inspected the post-input frame; never assume success."""
    _require(unchanged is True, 'confirm other map facts/resources/inventory/options are unchanged in the inspected frame')
    value = _snapshot(snapshot)
    _require(focused_id in {o['id'] for o in value['ui']['options']}, 'actual focus must be a visible selectable node')
    value['ui']['focused_id'] = focused_id
    return value


def focus_result(session, action_id, focused_id, *, unchanged=False, observed_result):
    _require(unchanged is True, 'inspect and confirm unchanged map facts, resources and inventory')
    pending, _ = read_pending(session, action_id)
    _require(pending['proposal']['step_kind'] == 'focus'
             and pending['request']['observation']['ui'].get('menu_family') == 'map_nodes',
             'pending map focus action required')
    return {'schema': 'veda.menu-result.v1', 'action_id': action_id,
            'resources': 'unchanged', 'inventory': 'unchanged', 'facts': 'unchanged',
            'result': {'kind': 'focus', 'focused_id': focused_id}, 'observed_result': observed_result}


def snapshot_from_result(packet, session):
    """Recover the exact sealed observation, not the old action's prediction."""
    from .shop import snapshot_from_result as verified_snapshot
    value = verified_snapshot(packet, session)
    value['schema'] = SNAPSHOT_SCHEMA
    return _snapshot(value)


def last_snapshot(session):
    from .map_survey import read_json
    path = Path(session)
    state = read_json(path/'state.json' if path.is_dir() else path)
    last = state.get('last_verified', {})
    _require(last.get('map_after') is not None,
             'no retained verified map result; use --after-result or an inspected snapshot')
    return snapshot_from_result({'operation': 'verify', 'action_id': last['action_id'],
                                 'after': last['map_after']}, session)


def reuse_verified_focus(snapshot, session, capture):
    """An unchanged exact verified frame must not reintroduce an older focus.

    A different capture or explicit focus override remains the reviewer's
    responsibility. Reuse only this one field; never erase new classifications,
    inventory uncertainty, entry forecasts or route choices.
    """
    from .map_survey import read_json
    path = Path(session)
    state = read_json(path/'state.json' if path.is_dir() else path)
    last = state.get('last_verified', {})
    if not last.get('map_after') or hashlib.sha256(Path(capture).read_bytes()).hexdigest() != last['source']['sha256']:
        return snapshot
    verified = last_snapshot(session)
    if (snapshot['context'] != verified['context']
            or snapshot['ui']['choice_id'] != verified['ui']['choice_id']
            or snapshot['facts'].get('current_node_id') != verified['facts'].get('current_node_id')
            or {o['id'] for o in snapshot['ui']['options']} != {o['id'] for o in verified['ui']['options']}):
        return snapshot
    value = deepcopy(snapshot)
    value['ui']['focused_id'] = verified['ui']['focused_id']
    return value


def load_session_cache(session, snapshot):
    from .map_survey import read_json
    from .shop_results import session_directory
    path = session_directory(session)/'map-packets'/('cache-act-' + str(snapshot['facts']['act']) + '.json')
    return _read_cache(read_json(path), snapshot) if path.exists() else None


def save_cache(cache, *, output=None, session=None):
    """Content-address immutable versions; atomically maintain private latest cache."""
    import json
    import os
    from uuid import uuid4
    from .map_survey import read_json
    from .shop_results import session_directory
    data = (json.dumps(cache, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if output is None:
        _require(session is not None, 'cache output or session required')
        directory = session_directory(session)/'map-packets'; directory.mkdir(exist_ok=True)
        output = directory/('cache-' + hashlib.sha256(data).hexdigest() + '.json')
    output = Path(output)
    try:
        with output.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        _require(read_json(output) == cache, 'cache path contains another version; omit cache-output for generated paths')
    if session is not None:
        directory = session_directory(session)/'map-packets'; directory.mkdir(exist_ok=True)
        latest = directory/('cache-act-' + str(cache['act']) + '.json')
        temp = directory/('cache-latest-' + uuid4().hex + '.tmp')
        try:
            with temp.open('xb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, latest)
        finally:
            temp.unlink(missing_ok=True)
    return str(output.resolve())
