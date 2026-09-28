"""Learning-policy lifecycle contracts with temporary state and a fake controller.

No actual captures, bridge connections or actual-run database writes occur.
"""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests import test_reviewed_play as fixtures
from tests import test_choice_execution as menus
from tests.test_execution import context
from veda.execution import Reading
from veda.reviewed_play import (ReviewedPlaySession, RuntimeStop,
    verify_choice_observation, verify_combat_observation)


class LearningRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReviewedPlayTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def create(self, *, arm=True):
        f = self.f
        f.session = ReviewedPlaySession(f.root / 'session', run_id=f.context_ids['run_id'],
            telemetry=f.telemetry, mode='codex', controller_factory=f.factory,
            decision_policy='learning', clock=lambda: f.now)
        if arm:
            self.assertEqual('armed_codex_reviewed', f.arm()['status'])
        return f.session

    def advance(self):
        f = self.f
        f.before = f.combat_request(0, focus='s', ui_override={'phase': 'targeting',
            'selected_card_id': 's', 'focused_target_id': 'enemy', 'target_order': ['enemy']})
        self.create()
        prepared = f.prepare()
        self.assertEqual('prepared', prepared['status'], prepared)
        self.assertEqual('awaiting_fresh_review', f.send(prepared)['status'])
        f.now += timedelta(seconds=1)
        return prepared

    def verify(self, prepared, after, changes=None):
        return self.f.session.handle({'operation': 'verify', 'action_id': prepared['action_id'],
            'operation_id': str(uuid4()), 'after': after, 'telemetry': changes or {}})

    def test_unknown_intent_end_turn_is_assessed_then_one_input_is_sent(self):
        f = self.f
        f.before['plan'] = {'steps': [{'kind': 'end_turn'}]}
        f.before['reading']['ui']['focused_card_id'] = None
        for state in (f.before['reading']['context']['state'], f.before['reading']['state']):
            enemy = state['enemies'][0]
            enemy.update(intent=None, intent_hits=None)
            if 'intent_total_damage' in enemy:
                enemy.update(intent_total_damage=None, intent_damage_confidence=0.0)
        f.before['reading']['context']['unknowns'] = ['Exact intent not revealed.']
        self.create()
        result = f.prepare()
        self.assertEqual('prepared', result['status'], result)
        self.assertTrue(result['assessment']['warnings'])
        self.assertIsNone(result['assessment']['forecast'])
        self.assertEqual('awaiting_fresh_review', f.send(result)['status'])
        self.assertEqual([['triangle']], [v['buttons'] for v in f.controller.inputs])
        self.assertTrue(f.session.armed)
        prediction = json.loads(f.decisions()[0]['prediction_json'])
        self.assertEqual('learning', prediction['decision_policy'])
        self.assertIsNone(prediction['forecast'])
        self.assertTrue(prediction['assessment']['warnings'])

    def test_policy_is_persisted_and_normalized_and_cannot_switch_with_pending(self):
        f = self.f
        self.create(); f.prepare()
        disk = json.loads(f.session.path.read_text())
        self.assertEqual('learning', disk['decision_policy'])
        self.assertEqual('learning', disk['pending']['request']['decision_policy'])
        self.assertEqual('learning', disk['pending']['request']['reading']['context']['decision_policy'])
        f.session.close()
        with self.assertRaisesRegex(RuntimeStop, 'policy'):
            ReviewedPlaySession(f.root / 'session', run_id=f.context_ids['run_id'],
                telemetry=f.telemetry, decision_policy='strict')

    def test_schema_and_timing_errors_keep_armed_session_without_input(self):
        f = self.f; self.create()
        for request in ([], {'operation': 'unknown'}, {'operation': 'timing'}):
            result = f.session.handle(request)
            self.assertEqual('recoverable_review', result['status'], result)
            self.assertTrue(result['armed'])
            self.assertFalse(result['controller_input_sent'])
        self.assertFalse(f.controller.closed)
        self.assertEqual([], f.controller.inputs)

    def test_stale_prepared_input_stays_unsent_then_can_be_cancelled(self):
        f = self.f; self.create(); prepared = f.prepare()
        f.now += timedelta(seconds=31)
        result = f.send(prepared)
        self.assertEqual('recoverable_review', result['status'])
        self.assertEqual('cancel_prepared', result['next_operation'])
        self.assertTrue(f.session.armed)
        self.assertEqual([], f.controller.inputs)
        self.assertEqual('cancelled_without_input', f.session.handle({'operation': 'cancel_prepared'})['status'])
        self.assertIsNone(f.session.state['pending'])

    def test_context_or_policy_contradiction_is_recoverable_and_sends_nothing(self):
        f = self.f; self.create()
        for change in ('floor', 'policy'):
            bad = deepcopy(f.before)
            if change == 'floor':
                bad['context']['floor_id'] = 'another-floor'
            else:
                bad['decision_policy'] = 'strict'
            result = f.session.handle(bad)
            self.assertEqual('recoverable_review', result['status'])
            self.assertTrue(result['armed'])
            self.assertEqual([], f.controller.inputs)

    def test_wrong_navigation_result_can_be_reinspected_without_repeating_input(self):
        f = self.f; self.create(); prepared = f.prepare(); f.send(prepared)
        f.now += timedelta(seconds=1)
        failed = self.verify(prepared, f.combat_request(1, focus='d'))
        self.assertEqual('recoverable_review', failed['status'])
        self.assertEqual('verify', failed['next_operation'])
        self.assertTrue(f.session.armed)
        self.assertEqual('attempted', f.session.state['pending']['status'])
        f.now += timedelta(seconds=1)
        result = f.verify_navigation(prepared, 2)
        self.assertEqual('verified', result['status'], result)
        self.assertEqual(1, len(f.controller.inputs))

    def test_actual_card_effect_is_logged_despite_wrong_prediction(self):
        f = self.f; prepared = self.advance()
        game = context(); game['state']['hand'] = game['state']['hand'][1:]
        game['state'].update(energy=1, hp=39, block=3)
        game['state']['enemies'][0]['hp'] = 7
        result = self.verify(prepared, f.combat_request(1, game=game))
        self.assertEqual('verified', result['status'], result)
        fields = {v['field'] for v in result['observed_mismatches']}
        self.assertTrue({'energy', 'hp', 'block', 'enemies.enemy.hp'} <= fields)
        actual = json.loads(f.decisions()[0]['actual_outcome_json'])
        self.assertEqual('learning', actual['learning']['decision_policy'])
        self.assertEqual(result['observed_mismatches'], actual['learning']['observed_mismatches'])
        self.assertTrue(f.session.armed)
        self.assertEqual(1, len(f.controller.inputs))

    def test_card_still_in_hand_is_not_a_forecast_mismatch(self):
        f = self.f; prepared = self.advance()
        result = self.verify(prepared, f.combat_request(1, focus='s'))
        self.assertEqual('recoverable_review', result['status'])
        self.assertIn('remains in hand', result['error'])
        self.assertTrue(f.session.armed)
        self.assertEqual('attempted', f.session.state['pending']['status'])

    def test_uncertain_transport_terminates_but_preserves_pending_and_never_replays(self):
        f = self.f; self.create(); prepared = f.prepare(); f.controller.uncertain = True
        result = f.send(prepared)
        self.assertEqual('transport_stopped', result['status'], result)
        self.assertFalse(result['armed'])
        self.assertIsNone(result['controller_input_sent'])
        self.assertEqual('attempted', f.session.state['pending']['status'])
        self.assertEqual(1, len(f.controller.inputs))
        self.assertTrue(f.controller.closed)

    def test_database_pre_dispatch_failure_is_recoverable_without_input(self):
        f = self.f; self.create(); prepared = f.prepare()
        with patch.object(f.telemetry, 'record_decision', side_effect=OSError('temporary database problem')):
            result = f.send(prepared)
        self.assertEqual('recoverable_review', result['status'])
        self.assertEqual('recover_unsent', result['next_operation'])
        self.assertTrue(f.session.armed)
        self.assertEqual([], f.controller.inputs)
        self.assertEqual('reconciled_unsent', f.session.handle({'operation': 'recover_unsent'})['status'])

    def test_source_expiry_after_decision_is_positively_recorded_unsent(self):
        f = self.f; self.create(); prepared = f.prepare()
        original = f.telemetry.record_decision
        def delayed(*args, **kwargs):
            receipt = original(*args, **kwargs)
            f.now += timedelta(seconds=31)
            return receipt
        with patch.object(f.telemetry, 'record_decision', side_effect=delayed):
            result = f.send(prepared)
        self.assertEqual('recoverable_review', result['status'])
        self.assertEqual('preparing_dispatch', result['pending']['status'])
        self.assertFalse(f.session.state['pending']['pre_dispatch_failure']['controller_call_started'])
        self.assertFalse(result['controller_input_sent'])
        self.assertEqual([], f.controller.inputs)
        self.assertTrue(f.session.armed)

    def test_outcome_commit_response_loss_finalizes_in_same_session(self):
        f = self.f; self.create(); prepared = f.prepare(); f.send(prepared)
        original = f.telemetry.record_outcome
        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('reply lost after outcome committed')
        with patch.object(f.telemetry, 'record_outcome', side_effect=commit_then_fail):
            result = f.verify_navigation(prepared)
        self.assertEqual('recoverable_review', result['status'])
        self.assertEqual('finalize', result['next_operation'])
        self.assertTrue(f.session.armed)
        final = f.session.handle({'operation': 'finalize'})
        self.assertEqual('verified', final['status'])
        self.assertEqual(1, f.session.state['completed'])
        self.assertEqual(1, len(f.controller.inputs))
        with f.db._connection() as con:
            self.assertEqual(1, con.execute("SELECT COUNT(*) FROM evidence_events WHERE kind='play_outcome'").fetchone()[0])

    def test_final_state_save_failure_preserves_verified_pending_in_memory(self):
        f = self.f; self.create(); prepared = f.prepare(); f.send(prepared)
        original = f.session._save
        def fail_final_save():
            if f.session.state['pending'] is None:
                raise OSError('final state save failed')
            original()
        with patch.object(f.session, '_save', side_effect=fail_final_save):
            result = f.verify_navigation(prepared)
        self.assertEqual('finalize', result['next_operation'])
        self.assertEqual('verified_pending_log', f.session.state['pending']['status'])
        self.assertEqual(0, f.session.state['completed'])
        self.assertEqual('verified', f.session.handle({'operation': 'finalize'})['status'])
        self.assertEqual(1, len(f.controller.inputs))

    def test_durable_storage_failure_disarms_and_retains_actual_pending(self):
        f = self.f; self.create(); prepared = f.prepare(); f.send(prepared)
        original = os.replace
        def fail_final_state(source, target):
            if Path(target) == f.session.path and json.loads(Path(source).read_text())['pending'] is None:
                raise OSError('synthetic storage unavailable')
            return original(source, target)
        with patch('veda.reviewed_play.os.replace', side_effect=fail_final_state):
            result = f.verify_navigation(prepared)
        self.assertEqual('storage_recovery_required', result['status'], result)
        self.assertFalse(result['armed'])
        self.assertEqual('verified_pending_log', result['pending']['status'])
        self.assertEqual('verified_pending_log', json.loads(f.session.path.read_text())['pending']['status'])
        self.assertEqual(1, len(f.controller.inputs))
        self.assertTrue(f.controller.closed)

    def test_ordinary_combat_win_continues(self):
        f = self.f; prepared = self.advance()
        after, changes = f.terminal_after(prepared)
        result = self.verify(prepared, after, changes)
        self.assertEqual('verified', result['status'], result)
        self.assertTrue(f.session.armed)
        self.assertFalse(f.controller.closed)

    def test_explicit_run_victory_closes_after_durable_result(self):
        f = self.f; prepared = self.advance()
        after, changes = f.terminal_after(prepared)
        after['observation']['ui']['screen'] = 'result'
        after['observation']['facts']['run_outcome'] = 'victory'
        result = self.verify(prepared, after, changes)
        self.assertEqual('run_complete', result['status'], result)
        self.assertEqual('victory', result['reason'])
        self.assertTrue(f.controller.closed)
        self.assertEqual('resolved', f.decisions()[0]['status'])
        self.assertIsNone(f.session.state['pending'])

    def test_combat_loss_result_closes_after_durable_result(self):
        f = self.f; prepared = self.advance()
        after, changes = f.terminal_after(prepared)
        after['observation']['ui']['screen'] = 'result'
        after['observation']['facts']['combat_outcome'] = 'loss'
        after['observation']['resources']['hp'] = 0
        changes['transitions'][1].update(outcome='defeat', closing_state={'hp': 0})
        after['mutation_review']['changes'] = deepcopy(changes)
        result = self.verify(prepared, after, changes)
        self.assertEqual('run_complete', result['status'], result)
        self.assertEqual('defeat', result['reason'])
        self.assertFalse(f.session.armed)
        self.assertIsNone(f.session.state['pending'])

    def test_menu_unpredicted_resource_effect_is_observed_not_assumed(self):
        f = self.f; f.before = f.choice_request(0); self.create()
        prepared = f.prepare(); f.send(prepared); f.now += timedelta(seconds=1)
        after = f.choice_after(prepared)
        after['observation']['resources']['hp'] = 27
        result = self.verify(prepared, after)
        self.assertEqual('verified', result['status'], result)
        self.assertEqual('choice_postconditions', result['observed_mismatches'][0]['field'])
        self.assertEqual(27, result['observed_mismatches'][0]['observed']['resources']['hp'])
        self.assertTrue(f.session.armed)

    def test_learning_selection_boundary_accepts_actual_uncatalogued_card_cause(self):
        f = self.f
        game = context(); game['state']['hand'][0].update(name='Burning Pact', type='Skill')
        f.before = f.combat_request(0, focus='s', game=game, ui_override={
            'phase': 'card_selected', 'selected_card_id': 's'})
        f.before['plan']['steps'][0].pop('target')
        self.create(); prepared = f.prepare(); f.send(prepared); f.now += timedelta(seconds=1)
        after = f.choice_request(1)
        after['observation']['facts'] = {'selection_cause_card_id': 's'}
        after['observation']['resources'] = {'hp': 40, 'energy': 0}
        after['observation']['review']['outcome'] = {'action_id': prepared['action_id'],
            'before_frame_id': 'frame-0', 'before_sha256': f.before['source']['sha256'],
            'action': f.before['plan']['steps'][0], 'observed_result': 'Actual card exhaust selection appeared.'}
        bad = deepcopy(after); bad['observation']['facts']['selection_cause_card_id'] = 'wrong'
        self.assertEqual('recoverable_review', self.verify(prepared, bad)['status'])
        result = self.verify(prepared, after)
        self.assertEqual('verified', result['status'], result)
        self.assertTrue(f.session.armed)
        self.assertEqual(1, len(f.controller.inputs))

    def test_explicit_stop_closes_session(self):
        f = self.f; self.create()
        self.assertEqual('stopped', f.session.handle({'operation': 'stop'})['status'])
        self.assertTrue(f.controller.closed)
        self.assertFalse(f.session.armed)

    def test_codex_cli_recovers_malformed_json_without_closing_armed_bridge(self):
        from scripts import veda_reviewed_play as cli
        f = self.f
        stream = io.StringIO(json.dumps(f.arm_request()) + '\nnot-json\n'
                             '{"operation":"summary"}\n{"operation":"stop"}\n')
        output = io.StringIO()
        with patch.object(cli.sys, 'stdin', stream), patch.object(cli, 'BridgeClient', return_value=f.controller), \
                redirect_stdout(output):
            code = cli.main([str(f.root / 'cli-session'), '--run-id', f.context_ids['run_id'],
                             '--database', str(f.db.path), '--mode', 'codex'])
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, code)
        self.assertEqual('learning', rows[0]['summary']['decision_policy'])
        self.assertEqual('armed_codex_reviewed', rows[1]['status'])
        self.assertEqual('recoverable_review', rows[2]['status'])
        self.assertTrue(rows[2]['armed'])
        self.assertTrue(rows[3]['armed'])
        self.assertEqual('stopped', rows[4]['status'])
        self.assertEqual([], f.controller.inputs)

    def test_shadow_cli_retains_strict_default_and_accepts_explicit_learning(self):
        from scripts import veda_reviewed_play as cli
        f = self.f
        for policy in (None, 'learning'):
            output = io.StringIO()
            args = [str(f.root / ('shadow-' + str(policy))), '--run-id', f.context_ids['run_id'],
                    '--database', str(f.db.path)]
            if policy:
                args += ['--decision-policy', policy]
            with patch.object(cli.sys, 'stdin', io.StringIO('')), \
                    patch.object(cli, 'BridgeClient', side_effect=AssertionError('no bridge in shadow')), \
                    redirect_stdout(output):
                self.assertEqual(0, cli.main(args))
            self.assertEqual(policy or 'strict', json.loads(output.getvalue())['summary']['decision_policy'])


class LearningPureVerificationTests(unittest.TestCase):
    def test_focus_choice_id_and_selection_only_cannot_prove_commit(self):
        before = menus.observation(); proposal = menus.plan(before)
        for key, value in (('focused_id', '1'), ('choice_id', 'renamed'), ('selected_ids', ['0'])):
            after = menus.outcome(before, proposal)
            after['facts'] = deepcopy(before['facts'])
            after['ui'] = deepcopy(before['ui'])
            after['ui'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeStop, 'semantic choice result'):
                verify_choice_observation(proposal, before, after, policy='learning',
                    now=menus.NOW + timedelta(seconds=3))

    def test_wrong_action_correlation_cannot_be_softened(self):
        before = menus.observation(); proposal = menus.plan(before)
        after = menus.outcome(before, proposal)
        after['resources']['hp'] = 1
        after['review']['outcome']['action_id'] = 'different-action'
        with self.assertRaisesRegex(RuntimeStop, 'exact source-bound'):
            verify_choice_observation(proposal, before, after, policy='learning',
                now=menus.NOW + timedelta(seconds=3))

    def test_unknown_previous_hp_does_not_invent_baseline(self):
        c = context(); c['state']['hp'] = None
        ui = {'screen_type': 'combat', 'phase': 'hand', 'hand_order': ['s', 'd'],
              'focused_card_id': 's', 'selected_card_id': None}
        before = Reading.from_dict(dict(fixtures.reading(c, ui), frame_id='before', image_sha256='a' * 64))
        c['state']['hand'] = c['state']['hand'][1:]; c['state']['energy'] = 0
        after = Reading.from_dict(dict(fixtures.reading(c, {**ui, 'hand_order': ['d'], 'focused_card_id': 'd'}),
            frame_id='after', image_sha256='b' * 64))
        result = verify_combat_observation(before, after, {'kind': 'card', 'card_id': 's', 'target': 'enemy'},
            {'kind': 'advance', 'card': before.context['state']['hand'][0]},
            {'steps': [{'energy_after': 0, 'reviewed_effect': {}}]}, policy='learning')
        self.assertTrue(result['logical_action_complete'])
        self.assertEqual([], result['observed_mismatches'])

    def test_oversized_jsonl_line_can_recover_to_next_request(self):
        path = Path(__file__).parents[1] / 'scripts/veda_reviewed_play.py'
        spec = importlib.util.spec_from_file_location('learning_cli_fixture', path)
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        class Session:
            decision_policy = 'learning'
            closed = False
        with patch.object(cli, 'MAX_BYTES', 40):
            rows = list(cli.timed_requests(io.StringIO('x' * 100 + '\n{"operation":"summary"}\n'), Session()))
        self.assertIsInstance(rows[0], ValueError)
        self.assertEqual({'operation': 'summary'}, json.loads(rows[1]))
        self.assertEqual(2, len(rows))


if __name__ == '__main__':
    unittest.main()
