"""Recorded focus-label regressions plus temporary fake-controller round trips.

Annotations were manually compared with the retained images. These tests check
the UI contract and never claim automatic visual recognition or hardware proof.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from uuid import uuid4

from tests import test_reviewed_play as fixtures
from tests.test_combat_requests import draft
from tests.test_execution import context
from veda.combat_input import (CombatInputAdapter, RuntimeStop, FOCUS_FIELDS,
    FOCUS_CONTROL_PROFILE, validate_combat_focus, focus_transition)
from veda.combat_requests import validate_combat_draft
from veda.controller_state_machine import ControllerStateMachine
from veda.execution import Reading
from veda.reviewed_play import ReviewedPlaySession, verify_combat_observation


def away(domain='potion', subject='potion:slot-1', direction='down'):
    return {'phase': 'inspect', 'focused_card_id': None, 'selected_card_id': None,
        'focused_target_id': None, 'focus_domain': domain, 'tooltip_kind': domain,
        'focused_subject_id': subject, 'focus_evidence_note': 'Synthetic reviewed nonhand focus.',
        'control_profile': FOCUS_CONTROL_PROFILE, 'recovery_direction': direction}


class FocusTraceTests(unittest.TestCase):
    def test_original_up_trace_is_away_from_hand_and_cannot_be_relabeled_hand(self):
        trace = json.loads((Path(__file__).parent / 'fixtures/combat_focus_trace.json').read_text())
        previous = None
        for frame in trace['frames']:
            ui = {k: v for k, v in frame.items() if k not in {'sha256', 'captured_at'}}
            ui.update(screen_type='combat', selected_card_id=None, focused_target_id=None,
                      hand_order=trace['hand_ids'])
            self.assertEqual(frame['focus_domain'], validate_combat_focus(ui, trace['hand_ids'], require_explicit=True)['domain'])
            if previous:
                transition = focus_transition(previous, ui, trace['hand_ids'])
                self.assertFalse(transition['returned_to_hand'])
                self.assertEqual('focus_changed', transition['effect'])
                contradictory = {**ui, 'phase': 'hand'}
                with self.assertRaisesRegex(RuntimeStop, 'contradict'):
                    validate_combat_focus(contradictory, trace['hand_ids'], require_explicit=True)
            previous = ui

    def test_raised_defend_keyword_help_navigates_within_hand_not_up(self):
        value = draft(); value['decision_policy'] = 'learning'
        value['ui'].update(focused_card_id='d', tooltip_kind='card_keyword')
        value['plan']['steps'] = [{'kind': 'card', 'card_id': 's', 'target': 'enemy'}]
        result = validate_combat_draft(value)
        self.assertEqual({'buttons': ['left'], 'expected_kind': 'card_focus'}, result['next_atomic_input_preview'])
        value['plan']['steps'] = [{'kind': 'end_turn'}]
        self.assertEqual(['triangle'], validate_combat_draft(value)['next_atomic_input_preview']['buttons'])

    def test_missing_or_contradictory_learning_focus_does_not_emit_input(self):
        value = draft(); value['decision_policy'] = 'learning'
        for key in FOCUS_FIELDS:
            broken = deepcopy(value); broken['ui'].pop(key)
            with self.subTest(key=key), self.assertRaises(RuntimeStop):
                validate_combat_draft(broken)
        value['ui'].update(away()); value['ui']['phase'] = 'hand'
        with self.assertRaisesRegex(RuntimeStop, 'contradict'):
            validate_combat_draft(value)

    def test_recovery_preview_is_bounded_profile_exploration_not_confirm(self):
        value = draft(); value['decision_policy'] = 'learning'; value['ui'].update(away())
        self.assertEqual({'buttons': ['down'], 'expected_kind': 'focus_probe'},
                         validate_combat_draft(value)['next_atomic_input_preview'])
        for button in ('cross', 'up', 'left', 'square'):
            value['ui']['recovery_direction'] = button
            with self.subTest(button=button), self.assertRaises(ValueError):
                validate_combat_draft(value)

    def test_legacy_generic_tooltip_never_plans_an_up(self):
        with self.assertRaisesRegex(ValueError, 'focus domain'):
            ControllerStateMachine().plan_card_step({'screen_type':'combat','tooltip':True}, card_name='Defend')
        with self.assertRaises(ValueError):
            ControllerStateMachine().plan_end_turn({'screen_type':'combat','tooltip':True})

    def test_legacy_clear_can_observe_potion_focus_without_claiming_progress(self):
        c = context()
        ui = {'screen_type':'combat', 'phase':'tooltip', 'focused_card_id':None,
              'selected_card_id':None, 'focused_target_id':None, 'hand_order':['s','d']}
        before = Reading.from_dict(dict(fixtures.reading(c, ui), frame_id='old', image_sha256='a'*64))
        after = Reading.from_dict(dict(fixtures.reading(c, {**ui, **away()}), frame_id='new', image_sha256='b'*64))
        result = verify_combat_observation(before, after, {'kind':'card','card_id':'s','target':'enemy'},
                                           {'kind':'clear'}, {'steps':[]}, policy='learning')
        self.assertFalse(result['logical_action_complete'])
        self.assertFalse(result['focus_transition']['returned_to_hand'])
        self.assertEqual('focus_observed', result['focus_transition']['effect'])
        self.assertEqual('potion', result['focus_transition']['after']['domain'])
        contradictory = deepcopy(after); contradictory.ui['phase'] = 'hand'
        with self.assertRaisesRegex(RuntimeStop, 'contradict'):
            verify_combat_observation(before, contradictory, {}, {'kind':'clear'}, {}, policy='learning')


class FocusRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReviewedPlayTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f; f.before = f.combat_request(0, focus=None, ui_override=away())
        f.session = ReviewedPlaySession(f.root/'focus-session', run_id=f.context_ids['run_id'], telemetry=f.telemetry,
            mode='codex', controller_factory=f.factory, clock=lambda:f.now, decision_policy='learning')
        self.assertEqual('armed_codex_reviewed', f.arm()['status'])

    def step(self, before, after):
        f = self.f
        prepared = f.session.handle(before); self.assertEqual('prepared', prepared['status'], prepared)
        sent = f.send(prepared); self.assertEqual('awaiting_fresh_review', sent['status'], sent)
        f.now += timedelta(seconds=1)
        result = f.session.handle({'operation':'verify','action_id':prepared['action_id'],
            'operation_id':str(uuid4()),'after':after,'telemetry':{'zone_coverage':'complete'}})
        self.assertEqual('verified', result['status'], result)
        return result

    def test_observed_nonhand_route_then_hand_focus_selection_and_real_card_effect(self):
        f = self.f
        packets = [f.before,
            f.combat_request(1, focus=None, ui_override=away('relic','relic:burning-blood')),
            f.combat_request(2, focus=None, ui_override=away('player_status','player:frail')),
            f.combat_request(3, focus='d', ui_override={'tooltip_kind':'card_keyword'}),
            f.combat_request(4, focus='s'),
            f.combat_request(5, focus='s', ui_override={'phase':'targeting','selected_card_id':'s',
                'focused_target_id':'enemy','target_order':['enemy']})]
        game = context(); game['state']['hand'] = game['state']['hand'][1:]
        game['state']['energy'] = 0; game['state']['enemies'][0]['hp'] = 6
        packets.append(f.combat_request(6, game=game))
        results = [self.step(packets[i],packets[i+1]) for i in range(len(packets)-1)]
        self.assertEqual([['down'],['down'],['down'],['left'],['cross'],['cross']], [v['buttons'] for v in f.controller.inputs])
        self.assertEqual([False]*5+[True], [r['logical_action_complete'] for r in results])
        self.assertFalse(results[0]['focus_transition']['returned_to_hand'])
        self.assertTrue(results[2]['focus_transition']['returned_to_hand'])
        self.assertTrue(f.session.armed)
        self.assertIsNone(f.session.state['pending'])
        actual = json.loads(f.decisions()[-1]['actual_outcome_json'])
        self.assertEqual('verified', actual['status'])

    def test_unchanged_down_records_no_progress_then_circle_is_available_once(self):
        f = self.f
        after = f.combat_request(1, focus=None, ui_override=away())
        first = self.step(f.before,after)
        self.assertEqual('unchanged', first['focus_transition']['effect'])
        self.assertFalse(first['focus_transition']['returned_to_hand'])
        repeated = f.session.handle(after)
        self.assertEqual('recoverable_review', repeated['status'])
        self.assertTrue(repeated['armed'])
        after['reading']['ui']['recovery_direction'] = 'circle'
        hand = f.combat_request(2, focus='d')
        second = self.step(after,hand)
        self.assertTrue(second['focus_transition']['returned_to_hand'])
        self.assertEqual([['down'],['circle']], [v['buttons'] for v in f.controller.inputs])

    def test_circle_without_verified_down_and_repeat_pending_are_rejected(self):
        f = self.f
        early = deepcopy(f.before); early['reading']['ui']['recovery_direction'] = 'circle'
        rejected = f.session.handle(early)
        self.assertEqual('recoverable_review', rejected['status'])
        self.assertEqual([], f.controller.inputs)
        prepared = f.prepare(); f.send(prepared)
        rejected = f.send(prepared)
        self.assertEqual('recoverable_review', rejected['status'])
        self.assertEqual(1, len(f.controller.inputs))
        self.assertEqual('attempted', f.session.state['pending']['status'])

    def test_identical_independent_after_capture_logs_unchanged_focus(self):
        f = self.f
        (f.root/'fixture-1.png').write_bytes((f.root/'fixture-0.png').read_bytes())
        after = f.combat_request(1, focus=None, ui_override=away())
        result = self.step(f.before,after)
        self.assertEqual('unchanged', result['focus_transition']['effect'])
        self.assertFalse(result['logical_action_complete'])
        self.assertEqual('resolved', f.decisions()[0]['status'])

    def test_focus_recovery_result_cannot_change_hp_or_play_a_card(self):
        f = self.f; prepared = f.prepare(); f.send(prepared); f.now += timedelta(seconds=1)
        game = context(); game['state']['hp'] -= 1
        bad = f.combat_request(1, focus=None, game=game, ui_override=away('relic','relic:burning-blood'))
        result = f.session.handle({'operation':'verify','action_id':prepared['action_id'],
            'operation_id':str(uuid4()),'after':bad})
        self.assertEqual('recoverable_review', result['status'])
        self.assertEqual('attempted', f.session.state['pending']['status'])
        self.assertEqual(1, len(f.controller.inputs))

    def test_identical_pixels_cannot_claim_hand_return_or_another_domain(self):
        f = self.f; prepared = f.prepare(); f.send(prepared); f.now += timedelta(seconds=1)
        (f.root/'fixture-1.png').write_bytes((f.root/'fixture-0.png').read_bytes())
        for override, focus in ((away('relic','relic:burning-blood'),None), ({},'d')):
            after = f.combat_request(1, focus=focus, ui_override=override)
            result = f.session.handle({'operation':'verify','action_id':prepared['action_id'],
                'operation_id':str(uuid4()),'after':after})
            self.assertEqual('recoverable_review', result['status'])
            self.assertIn('identical pixels', result['error'])
            self.assertEqual('attempted', f.session.state['pending']['status'])
        self.assertEqual(1, len(f.controller.inputs))


if __name__ == '__main__':
    unittest.main()
