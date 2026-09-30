"""Compact observed combat loot outcomes. Never observes pixels or sends input."""
from copy import deepcopy
from uuid import uuid4

from .map_survey import read_json
from .menu_requests import _require
from .menu_results import read_pending
from .loot import snapshot_from_result
from .shop_results import session_directory


def output_path(session, *, action_id=None):
    directory = session_directory(session)/'loot-packets'
    directory.mkdir(exist_ok=True)
    return directory/(('result-' if action_id else 'action-') + uuid4().hex + '.json')


def last_result(session):
    state = read_json(session_directory(session)/'state.json')
    _require(not state.get('pending'), 'verify the pending result before reusing it')
    for path in (session_directory(session)/'loot-packets').glob('result-*.json'):
        packet = read_json(path)
        if packet.get('action_id') == state.get('last_verified', {}).get('action_id'):
            try:
                snapshot_from_result(packet, session)
            except (ValueError, KeyError, TypeError):
                continue
            return packet
    raise ValueError('no sealed verified loot result; use --after-result or an inspected snapshot')


def observed_result(session, kind, *, note, unchanged=False, focused_id=None,
                    hint=None, ui=None, actual=None, no_op_reconciliation=False):
    """Expand actual deltas, never the predicted result of a controller tap.

    ``unchanged`` explicitly attests to inspected resources/inventory/facts.
    Changed rows and card offers must be supplied as the actual visible UI.
    """
    state = read_json(session_directory(session)/'state.json')
    action_id = (state.get('pending') or {}).get('action_id')
    pending, _ = read_pending(session, action_id)
    before = pending['request']
    old_ui = before['observation']['ui']
    _require(old_ui.get('menu_family') in {'loot_rewards', 'loot_cards'}, 'pending loot action required')
    result = {'schema': 'veda.menu-result.v1', 'action_id': action_id,
              'resources': 'unchanged', 'inventory': 'unchanged', 'facts': 'unchanged',
              'observed_result': note}
    if kind in {'focus', 'confirmation', 'offers', 'returned', 'map'}:
        _require(unchanged is True and actual is None, 'inspect and declare unchanged resources, inventory and facts')
    else:
        _require(not unchanged, 'changed loot needs an observed delta, not --unchanged')
    if kind == 'focus':
        _require(focused_id is not None and hint is None, 'focus needs the actual focused option')
        if ui is None:
            result['result'] = {'kind': 'focus', 'focused_id': focused_id}
        else:
            # A delivered directional tap can expose a refreshed reward row
            # (for example a card offer replaced by the live UI) without
            # selecting anything. Preserve the pending choice while carrying
            # the complete, freshly reviewed menu forward.
            _require(old_ui.get('menu_family') == ui.get('menu_family') == 'loot_cards'
                     and ui.get('focused_id') == focused_id
                     and ui.get('screen') in {'reward', 'card_reward'}
                     and ui.get('phase') == 'choose'
                     and isinstance(ui.get('options'), list),
                     'actual loot-card menu and focus required for focus reobservation')
            result['result'] = {'kind': 'menu', 'ui': deepcopy(ui)}
    elif kind == 'confirmation':
        _require(focused_id is not None and hint is not None and ui is None,
                 'confirmation needs actual selected card and visible confirm hint')
        result['result'] = {'kind': 'loot_confirmation', 'selected_id': focused_id, 'confirm_hint': hint}
    elif kind in {'offers', 'returned'}:
        expected = 'loot_cards' if kind == 'offers' else 'loot_rewards'
        _require(isinstance(ui, dict) and ui.get('menu_family') == expected
                 and hint is None and focused_id is None, 'supply the actual new reward UI and focus')
        result['result'] = {'kind': 'menu', 'ui': deepcopy(ui)}
        if no_op_reconciliation:
            _require(kind == 'returned' and unchanged is True,
                     'no-op reconciliation is only for an inspected returned menu')
            result['no_op_reconciliation'] = True
    elif kind == 'map':
        _require(ui is None and hint is None and focused_id is None, 'map result has no reward control fields')
        result['result'] = {'kind': 'menu', 'ui': {'screen': 'map', 'phase': 'result',
            'choice_id': 'loot-departed', 'layout_id': 'map-result', 'focused_id': None, 'options': []}}
    elif kind in {'gold', 'acquired'}:
        _require(pending['proposal']['step_kind'] == 'commit'
                 and isinstance(ui, dict) and ui.get('menu_family') == 'loot_rewards'
                 and hint is None and focused_id is None, 'committed collection and actual remaining reward UI required')
        _require(isinstance(actual, dict) and actual.get('others_unchanged') is True,
                 'supply actual changed values and attest to inspected unchanged remaining facts')
        option = next(o for o in old_ui['options'] if [o['id']] == before['choice']['option_ids'])
        if kind == 'gold':
            _require(set(actual) == {'gold', 'others_unchanged'} and option.get('role') == 'gold'
                     and type(actual['gold']) is int and actual['gold'] >= 0, 'actual gold total required')
            result['resources'] = dict(before['observation']['resources'], gold=actual['gold'])
        else:
            _require(set(actual) == {'acquired', 'deck_size', 'others_unchanged'}
                     and actual['acquired'] == {'kind': option.get('role'), 'name': option.get('reward', {}).get('name')}
                     and option.get('role') in {'card', 'relic', 'potion'}
                     and type(actual['deck_size']) is int and actual['deck_size'] >= 0,
                     'actual acquired item and deck count required; unexpected outcomes use a full menu result')
            if option['role'] == 'card':
                _require(old_ui.get('phase') == 'confirm', 'card must be observed acquired after its confirmation')
            result['resources'] = dict(before['observation']['resources'], deck_size=actual['deck_size'])
            result['inventory'] = deepcopy(before['inventory'])
            result['inventory']['current'][option['role']].append(actual['acquired']['name'])
        result['result'] = {'kind': 'menu', 'ui': deepcopy(ui)}
    else:
        raise ValueError('unsupported loot result')
    return result
