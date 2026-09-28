"""Archived merchant annotations and fake-controller acceptance; no live state."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_shop_workflow as shop_cases, test_map_travel as map_cases
from veda.map_travel import plan_map
from veda.shop import plan_shop, snapshot_from_result
from veda.shop_observation import stock_ui
from veda.shop_results import last_result, observed_result, output_path
from veda.menu_results import write_menu_result
from veda.menu_controls import CONTROL_PROFILE

FIXTURE = json.loads(Path('tests/fixtures/merchant_visit_20260928.json').read_text())


class MerchantShortcutTests(unittest.TestCase):
    def setUp(self):
        self.case = shop_cases.ShopTests(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.f = self.case.f
        self.f.inventory['current']['card'].append('Uppercut')
        self.f.resources['deck_size'] = 11
        with patch('veda.telemetry_database._now', return_value=(self.f.now-timedelta(seconds=1)).isoformat()):
            self.f.db.record_inventory_baseline(run_id=self.f.run, floor_id=self.f.floor,
                items=[{'kind': k, 'item': n} for k, names in self.f.inventory['current'].items() for n in names],
                coverage=self.f.inventory['coverage'], source='Synthetic merchant replay baseline')
        self.value = deepcopy(self.case.value)
        self.value['inventory'] = deepcopy(self.f.inventory)
        self.value['resources'] = deepcopy(self.f.resources)
        self.value['ui'] = stock_ui(FIXTURE['stock'], 113)

    def verify(self, kind, **fields):
        draft = observed_result(self.f.session.path, kind, note='Inspected fixture outcome.', **fields)
        capture = self.f.capture()
        out = output_path(self.f.session.path, action_id=draft['action_id'])
        write_menu_result(draft, session=self.f.session.path, action_id=draft['action_id'],
                          capture=capture, reviewer='Fixture', evidence_note='Inspected fixture result.',
                          reviewed=True, control_profile=CONTROL_PROFILE, output=out, now=self.f.now)
        packet = json.loads(out.read_text())
        answer = self.f.session.handle(packet)
        self.assertEqual('verified', answer['status'], answer)
        self.assertEqual(packet, last_result(self.f.session.path))
        return packet

    def test_archived_stock_excludes_preview_and_keeps_actual_focus(self):
        ui = self.value['ui']
        self.assertEqual('wild-strike', ui['focused_id'])
        self.assertNotIn('Wound', [o.get('offer', {}).get('name') for o in ui['options']])
        self.assertEqual('Heavy Blade', ui['options'][1]['offer']['name'])
        plan = plan_shop(self.value, shop_cases.choose(self.value, 'disarm'))
        self.f.prepare(plan['draft'])
        self.assertEqual('heavy-blade', self.f.session.state['pending']['proposal']['expectation']['focused_id'])
        self.assertEqual('right', self.f.controller.inputs[-1]['buttons'][0])

    def test_ghost_stock_and_false_upgrade_cannot_become_navigation_lessons(self):
        for variant in ('status', 'upgrade'):
            value = deepcopy(self.value)
            if variant == 'status':
                value['ui']['options'][0]['offer']['name'] = 'Wound'
            else:
                value['ui']['options'][1]['offer'] = {'name': 'Heavy Blade+', 'upgraded': False}
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                plan_shop(value)
        self.assertEqual([], self.f.controller.inputs)

    def test_affordable_unknowns_and_exhaust_get_honest_strategy_notes(self):
        result = plan_shop(self.value)
        self.assertTrue(any('Disarm exhausts' in n for n in result['notes']))
        self.assertTrue(any('potion-1' in n for n in result['notes']))
        with self.assertRaisesRegex(ValueError, 'observed item identity'):
            plan_shop(self.value, shop_cases.choose(self.value, 'potion-1'))

    def test_compact_full_sequence_retains_decision_and_no_purchase_before_confirm(self):
        entry = deepcopy(self.value); entry['ui'] = shop_cases.ui('shop_entry')
        self.f.prepare(plan_shop(entry)['draft'])
        packet = self.verify('opened', unchanged=True, stock=FIXTURE['stock'])
        value = snapshot_from_result(packet, self.f.session.path)
        saved = shop_cases.choose(value, 'disarm')
        for focus in ('heavy-blade', 'flex', 'disarm'):
            self.f.prepare(plan_shop(value, saved)['draft'])
            packet = self.verify('focus', unchanged=True, focused_id=focus)
            value = snapshot_from_result(packet, self.f.session.path)
        self.f.prepare(plan_shop(value, saved)['draft'])
        self.assertEqual('select', self.f.session.state['pending']['proposal']['step_kind'])
        packet = self.verify('confirmation', unchanged=True, focused_id='disarm',
                             hint={'button': 'cross', 'hint_text': 'Cross Confirm'})
        self.assertFalse(packet['telemetry'])
        self.assertEqual(113, packet['after']['observation']['resources']['gold'])
        self.assertNotIn('Disarm', self.f.db.inventory_ledger(run_id=self.f.run)['current']['card'])
        value = snapshot_from_result(packet, self.f.session.path)
        self.f.prepare(plan_shop(value, saved)['draft'])
        self.assertEqual('commit', self.f.session.state['pending']['proposal']['step_kind'])
        packet = self.verify('purchased', actual={'gold': 39, 'deck_size': self.value['resources']['deck_size']+1,
            'acquired': {'kind': 'card', 'name': 'Disarm'}, 'sold_id': 'disarm',
            'focused_id': 'wild-strike', 'others_unchanged': True})
        self.assertEqual(1, len(packet['telemetry']['inventory_events']))
        value = snapshot_from_result(packet, self.f.session.path)
        self.f.prepare(plan_shop(value, shop_cases.choose(value, 'leave'))['draft'])
        packet = self.verify('left', unchanged=True, hint={'button': 'triangle', 'hint_text': 'Triangle Proceed'})
        self.f.prepare(plan_shop(snapshot_from_result(packet, self.f.session.path))['draft'])
        # The final map result is valid but no longer a merchant planning snapshot.
        draft = observed_result(self.f.session.path, 'map', unchanged=True, note='Map actually visible.')
        self.case.verify({'action_id': draft['action_id']}, draft['result']['ui'])
        self.assertEqual(['cross', 'right', 'right', 'right', 'cross', 'cross', 'circle', 'triangle'],
                         [c['buttons'][0] for c in self.f.controller.inputs])
        self.assertIsNone(self.f.session.state['pending'])

    def test_confirmation_cannot_change_item_or_invent_paid_outcome(self):
        self.value['ui']['focused_id'] = 'disarm'
        self.f.prepare(plan_shop(self.value, shop_cases.choose(self.value, 'disarm'))['draft'])
        with self.assertRaises(ValueError):
            self.verify('confirmation', unchanged=True, focused_id='flex', hint={'button': 'cross', 'hint_text': 'Cross Confirm'})
        with self.assertRaisesRegex(ValueError, 'exact confirmed purchase'):
            observed_result(self.f.session.path, 'purchased', note='Not confirmed.', actual={
                'gold':39, 'deck_size':11, 'acquired':{'kind':'card','name':'Disarm'},
                'sold_id':'disarm', 'focused_id':'wild-strike', 'others_unchanged':True})
        self.assertEqual(1, len(self.f.controller.inputs))

    def test_cli_places_result_under_session_from_any_cwd_then_reuses_verified_state(self):
        entry = deepcopy(self.value); entry['ui'] = shop_cases.ui('shop_entry')
        self.f.prepare(plan_shop(entry)['draft'])
        capture = self.f.capture()
        stock = self.f.root/'stock.json'; stock.write_text(json.dumps(FIXTURE['stock']))
        cli = [sys.executable, str(Path('scripts/veda_shop.py').resolve()), '--session', str(self.f.session.path)]
        cmd = cli + ['--result','opened','--stock',str(stock),'--unchanged','--observed-result','Actual stock inspected.',
            '--capture',str(capture),'--reviewer','Fixture','--evidence-note','Actual fixture inspected.','--reviewed']
        result = subprocess.run(cmd, cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout+result.stderr)
        path = Path(json.loads(result.stdout)['request_file'])
        self.assertEqual(self.f.session.path.parent/'shop-packets', path.parent)
        # An unverified output cannot be reused as live state.
        with self.assertRaises(ValueError): last_result(self.f.session.path)
        self.assertEqual('verified', self.f.session.handle(json.loads(path.read_text()))['status'])
        result = subprocess.run(cli+['--last-result'], cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout+result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual('strategy_required', preview['status'])
        self.assertNotIn('draft', preview)
        self.assertEqual(1, len(self.f.controller.inputs))


class ForcedMapTests(unittest.TestCase):
    def single(self):
        value = map_cases.snapshot()
        value['ui']['options'] = [value['ui']['options'][2]]
        value['ui']['focused_id'] = 'right'
        value['ui']['map_siblings']['selectable_count'] = 1
        value['ui']['map_siblings']['evidence_note'] = 'All current connections inspected; exactly one reachable node.'
        return value

    def test_single_node_skips_archive_strategy_and_preserves_cache_baseline(self):
        original = map_cases.snapshot()
        cache = plan_map(original, decision=map_cases.decision())['cache']
        value = self.single(); value['resources'].update(hp=1, gold=0)
        value['inventory']['current']['card'].append('Disarm')
        with patch('veda.map_travel._merge_views', side_effect=AssertionError('forced move rescanned map')), \
             patch('veda.map_travel._reasons', side_effect=AssertionError('forced move re-ran strategy')):
            result = plan_map(value, cache)
        self.assertEqual('planned', result['status'])
        self.assertTrue(result['forced_move'])
        self.assertEqual(cache['route']['baseline'], result['cache']['route']['baseline'])
        self.assertIn('deck_relics_or_potions_changed', plan_map(dict(original, inventory=value['inventory']), result['cache'])['reasons'])

    def test_partial_unreachable_disabled_or_wrong_count_is_not_forced(self):
        for mistake in ('partial','unreachable','disabled','count'):
            value = self.single()
            if mistake == 'partial': value['ui']['map_siblings']['complete'] = False
            if mistake == 'unreachable': value['ui']['options'][0]['node']['reachable'] = False
            if mistake == 'disabled': value['ui']['options'][0]['enabled'] = False
            if mistake == 'count': value['ui']['map_siblings']['selectable_count'] = 2
            with self.subTest(mistake=mistake), self.assertRaises(ValueError): plan_map(value)
        self.assertEqual('strategy_required', plan_map(map_cases.snapshot())['status'])

    def test_single_node_still_uses_real_adapter_and_verifies_room_entry(self):
        case = map_cases.MapTravelAdapterTests(); case.setUp(); self.addCleanup(case.doCleanups)
        value = deepcopy(case.snapshot)
        value['ui']['options'] = [value['ui']['options'][2]]
        value['ui']['focused_id'] = 'right'; value['ui']['map_siblings']['selectable_count'] = 1
        value['ui']['map_siblings']['evidence_note'] = 'All current connections inspected; exactly one reachable node.'
        plan = plan_map(value)
        prepared = case.flow.prepare(plan['draft'])
        self.assertEqual(['cross'], [c['buttons'][0] for c in case.flow.controller.inputs])
        case.flow.verify(prepared, {'kind':'room_entry','node_id':'right','screen':'shop'},
            facts=dict(case.flow.facts, floor=1, current_node_id='right', node_type='merchant'))
        self.assertIsNone(case.flow.session.state['pending'])
