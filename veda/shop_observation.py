"""Normalize explicitly inspected merchant slots; this is not pixel recognition."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from .menu_requests import _require, _text


@lru_cache(maxsize=1)
def card_facts():
    data = json.loads((Path(__file__).resolve().parents[1]/'data/spire_reference/entities.json').read_text())
    return {e['name']: e['facts'] for e in data['entries'] if e['kind'] == 'card'}


def validate_stock(ui):
    if ui.get('menu_family') != 'shop_stock':
        return
    for option in ui['options']:
        if option.get('role') == 'card':
            name = option.get('offer', {}).get('name')
            facts = card_facts().get(name, {})
            _require(facts.get('type') not in {'status', 'curse'},
                     f'{name} is a status/curse preview, not a merchant stock slot; inspect actual focus and exclude the preview')
            upgraded = option.get('offer', {}).get('upgraded')
            _require(upgraded is None or type(upgraded) is bool and upgraded == name.endswith('+'),
                     'card upgrade label conflicts with its inspected upgrade state')


def stock_ui(observed, gold, *, choice_id='merchant-stock'):
    """Only priced shop slots become choices; preview panels remain annotations."""
    _require(isinstance(observed, dict) and {'focused_id', 'items', 'leave_hint'} <= set(observed)
             and not set(observed) - {'focused_id', 'items', 'leave_hint', 'previews'},
             'observed stock needs focused_id, items and the visible leave_hint')
    _require(isinstance(observed['items'], list) and 1 <= len(observed['items']) <= 128,
             'bounded inspected stock slots required')
    options, positions = [], []
    for item in observed['items']:
        _require(isinstance(item, dict) and {'id', 'role', 'name', 'price', 'x', 'y'} <= set(item)
                 and not set(item) - {'id', 'role', 'name', 'price', 'x', 'y', 'upgraded'}
                 and item['role'] in {'card', 'relic', 'potion', 'remove_service'}
                 and _text(item['id']) and (item['name'] is None or _text(item['name']))
                 and type(item['price']) is int and item['price'] >= 0,
                 'each real merchant slot needs an identity, own visible price and position')
        option = {'id': item['id'], 'role': item['role'], 'label': item['name'] or 'Unidentified '+item['role'],
                  'enabled': item['price'] <= gold, 'costs': {'gold': item['price']}}
        if item['role'] != 'remove_service':
            option['offer'] = {'name': item['name']}
            if 'upgraded' in item:
                option['offer']['upgraded'] = item['upgraded']
        options.append(option)
        positions.append({k: item[k] for k in ('id', 'x', 'y')})
    _require(observed['focused_id'] in {o['id'] for o in options}, 'actual focus must be a stock slot, not a preview panel')
    options.append({'id': 'leave', 'label': 'Leave', 'role': 'leave', 'enabled': True,
                    'costs': {}, 'shortcut_hint': deepcopy(observed['leave_hint'])})
    ui = {'menu_family': 'shop_stock', 'choice_id': choice_id, 'focused_id': observed['focused_id'],
          'phase': 'choose', 'options': options, 'shop_positions': positions}
    validate_stock(ui)
    return ui


def shopping_notes(snapshot):
    """Short local facts for one buying comparison, never a purchase ranking."""
    notes = []
    gold = snapshot['resources']['gold']
    for item in snapshot['ui']['options']:
        price = item['costs'].get('gold', gold+1)
        _require(type(price) is int and price >= 0, 'merchant price must be an inspected nonnegative integer')
        if item.get('role') not in {'card', 'relic', 'potion'} or price > gold:
            continue
        name = item.get('offer', {}).get('name')
        if not name or 'unidentified' in name.casefold():
            notes.append('Affordable '+item['id']+' is unidentified; do not rank it as low value without inspecting it.')
        facts = card_facts().get(name, {})
        if facts.get('base', {}).get('exhaust') is True:
            notes.append(str(name)+' exhausts; do not assume repeated plays in the same combat.')
    return notes
