"""Reward replay through the adapter with fake input and private SQLite only."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests import test_map_travel
from tests.test_loot_fast_path import snapshot
from tests.test_menu_requests import make_capture
from veda.loot import plan_loot, decision_key, snapshot_from_result
from veda.loot_results import observed_result, output_path, last_result
from veda.menu_results import write_menu_result, validate_menu_result
from veda.menu_controls import CONTROL_PROFILE


def rewards(*roles):
    options = []
    for role in roles:
        o = {'id': role, 'label': role, 'role': role, 'enabled': True, 'costs': {}}
        if role == 'gold':
            o['reward'] = {'amount': 17}
        if role == 'proceed':
            o['activate_hint'] = {'button': 'triangle', 'hint_text': 'Triangle Proceed'}
        options.append(o)
    return {'menu_family': 'loot_rewards', 'choice_id': 'floor5-loot', 'focused_id': roles[0],
            'options': options, 'grid': {'complete': True, 'cells': [
                {'id': o['id'], 'row': i, 'column': 0} for i, o in enumerate(options)]}}


def offers():
    ui = snapshot(True)['ui']
    ui['options'][0].update(id='Headbutt', label='Headbutt', reward={'name': 'Headbutt'})
    ui['focused_id'] = ui['grid']['cells'][0]['id'] = 'Headbutt'
    ui['options'][1]['activate_hint'] = {'button': 'square', 'hint_text': 'Square Skip'}
    return ui


class LootWorkflowTests(unittest.TestCase):
    def setUp(self):
        case = test_map_travel.MapTravelAdapterTests(); case.setUp()
        self.addCleanup(case.doCleanups)
        self.f = case.flow
        def capture(color=None):
            self.f.serial += 1; self.f.now += timedelta(seconds=1)
            return make_capture(self.f.root, self.f.now-timedelta(milliseconds=200),
                color=color or (self.f.serial, 40, 70), index=format(self.f.serial % 16, 'x'))
        self.f.capture = capture
        self.value = {'schema': 'veda.loot-snapshot.v1', 'context': self.f.context,
            'inventory': deepcopy(self.f.inventory), 'resources': dict(self.f.resources, hp=61, gold=39),
            'facts': dict(self.f.facts, reward_source='combat', potion_capacity=3),
            'ui': rewards('gold', 'card_reward')}

    def verify(self, kind, **kwargs):
        f = self.f
        draft = observed_result(f.session.path, kind, note='Actual synthetic result inspected.', **kwargs)
        path = output_path(f.session.path, action_id=draft['action_id'])
        write_menu_result(draft, session=f.session.path, action_id=draft['action_id'], capture=f.capture(),
            reviewer='Fixture', evidence_note='Synthetic reviewed outcome.', reviewed=True,
            control_profile=CONTROL_PROFILE, output=path, now=f.now)
        packet = json.loads(path.read_text())
        answer = f.session.handle(packet)
        self.assertEqual('verified', answer['status'], answer)
        return packet, path

    def open_cards(self):
        self.value['ui'] = rewards('card_reward')
        self.f.prepare(plan_loot(self.value)['draft'])
        packet, path = self.verify('offers', ui=offers(), unchanged=True)
        return snapshot_from_result(packet, self.f.session.path), packet, path

    def test_gold_offers_select_confirm_proceed_with_one_card_decision(self):
        f = self.f
        first = plan_loot(self.value)
        f.prepare(first['draft'])
        packet, _ = self.verify('gold', ui=rewards('card_reward'), actual={'gold': 56, 'others_unchanged': True})
        value = snapshot_from_result(last_result(f.session.path), f.session.path)
        self.assertEqual(56, value['resources']['gold'])
        f.prepare(plan_loot(value)['draft'])
        packet, _ = self.verify('offers', ui=offers(), unchanged=True)
        value = snapshot_from_result(packet, f.session.path)
        decision = {'option_id': 'Headbutt', 'reason': 'Reuse useful cards in this fixture.', 'decision_key': decision_key(value)}
        f.prepare(plan_loot(value, decision)['draft'])
        self.assertEqual('select', f.session.state['pending']['proposal']['step_kind'])
        packet, _ = self.verify('confirmation', focused_id='Headbutt', unchanged=True,
                              hint={'button': 'cross', 'hint_text': 'Cross Confirm'})
        self.assertEqual({}, packet['telemetry'])
        self.assertNotIn('Headbutt', f.db.inventory_ledger(run_id=f.run)['current']['card'])
        value = snapshot_from_result(packet, f.session.path)
        self.assertEqual(decision, plan_loot(value, decision)['decision'])
        self.assertTrue(plan_loot(value)['routine'])
        f.prepare(plan_loot(value, decision)['draft'])
        self.assertEqual('commit', f.session.state['pending']['proposal']['step_kind'])
        packet, _ = self.verify('acquired', ui=rewards('proceed'), actual={
            'acquired': {'kind': 'card', 'name': 'Headbutt'}, 'deck_size': 11, 'others_unchanged': True})
        self.assertEqual(['Headbutt'], [e['item'] for e in packet['telemetry']['inventory_events']])
        value = snapshot_from_result(packet, f.session.path)
        self.assertEqual(11, value['resources']['deck_size'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            plan_loot(value, decision)
        f.prepare(plan_loot(value)['draft'])
        packet, _ = self.verify('map', unchanged=True)
        self.assertEqual(['cross', 'cross', 'cross', 'cross', 'triangle'], [c['buttons'][0] for c in f.controller.inputs])
        self.assertIsNone(f.session.state['pending'])
        self.assertEqual(1, f.db.inventory_ledger(run_id=f.run)['current']['card'].count('Headbutt'))
        # Replaying a verified result must not duplicate its inventory event.
        f.session.handle(packet)
        self.assertEqual(1, f.db.inventory_ledger(run_id=f.run)['current']['card'].count('Headbutt'))
        with self.assertRaisesRegex(ValueError, 'no longer a loot'):
            snapshot_from_result(packet, f.session.path)

    def test_selection_cannot_claim_acquired_card_and_wrong_confirmation_is_rejected(self):
        value, _, _ = self.open_cards()
        decision = {'option_id': 'Headbutt', 'reason': 'Synthetic choice.', 'decision_key': decision_key(value)}
        self.f.prepare(plan_loot(value, decision)['draft'])
        with self.assertRaisesRegex(ValueError, 'committed collection'):
            observed_result(self.f.session.path, 'acquired', note='False', ui=rewards('proceed'), actual={
                'acquired': {'kind': 'card', 'name': 'Headbutt'}, 'deck_size': 11, 'others_unchanged': True})
        with self.assertRaisesRegex(ValueError, 'exact pending selection'):
            self.verify('confirmation', focused_id='skip', unchanged=True,
                        hint={'button': 'cross', 'hint_text': 'Cross Confirm'})
        self.assertEqual(2, len(self.f.controller.inputs))
        self.assertNotIn('Headbutt', self.f.db.inventory_ledger(run_id=self.f.run)['current']['card'])
        draft = observed_result(self.f.session.path, 'confirmation', note='Actual selection.', unchanged=True,
            focused_id='Headbutt', hint={'button': 'cross', 'hint_text': 'Cross Confirm'})
        draft['decision_policy'] = 'learning'
        draft['observed_result'] = 'Observed card confirmation; no acquisition yet. ' * 12
        validate_menu_result(draft, session=self.f.session.path, action_id=draft['action_id'], control_profile=CONTROL_PROFILE)
        draft['decision_policy'] = 'strict'
        with self.assertRaisesRegex(ValueError, 'cannot change'):
            validate_menu_result(draft, session=self.f.session.path, action_id=draft['action_id'], control_profile=CONTROL_PROFILE)
        draft['decision_policy'] = 'learning'
        draft['observed_result'] = '\N{EURO SIGN}' * 683
        with self.assertRaisesRegex(ValueError, 'observed_result.*2048 UTF-8 bytes'):
            validate_menu_result(draft, session=self.f.session.path, action_id=draft['action_id'], control_profile=CONTROL_PROFILE)

    def test_confirm_hint_required_and_cached_choice_changes_only_for_game_facts(self):
        source = snapshot(True)
        decision = {'option_id': 'strike', 'reason': 'Synthetic choice.', 'decision_key': decision_key(source)}
        source['ui'].update(phase='confirm', selected_ids=['strike'], pending_ids=['strike'])
        with self.assertRaises(ValueError):
            plan_loot(source, decision)
        source['ui']['confirm_hint'] = {'button': 'cross', 'hint_text': 'Cross Confirm'}
        self.assertEqual(decision, plan_loot(source, decision)['decision'])
        source['ui']['options'][0]['reward']['name'] = 'Strike+'
        with self.assertRaisesRegex(ValueError, 'changed'):
            plan_loot(source, decision)

    def test_actual_gold_required_and_unverified_or_modified_results_cannot_be_reused(self):
        self.f.prepare(plan_loot(self.value)['draft'])
        with self.assertRaisesRegex(ValueError, 'actual gold total'):
            observed_result(self.f.session.path, 'gold', note='Bad', ui=rewards('card_reward'),
                            actual={'others_unchanged': True})
        packet, _ = self.verify('gold', ui=rewards('card_reward'), actual={'gold': 55, 'others_unchanged': True})
        # A prediction mismatch retains the actual total rather than fabricating 56.
        self.assertEqual(55, snapshot_from_result(packet, self.f.session.path)['resources']['gold'])
        tampered = deepcopy(packet); tampered['after']['observation']['resources']['gold'] = 999
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            snapshot_from_result(tampered, self.f.session.path)
        value = snapshot_from_result(packet, self.f.session.path)
        self.f.prepare(plan_loot(value)['draft'])
        with self.assertRaisesRegex(ValueError, 'pending'):
            last_result(self.f.session.path)

    def test_skip_keeps_one_decision_and_inventory_unchanged(self):
        value, _, _ = self.open_cards()
        decision = {'option_id': 'skip', 'reason': 'No useful reward.', 'decision_key': decision_key(value)}
        self.f.prepare(plan_loot(value, decision)['draft'])
        packet, _ = self.verify('focus', focused_id='skip', unchanged=True)
        value = snapshot_from_result(packet, self.f.session.path)
        self.f.prepare(plan_loot(value, decision)['draft'])
        packet, _ = self.verify('returned', ui=rewards('proceed'), unchanged=True)
        self.assertEqual({}, packet['telemetry'])
        self.assertEqual('square', self.f.controller.inputs[-1]['buttons'][0])

    def test_cli_reuses_verified_state_and_generates_action_and_result_paths(self):
        value, packet, _ = self.open_cards()
        cli = str(Path('scripts/veda_loot.py').resolve())
        common = [sys.executable, cli, '--session', str(self.f.session.path)]
        decision = self.f.root/'decision.json'
        completed = subprocess.run(common + ['--last-result', '--choose', 'Headbutt', '--reason', 'Synthetic choice.',
            '--decision-output', str(decision)], cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertNotIn('draft', json.loads(completed.stdout))
        completed = subprocess.run(common + ['--last-result', '--decision', str(decision),
            '--capture', packet['after']['source']['path'], '--reviewer', 'Fixture', '--evidence-note', 'Same inspected state.',
            '--reviewed', '--execute'], cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        request_path = json.loads(completed.stdout)['request_file']
        self.assertIn('loot-packets', request_path)
        self.assertEqual('awaiting_fresh_review', self.f.session.handle(json.loads(Path(request_path).read_text()))['status'])
        capture = self.f.capture()
        completed = subprocess.run(common + ['--result', 'confirmation', '--focused-id', 'Headbutt',
            '--hint-button', 'cross', '--hint-text', 'Cross Confirm', '--unchanged',
            '--observed-result', 'Actual selected card; unchanged inventory.', '--capture', str(capture),
            '--reviewer', 'Fixture', '--evidence-note', 'Synthetic selection inspected.', '--reviewed'],
            cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        packet = json.loads(Path(json.loads(completed.stdout)['request_file']).read_text())
        self.assertEqual('verified', self.f.session.handle(packet)['status'])
