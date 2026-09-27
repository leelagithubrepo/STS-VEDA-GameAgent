"""Action prefixes are not implicit End Turn; synthetic contracts only."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import unittest

from tests import test_reviewed_play as reviewed_fixture
from tests import test_routine_current_deck as routine_fixture
from tests import test_execution as execution_fixture
from veda.advisory import boss_manifest, check_plan
from veda.calibration import CalibrationReport
from veda.execution import ExecutionLoop, Reading, RUNTIME_FIELDS, RuntimeStop


def defense_context():
    c = execution_fixture.context()
    c['state'].update(hp=5, energy=2, block=0)
    c['state']['hand'] = [routine_fixture.card('Defend', kind='Skill', ident=ident)
                          for ident in ('first', 'second')]
    c['state']['enemies'][0].update(intent='attack 10', intent_hits=[10])
    return c


def play(ident='first'):
    return {'kind': 'card', 'card_id': ident}


def prefix(c, actions=None, **extra):
    return check_plan(c, {'steps': actions or [play()], **extra}, survival_scope='action_prefix')


class ActionPrefixTests(unittest.TestCase):
    def test_default_remains_strict_but_first_defend_is_a_legal_prefix(self):
        c = defense_context(); original = deepcopy(c)
        strict = check_plan(c, {'steps': [play()]})
        checked = prefix(c)
        self.assertFalse(strict['allowed'])
        self.assertTrue(checked['allowed'], checked['reasons'])
        self.assertEqual(checked['forecast']['player_hp'], 0)
        self.assertEqual(checked['forecast']['block'], 5)
        self.assertEqual(checked['forecast']['horizon'], 'if_turn_ended_now')
        self.assertFalse(checked['forecast']['survival_required'])
        self.assertTrue(strict['forecast']['survival_required'])
        self.assertEqual(c, original)
        self.assertEqual(c['state']['hp'], 5, 'hypothetical turn HP is not immediate card HP')

    def test_plan_payload_cannot_enable_prefix_or_forgive_actual_end_turn(self):
        c = defense_context()
        self.assertFalse(check_plan(c, {'steps': [play()], 'survival_scope': 'action_prefix'})['allowed'])
        for mode in (None, True, 'best_effort', 'ACTION_PREFIX', []):
            with self.subTest(mode=mode):
                self.assertFalse(check_plan(c, {'steps': [play()]}, survival_scope=mode)['allowed'])
        self.assertFalse(prefix(c, [play()], claims_lethal=True)['allowed'])

    def test_reobserve_defense_then_end_turn_still_requires_sufficient_block(self):
        c = defense_context()
        # Freshly observed first result, not an assumed automatic second play.
        c['state'].update(block=5, energy=1)
        c['state']['hand'] = c['state']['hand'][1:]
        ending = prefix(c, [{'kind': 'end_turn'}])
        self.assertFalse(ending['allowed'])
        self.assertEqual(ending['forecast']['horizon'], 'committed_enemy_turn')
        self.assertTrue(ending['forecast']['survival_required'])
        second = prefix(c, [play('second')])
        self.assertTrue(second['allowed'], second['reasons'])
        self.assertEqual(second['forecast']['player_hp'], 5)
        c['state'].update(block=10, energy=0, hand=[])
        ending = prefix(c, [{'kind': 'end_turn'}])
        self.assertTrue(ending['allowed'], ending['reasons'])
        self.assertEqual(ending['forecast']['player_hp'], 5)

    def test_complete_two_defend_line_default_remains_survivable(self):
        c = defense_context()
        checked = check_plan(c, {'steps': [play(), play('second')]})
        self.assertTrue(checked['allowed'], checked['reasons'])
        self.assertEqual(checked['forecast']['player_hp'], 5)
        self.assertTrue(checked['forecast']['survival_required'])
        # The keyword does not allow an explicit premature ending in a prefix.
        self.assertFalse(prefix(c, [play(), {'kind': 'end_turn'}])['allowed'])

    def test_forced_time_warp_twelfth_still_requires_survival(self):
        c = defense_context()
        c['encounter_type'] = 'boss'
        c['boss_manifest'] = boss_manifest('Time Eater', 1)
        c['state']['ascension'] = 1
        c['state']['counters'] = {'time_warp': 11}
        c['state']['enemies'][0].update(name='Time Eater', hp=200, max_hp=456,
            move='Reverberate', intent='attack 7x3', intent_hits=[7, 7, 7], strength=0)
        checked = prefix(c)
        self.assertFalse(checked['allowed'])
        self.assertEqual(checked['forecast']['horizon'], 'committed_enemy_turn')
        self.assertTrue(checked['forecast']['survival_required'])
        self.assertEqual(checked['forecast']['incoming_displayed'], 27)
        c['state']['block'] = 30
        self.assertTrue(prefix(c)['allowed'])
        c['state']['counters']['time_warp'] = 10
        c['state']['block'] = 0
        checked = prefix(c)
        self.assertTrue(checked['allowed'], checked['reasons'])
        self.assertEqual(checked['forecast']['horizon'], 'if_turn_ended_now')

    def test_immediate_hp_costs_and_already_dead_player_still_reject(self):
        for name, hp, cost, kind in [('Hemokinesis', 2, 1, 'Attack'),
                                     ('Offering', 6, 0, 'Skill'),
                                     ('Bloodletting', 3, 0, 'Skill')]:
            with self.subTest(name=name):
                c = defense_context(); c['state']['hp'] = hp
                c['state']['block'] = 999
                c['state']['hand'] = [routine_fixture.card(name, cost=cost, kind=kind, ident='first')]
                checked = prefix(c, [dict(play(), target='enemy')])
                self.assertFalse(checked['allowed'])
                self.assertTrue(any('HP cost' in x for x in checked['reasons']))
        c = defense_context(); c['state']['hp'] = 0
        self.assertFalse(prefix(c)['allowed'], 'prefix cannot revive a dead player')

    def test_unknown_required_evidence_is_not_dropped_by_prefix_scope(self):
        mutations = [lambda c: c.update(fresh=False),
            lambda c: c.update(unknowns=['current modifier unread']),
            lambda c: c['state'].update(hand_complete=False),
            lambda c: c['state'].update(powers_complete=False),
            lambda c: c['state'].update(strength=None),
            lambda c: c['inventory']['coverage'].update(relic='unknown'),
            lambda c: c['inventory']['coverage'].update(potion='unknown'),
            lambda c: c['state']['enemies'][0].update(intent='?', intent_hits=None),
            lambda c: c['state']['hand'][0].update(cost=None)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                c = defense_context(); mutate(c)
                self.assertFalse(prefix(c)['allowed'])

    def test_observation_boundaries_and_potion_reviews_remain_required(self):
        c = defense_context()
        c['state']['hand'][0] = routine_fixture.card('True Grit', kind='Skill', ident='first')
        checked = prefix(c)
        self.assertTrue(checked['allowed'], checked['reasons'])
        self.assertTrue(checked['steps'][0]['observe_after'])
        self.assertIsNone(checked['forecast'])
        self.assertFalse(prefix(c, [play(), play('second')])['allowed'])
        c['state']['hand'][0] = routine_fixture.card('Hemokinesis', ident='first')
        c['inventory']['current']['potion'] = ['Block Potion']
        checked = prefix(c, [dict(play(), target='enemy')])
        self.assertFalse(checked['allowed'])
        self.assertTrue(any('review available Block Potion' in x for x in checked['reasons']))


class ReviewedPrefixTests(unittest.TestCase):
    def setUp(self):
        self.f = reviewed_fixture.ReviewedPlayTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.c = defense_context()
        self.f.before = self.request(0, self.c, 'first')
        self.f.create(); self.f.arm()

    def request(self, number, game, ident):
        value = self.f.combat_request(number, focus=ident, game=game)
        value['plan'] = {'steps': [play(ident)]}
        return value

    def verify(self, prepared, number, game, ident):
        self.f.now = self.f.base_time + timedelta(seconds=number)
        after = self.request(number, game, ident)
        return self.f.session.handle({'operation': 'verify', 'action_id': prepared['action_id'],
            'operation_id': str(uuid4()), 'after': after}), after

    def test_fake_first_defend_verifies_immediate_hp_then_fresh_second_defend(self):
        f = self.f
        prepared = f.prepare()
        checked = f.session.state['pending']['proposal']['checked']
        self.assertEqual(checked['forecast']['player_hp'], 0)
        self.assertEqual(checked['survival_scope'], 'action_prefix')
        f.send(prepared)
        self.assertEqual(len(f.controller.inputs), 1)
        self.assertEqual(f.session.state['pending']['status'], 'attempted')
        after_first = deepcopy(self.c)
        after_first['state'].update(block=5, energy=1)
        after_first['state']['hand'] = after_first['state']['hand'][1:]
        result, fresh = self.verify(prepared, 1, after_first, 'second')
        self.assertEqual(result['status'], 'verified')
        self.assertIsNone(f.session.state['pending'])
        self.assertEqual(fresh['reading']['context']['state']['hp'], 5)
        second = f.session.handle(fresh)
        self.assertNotEqual(fresh['source']['sha256'], f.before['source']['sha256'])
        self.assertGreater(fresh['source']['captured_at'], f.before['source']['captured_at'])
        self.assertEqual(f.session.state['pending']['request']['source']['sha256'], fresh['source']['sha256'])
        f.send(second)
        after_second = deepcopy(after_first)
        after_second['state'].update(block=10, energy=0, hand=[])
        result, _ = self.verify(second, 2, after_second, None)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(len(f.controller.inputs), 2)
        self.assertTrue(all(row['status'] == 'resolved' for row in f.decisions()))

    def test_pending_first_input_cannot_be_bypassed_for_second(self):
        f = self.f; prepared = f.prepare(); f.send(prepared)
        with self.assertRaises(RuntimeStop):
            f.session.handle(self.request(1, self.c, 'second'))
        self.assertEqual(len(f.controller.inputs), 1)
        self.assertEqual(f.session.state['pending']['status'], 'attempted')


class ExecutionPrefixTests(unittest.TestCase):
    def test_final_single_action_guard_uses_prefix_but_retains_required_facts(self):
        c = defense_context()
        raw = execution_fixture.reading(c, {'phase': 'hand'})
        raw.update(frame_id='synthetic-frame', image_sha256='a' * 64)
        reading = Reading.from_dict(raw)
        plan = SimpleNamespace(ready=True, next_action=play(), reasons=())
        loop = ExecutionLoop(None, None, SimpleNamespace(offline=True),
            calibration=CalibrationReport({field: 1.0 for field in RUNTIME_FIELDS}, 12),
            run_id='fixture-run', planner=lambda *a, **k: plan)
        action, checked = loop._choose(reading)
        self.assertEqual(action, play())
        self.assertTrue(checked['allowed'])
        self.assertEqual(checked['forecast']['player_hp'], 0)
        reading.context['unknowns'] = ['unread relevant status']
        with self.assertRaises(RuntimeStop):
            loop._choose(reading)
        reading.context['unknowns'] = []
        plan.next_action = {'kind': 'end_turn'}
        with self.assertRaises(RuntimeStop):
            loop._choose(reading)


if __name__ == '__main__':
    unittest.main()
