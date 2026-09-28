"""Merchant regression: fake controller and private DB, no live access."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_map_travel
from tests.test_menu_requests import make_capture
from veda.shop import SCHEMA, plan_shop, resolve_snapshot, session_context, snapshot_from_result
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_results import write_menu_result


def boundary(role, button, label):
    return {'id': role, 'label': label, 'enabled': True, 'costs': {}, 'role': role,
            'shortcut_hint': {'button': button, 'hint_text': button + ' ' + label}}


def ui(family='shop_stock'):
    if family == 'shop_entry':
        options = [boundary('open', 'cross', 'Merchant'), boundary('skip', 'triangle', 'Skip Merchant')]
    elif family == 'shop_exit':
        options = [boundary('proceed', 'triangle', 'Proceed')]
    else:
        options = [{'id': name, 'label': name, 'enabled': True, 'costs': {'gold': cost},
                    'role': 'card', 'offer': {'name': name}}
                   for name, cost in [('Wild Strike', 50), ('Heavy Blade', 27), ('Flex', 48), ('Disarm', 74)]]
        options.append(boundary('leave', 'circle', 'Leave'))
    result = {'menu_family': family, 'choice_id': family + '-floor3',
              'focused_id': options[0]['id'], 'options': options}
    if family == 'shop_stock':
        result['shop_positions'] = [{'id': o['id'], 'x': i * 250 + 200, 'y': 200}
                                    for i, o in enumerate(options[:-1])]
    return result


def choose(value, option, reason='Synthetic reviewed shopping decision.'):
    preview = plan_shop(value)
    key = preview.get('decision_key', preview.get('decision', {}).get('decision_key'))
    return {'option_id': option, 'reason': reason, 'decision_key': key}


class ShopTests(unittest.TestCase):
    def setUp(self):
        case = test_map_travel.MapTravelAdapterTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.f = case.flow
        with patch('veda.telemetry_database._now', return_value=(self.f.now-timedelta(seconds=30)).isoformat()):
            self.f.floor = self.f.db.record_floor(run_id=self.f.run, act=1, floor=3, node_type='merchant', outcome=None)
        self.f.context['floor_id'] = self.f.floor
        def capture(color=None):
            self.f.serial += 1; self.f.now += timedelta(seconds=1)
            return make_capture(self.f.root, self.f.now-timedelta(milliseconds=200),
                                color=color or (self.f.serial, 40, 70), index=format(self.f.serial % 16, 'x'))
        self.f.capture = capture
        self.f.facts = dict(act=1, floor=3, current_node_id='shop-node', node_type='merchant', potion_capacity=3)
        self.f.resources['gold'] = 113
        self.value = {'schema': SCHEMA, 'context': self.f.context, 'inventory': deepcopy(self.f.inventory),
                      'resources': deepcopy(self.f.resources), 'facts': self.f.facts, 'ui': ui()}

    def verify(self, prepared, actual_ui=None, *, resources='unchanged', inventory='unchanged', focus=None, color=None):
        f = self.f
        draft = {'schema': 'veda.menu-result.v1', 'action_id': prepared['action_id'],
                 'resources': resources, 'inventory': inventory, 'facts': 'unchanged',
                 'result': {'kind': 'focus', 'focused_id': focus} if focus else {'kind': 'menu', 'ui': actual_ui},
                 'observed_result': 'Actual synthetic merchant outcome inspected; no live input.'}
        capture = f.capture(color)
        output = f.root/f'shop-result-{f.serial}.json'
        write_menu_result(draft, session=f.session.path, action_id=prepared['action_id'], capture=capture,
            reviewer='Fixture', evidence_note='Exact synthetic merchant result.', reviewed=True,
            control_profile=CONTROL_PROFILE, output=output, now=f.now)
        packet = json.loads(output.read_text())
        answer = f.session.handle(packet)
        self.assertEqual('verified', answer['status'], answer)
        return packet, answer, output

    def test_stock_requires_strategy_and_never_leaves_because_mapping_missing(self):
        result = plan_shop(self.value)
        self.assertEqual('strategy_required', result['status'])
        self.assertNotIn('draft', result)
        planned = plan_shop(self.value, choose(self.value, 'Disarm'))
        self.assertEqual(['Disarm'], planned['draft']['choice']['option_ids'])
        self.assertEqual(39, planned['draft']['choice']['postconditions']['resources']['gold'])

    def test_full_open_focus_purchase_leave_proceed_flow_records_actual_inventory(self):
        f = self.f
        value = deepcopy(self.value); value['ui'] = ui('shop_entry')
        prepared = f.prepare(plan_shop(value)['draft'])
        packet, _, _ = self.verify(prepared, ui())
        value = snapshot_from_result(packet, f.session.path)
        plan = plan_shop(value, choose(value, 'Disarm'))
        saved = plan['decision']
        prepared = f.prepare(plan['draft'])
        # Predicted Heavy Blade, actually Flex: learn rather than pretending.
        packet, _, _ = self.verify(prepared, focus='Flex')
        value = snapshot_from_result(packet, f.session.path)
        self.assertEqual([{'from': 'Wild Strike', 'button': 'right', 'to': 'Flex'}], value['ui']['shop_navigation'])
        prepared = f.prepare(plan_shop(value, saved)['draft'])
        packet, _, _ = self.verify(prepared, focus='Disarm')
        value = snapshot_from_result(packet, f.session.path)
        prepared = f.prepare(plan_shop(value, saved)['draft'])
        confirmation = deepcopy(value['ui'])
        confirmation.update(phase='confirm', selected_ids=['Disarm'], pending_ids=['Disarm'],
                            confirm_hint={'button': 'cross', 'hint_text': 'Cross Confirm'})
        packet, _, _ = self.verify(prepared, confirmation)
        self.assertFalse(packet['telemetry'])
        value = snapshot_from_result(packet, f.session.path)
        prepared = f.prepare(plan_shop(value, saved)['draft'])
        actual_inventory = deepcopy(value['inventory']); actual_inventory['current']['card'].append('Disarm')
        resources = dict(value['resources'], gold=39, deck_size=value['resources']['deck_size']+1)
        stock = ui(); stock['options'] = [o for o in stock['options'] if o['id'] != 'Disarm']
        stock['shop_positions'] = [p for p in stock['shop_positions'] if p['id'] != 'Disarm']
        packet, _, _ = self.verify(prepared, stock, resources=resources, inventory=actual_inventory)
        self.assertEqual(['Disarm'], [e['item'] for e in packet['telemetry']['inventory_events']])
        value = snapshot_from_result(packet, f.session.path)
        with self.assertRaisesRegex(ValueError, 'changed'):
            plan_shop(value, saved)
        prepared = f.prepare(plan_shop(value, choose(value, 'leave', 'Shopping complete for this fixture.'))['draft'])
        packet, _, _ = self.verify(prepared, ui('shop_exit'))
        value = snapshot_from_result(packet, f.session.path)
        prepared = f.prepare(plan_shop(value)['draft'])
        after_map = {'screen': 'map', 'phase': 'result', 'choice_id': 'after-shop',
                     'layout_id': 'map-result', 'options': [], 'focused_id': None}
        self.verify(prepared, after_map)
        self.assertEqual(['cross', 'right', 'right', 'cross', 'cross', 'circle', 'triangle'], [x['buttons'][0] for x in f.controller.inputs])
        self.assertIsNone(f.session.state['pending'])
        self.assertIn('Disarm', f.db.inventory_ledger(run_id=f.run)['current']['card'])

    def test_focus_only_cannot_invent_purchase_or_changed_gold(self):
        prepared = self.f.prepare(plan_shop(self.value, choose(self.value, 'Disarm'))['draft'])
        with self.assertRaisesRegex(ValueError, 'gameplay state'):
            self.verify(prepared, focus='Heavy Blade', resources=dict(self.value['resources'], gold=39))
        self.assertEqual(1, len(self.f.controller.inputs))
        self.assertEqual('attempted', self.f.session.state['pending']['status'])

    def test_observed_noop_focus_is_recorded_and_that_direction_not_repeated(self):
        prepared = self.f.prepare(plan_shop(self.value, choose(self.value, 'Disarm'))['draft'], color=(9, 9, 9))
        packet, _, _ = self.verify(prepared, focus='Wild Strike', color=(9, 9, 9))
        value = snapshot_from_result(packet, self.f.session.path)
        self.assertEqual('Wild Strike', value['ui']['shop_navigation'][0]['to'])
        # In this single-row fixture no other route exists; return review work,
        # never reissue the known no-effect right tap or auto-leave.
        with self.assertRaisesRegex(ValueError, 'no reviewed path'):
            plan_shop(value, choose(value, 'Disarm'))
        self.assertEqual(1, len(self.f.controller.inputs))

    def test_paid_purchase_rejects_unaffordable_or_unread_cost(self):
        for price in (200, None, True, -1):
            value = deepcopy(self.value); value['ui']['options'][3]['costs']['gold'] = price
            with self.subTest(price=price), self.assertRaises(ValueError):
                plan_shop(value, choose(value, 'Disarm'))

    def test_potion_does_not_discard_full_belt_and_relic_purchase_is_supported(self):
        for role, name in [('potion', 'Energy Potion'), ('relic', 'Anchor')]:
            value = deepcopy(self.value)
            value['ui']['options'][0].update(role=role, offer={'name': name})
            result = plan_shop(value, choose(value, 'Wild Strike'))
            self.assertIn(name, result['draft']['choice']['postconditions']['inventory']['current'][role])
        value['ui']['options'][0].update(role='potion', offer={'name': 'Energy Potion'})
        value['inventory']['current']['potion'] = ['Fire Potion']*3
        self.assertEqual('strategy_required', plan_shop(value, choose(value, 'Wild Strike'))['status'])

    def test_removal_picker_and_actual_removal_preserve_duplicate_cards(self):
        value = deepcopy(self.value)
        value['ui'] = {'menu_family': 'shop_remove', 'choice_id': 'removal', 'focused_id': 'strike-1',
            'options': [{'id': 'strike-1', 'label': 'Strike', 'role': 'remove_card', 'enabled': True,
                         'costs': {'gold': 75}, 'offer': {'name': 'Strike'}}],
            'shop_positions': [{'id': 'strike-1', 'x': 300, 'y': 300}]}
        plan = plan_shop(value, choose(value, 'strike-1'))
        prepared = self.f.prepare(plan['draft'])
        inventory = deepcopy(value['inventory']); inventory['current']['card'].remove('Strike')
        packet, _, _ = self.verify(prepared, ui(), inventory=inventory,
            resources=dict(value['resources'], gold=38, deck_size=value['resources']['deck_size']-1))
        self.assertEqual(1, len(packet['telemetry']['inventory_events']))
        self.assertEqual('removed', packet['telemetry']['inventory_events'][0]['action'])
        self.assertEqual(4, self.f.db.inventory_ledger(run_id=self.f.run)['current']['card'].count('Strike'))

    def test_removal_preview_needs_its_actual_confirmation_before_inventory_changes(self):
        value = deepcopy(self.value)
        value['ui'] = {'menu_family': 'shop_remove', 'choice_id': 'removal', 'focused_id': 'strike-1',
            'options': [{'id': 'strike-1', 'label': 'Strike', 'role': 'remove_card', 'enabled': True,
                         'costs': {'gold': 75}, 'offer': {'name': 'Strike'}}],
            'shop_positions': [{'id': 'strike-1', 'x': 300, 'y': 300}]}
        prepared = self.f.prepare(plan_shop(value, choose(value, 'strike-1'))['draft'])
        preview = deepcopy(value['ui'])
        preview.update(phase='confirm', selected_ids=['strike-1'], pending_ids=['strike-1'],
                       confirm_hint={'button': 'triangle', 'hint_text': 'Triangle Confirm'})
        packet, _, _ = self.verify(prepared, preview)
        self.assertFalse(packet['telemetry'])
        self.assertEqual(5, self.f.db.inventory_ledger(run_id=self.f.run)['current']['card'].count('Strike'))
        value = snapshot_from_result(packet, self.f.session.path)
        prepared = self.f.prepare(plan_shop(value, choose(value, 'strike-1'))['draft'])
        inventory = deepcopy(value['inventory']); inventory['current']['card'].remove('Strike')
        self.verify(prepared, ui(), inventory=inventory,
                    resources=dict(value['resources'], gold=38, deck_size=value['resources']['deck_size']-1))
        self.assertEqual(['cross', 'triangle'], [x['buttons'][0] for x in self.f.controller.inputs])

    def test_nonpurchase_focus_cannot_change_stock_or_prices(self):
        prepared = self.f.prepare(plan_shop(self.value, choose(self.value, 'Disarm'))['draft'])
        changed = ui(); changed['focused_id'] = 'Heavy Blade'; changed['options'][0]['costs']['gold'] = 1
        with self.assertRaises(ValueError):
            self.verify(prepared, changed)
        self.assertEqual(1, len(self.f.controller.inputs))

    def test_bad_floor_pre_dispatch_request_can_be_cleared_without_controller_or_ledger_rewrite(self):
        value = deepcopy(self.value); value['context'] = dict(value['context'], floor_id='mistyped-floor')
        value['ui'] = ui('shop_exit')
        prepared = self.f.prepare(plan_shop(value)['draft'])
        self.assertEqual('preparing_dispatch', self.f.session.state['pending']['status'])
        self.assertEqual([], self.f.controller.inputs)
        self.assertEqual([], self.f.session.telemetry.recover(run_id=self.f.run)['pending'])
        answer = self.f.session.handle({'operation': 'recover_unsent'})
        self.assertEqual('reconciled_unsent', answer['status'])
        self.assertIsNone(self.f.session.state['pending'])
        self.assertEqual([], self.f.controller.inputs)

    def test_context_is_copied_exactly_and_typo_is_rejected_before_request(self):
        entry = deepcopy(self.value); entry['ui'] = ui('shop_entry')
        prepared = self.f.prepare(plan_shop(entry)['draft'])
        packet, _, _ = self.verify(prepared, ui())
        value = deepcopy(self.value); value['context'] = 'session'
        self.assertEqual(self.f.context, resolve_snapshot(value, self.f.session.path)['context'])
        value['context'] = dict(self.f.context, floor_id='one-character-typo')
        with self.assertRaisesRegex(ValueError, 'canonical session context'):
            resolve_snapshot(value, self.f.session.path)
        for key in ('inventory', 'resources', 'ui'):
            changed = deepcopy(packet)
            if key == 'inventory':
                changed['after']['inventory']['current']['card'].append('Invented Card')
            elif key == 'resources':
                changed['after']['observation']['resources']['gold'] += 100
            else:
                changed['after']['observation']['ui']['focused_id'] = 'Disarm'
            with self.subTest(changed=key), self.assertRaisesRegex(ValueError, 'contents differ'):
                snapshot_from_result(changed, self.f.session.path)
        packet['action_id'] = 'unrelated-action'
        with self.assertRaisesRegex(ValueError, 'already-verified'):
            snapshot_from_result(packet, self.f.session.path)

    def test_terminal_helper_works_from_an_unrelated_directory_and_old_settled_frame(self):
        entry = deepcopy(self.value); entry['ui'] = ui('shop_entry')
        prepared = self.f.prepare(plan_shop(entry)['draft'])
        packet, _, result_path = self.verify(prepared, ui('shop_exit'))
        # Binding is event-based despite time spent outside the helper.
        self.f.now += timedelta(minutes=10)
        output = self.f.root/'proceed.json'
        command = [sys.executable, str(Path('scripts/veda_shop.py').resolve()), '--after-result', str(result_path),
            '--session', str(self.f.session.path), '--capture', packet['after']['source']['path'],
            '--reviewer', 'Fixture', '--evidence-note', 'Same inspected settled Proceed prompt.',
            '--reviewed', '--execute', '--output', str(output)]
        result = subprocess.run(command, cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual({'request_file'}, set(json.loads(result.stdout)))
        request = json.loads(output.read_text())
        self.assertEqual(self.f.context, request['context'])
        self.assertIn('evidence_binding', request)
        answer = self.f.session.handle(request)
        self.assertEqual('awaiting_fresh_review', answer['status'], answer)
        self.assertEqual('triangle', self.f.controller.inputs[-1]['buttons'][0])


if __name__ == '__main__':
    unittest.main()
