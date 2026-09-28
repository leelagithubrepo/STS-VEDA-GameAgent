"""Compact observed merchant outcomes, with canonical result paths. No input."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .map_survey import read_json
from .menu_requests import _require, _ui
from .menu_results import read_pending, _unbound_ui
from .shop_observation import stock_ui
from .shop import snapshot_from_result


def session_directory(path):
    path = Path(path).resolve()
    return path if path.is_dir() else path.parent


def output_path(session, *, action_id=None):
    directory = session_directory(session)/'shop-packets'
    directory.mkdir(exist_ok=True)
    # Do not put caller-supplied action IDs in a filesystem path.
    prefix = 'result-' if action_id else 'action-'
    return directory/(prefix + uuid4().hex + '.json')


def last_result(session):
    state = read_json(session_directory(session)/'state.json')
    _require(not state.get('pending'), 'verify the pending result before reusing it')
    directory = session_directory(session)/'shop-packets'
    matches = []
    for path in directory.glob('result-*.json'):
        packet = read_json(path)
        if packet.get('action_id') == state.get('last_verified', {}).get('action_id'):
            try:
                snapshot_from_result(packet, session)
            except (ValueError, KeyError, TypeError):
                continue
            matches.append(packet)
    _require(matches, 'no sealed verified merchant result in the session; supply an inspected snapshot')
    return matches[0]


def observed_result(session, kind, *, note, unchanged=False, focused_id=None,
                    hint=None, stock=None, actual=None):
    """Expand an explicit observation, never a predicted successful purchase."""
    state = read_json(session_directory(session)/'state.json')
    action_id = (state.get('pending') or {}).get('action_id')
    pending, _ = read_pending(session, action_id)
    before = pending['request']
    ui = before['observation']['ui']
    _require(ui.get('menu_family', '').startswith('shop_'), 'pending merchant action required')
    result = {'schema': 'veda.menu-result.v1', 'action_id': action_id,
              'resources': 'unchanged', 'inventory': 'unchanged', 'facts': 'unchanged',
              'observed_result': note}
    if kind != 'purchased':
        _require(unchanged is True, 'inspect and declare unchanged resources, inventory and other facts')
    if kind == 'focus':
        _require(focused_id is not None and hint is None and stock is None and actual is None,
                 'focus result needs only the actual focused slot')
        result['result'] = {'kind': 'focus', 'focused_id': focused_id}
    elif kind == 'confirmation':
        _require(focused_id is not None and hint is not None and stock is None and actual is None,
                 'confirmation needs actual selected item and its visible confirm hint')
        result['result'] = {'kind': 'shop_confirmation', 'selected_id': focused_id, 'confirm_hint': hint}
    elif kind == 'opened':
        _require(stock is not None and actual is None and hint is None and focused_id is None,
                 'opened result needs inspected stock slots')
        result['result'] = {'kind': 'menu', 'ui': stock_ui(stock, before['observation']['resources']['gold'])}
    elif kind == 'left':
        _require(hint is not None and actual is None and stock is None and focused_id is None,
                 'left result needs the actual separate Proceed hint')
        result['result'] = {'kind': 'menu', 'ui': {'menu_family': 'shop_exit', 'choice_id': 'merchant-proceed',
            'focused_id': 'proceed', 'options': [{'id': 'proceed', 'label': 'Proceed', 'enabled': True,
                'costs': {}, 'role': 'proceed', 'shortcut_hint': hint}]}}
    elif kind == 'map':
        _require(hint is None and actual is None and stock is None and focused_id is None,
                 'map result has no merchant control fields')
        result['result'] = {'kind': 'menu', 'ui': {'screen': 'map', 'phase': 'result',
            'choice_id': 'merchant-departed', 'layout_id': 'map-result', 'focused_id': None, 'options': []}}
    elif kind == 'purchased':
        # --actual is a small observed delta, not the saved buying decision.
        required = {'gold', 'deck_size', 'acquired', 'sold_id', 'focused_id', 'others_unchanged'}
        _require(isinstance(actual, dict) and set(actual) == required and actual['others_unchanged'] is True
                 and type(actual['gold']) is int and actual['gold'] >= 0
                 and type(actual['deck_size']) is int and actual['deck_size'] >= 0
                 and isinstance(actual['acquired'], dict) and set(actual['acquired']) == {'kind', 'name'}
                 and not unchanged and stock is None and hint is None and focused_id is None,
                 'purchase needs actual gold/deck count, acquired item, sold slot, focus and unchanged remaining facts')
        _require(pending['proposal']['step_kind'] == 'commit' and ui.get('phase') == 'confirm'
                 and before['choice']['option_ids'] == [actual['sold_id']], 'exact confirmed purchase required')
        option = next(o for o in ui['options'] if o['id'] == actual['sold_id'])
        acquired = actual['acquired']
        _require(acquired == {'kind': option['role'], 'name': option['offer']['name']},
                 'observed acquired item differs; use an explicit full menu result for an unexpected outcome')
        result['resources'] = dict(before['observation']['resources'], gold=actual['gold'], deck_size=actual['deck_size'])
        result['inventory'] = deepcopy(before['inventory'])
        result['inventory']['current'][acquired['kind']].append(acquired['name'])
        next_ui = _unbound_ui(ui)
        next_ui.pop('control_layout', None)
        next_ui.update(phase='choose', selected_ids=[], pending_ids=[], focused_id=actual['focused_id'])
        next_ui['options'] = [o for o in next_ui['options'] if o['id'] != actual['sold_id']]
        next_ui.pop('order', None)
        for o in next_ui['options']:
            o['enabled'] = o['costs'].get('gold', 0) <= actual['gold']
        next_ui['shop_positions'] = [p for p in next_ui.get('shop_positions', []) if p['id'] != actual['sold_id']]
        # A sold slot can alter layout transitions; do not carry stale edges.
        next_ui.pop('shop_navigation', None)
        result['result'] = {'kind': 'menu', 'ui': _ui(next_ui)}
    else:
        raise ValueError('unsupported merchant result')
    return result
