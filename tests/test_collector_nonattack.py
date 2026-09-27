"""Synthetic A2 category proofs; no screenshot recognition or console execution."""
from copy import deepcopy
import unittest

from veda.advisory import (
    COLLECTOR_EFFECTS, COLLECTOR_NONATTACK_CANDIDATES,
    COLLECTOR_NONATTACK_EFFECTS, COLLECTOR_NONATTACK_MOVE,
    check_plan, collector_turn_bound, reviewed_collector_roster, validate_snapshot,
)
from veda.routine_combat import _boundary_rank, plan_routine_combat
from tests.test_advisory_current_deck import card
from tests.test_advisory_current_deck import context as ordinary_context
from tests.test_collector_turns import turn
from tests.test_routine_current_deck import observation, report


def category(*, torches=0, label='Unknown (not attacking)', hand=None):
    context = turn('Spawn', torches=torches, hand=hand)
    context['state']['enemies'][0].update(
        move=COLLECTOR_NONATTACK_MOVE, intent=label,
        intent_effects=deepcopy(COLLECTOR_NONATTACK_EFFECTS))
    return context


def end(context):
    return check_plan(context, {'steps': [{'kind': 'end_turn'}]})


def exact_candidate(context, move):
    state = deepcopy(context['state'])
    state['enemies'][0].update(move=move, intent=move,
                             intent_effects=deepcopy(COLLECTOR_EFFECTS[move]))
    return collector_turn_bound(state, context['inventory']['current']['relic'])


class CollectorNonattackTests(unittest.TestCase):
    def test_explicit_labels_preserve_unknown_exact_move_and_nonzero_survival_bound(self):
        for label in ('Unknown (not attacking)', 'unknown_not_attacking',
                      "This enemy's intentions are unknown (not attacking)."):
            with self.subTest(label=label):
                c = category(label=label)
                before = deepcopy(c)
                validate_snapshot(c['state'])
                self.assertTrue(reviewed_collector_roster(c['state']))
                result = end(c)
                self.assertTrue(result['allowed'], result['reasons'])
                f = result['forecast']
                self.assertEqual((f['incoming_displayed'], f['incoming_upper_bound']), (0, 14))
                self.assertEqual(f['player_hp_lower_bound'], 66)
                self.assertIsNone(f['boss_move_observed'])
                self.assertEqual(f['observed_intent'], label)
                self.assertEqual(f['observed_intent_category'], COLLECTOR_NONATTACK_MOVE)
                self.assertEqual(f['candidate_moves'], list(COLLECTOR_NONATTACK_CANDIDATES))
                self.assertEqual(f['aggregation'], 'maximum_of_alternative_moves')
                self.assertTrue(all('boss_move_observed' not in b for b in f['candidate_bounds']))
                self.assertEqual(len(f['enemies']), 1, 'never invent future summoned enemies')
                self.assertEqual(c, before)

    def test_union_matches_worst_existing_branch_across_rosters_and_modifiers(self):
        for torches in range(3):
            for weak in (0, 1):
                for vulnerable in (0, 1):
                    for strength in (-2, 0, 6):
                        for thorns in (False, True):
                            with self.subTest(torches=torches, weak=weak, vulnerable=vulnerable,
                                              strength=strength, thorns=thorns):
                                c = category(torches=torches)
                                s = c['state']; s['vulnerable'] = vulnerable
                                if thorns:
                                    c['inventory']['current']['relic'] = ['Bronze Scales']
                                    s['powers'] = {'Thorns': 3}
                                for enemy in s['enemies']:
                                    enemy['strength'] = strength
                                    if enemy['name'] == 'Torch Head':
                                        enemy.update(weak=weak, hp=3 if thorns else 40,
                                            intent_hits=[max(0, 7 + strength)
                                                * (3 if weak else 4) * (3 if vulnerable else 2) // 8])
                                before = deepcopy(c)
                                bound = collector_turn_bound(s, c['inventory']['current']['relic'])
                                candidates = [exact_candidate(c, move) for move in COLLECTOR_NONATTACK_CANDIDATES]
                                self.assertTrue(all(b is not None for b in candidates))
                                self.assertEqual(bound['incoming_upper_bound'],
                                                 max(b['incoming_upper_bound'] for b in candidates))
                                self.assertEqual(bound['summon_damage_upper_bound_per_slot'],
                                    max(b['summon_damage_upper_bound_per_slot'] for b in candidates
                                        if b['summon_damage_upper_bound_per_slot'] is not None))
                                self.assertEqual([b['incoming_upper_bound'] for b in bound['candidate_bounds']],
                                                 [b['incoming_upper_bound'] for b in candidates])
                                self.assertEqual(c, before)

    def test_one_unbounded_candidate_withholds_entire_category(self):
        c = category(torches=1)
        c['state']['vulnerable'] = 1
        c['state']['enemies'][1]['intent_hits'] = [2]  # No integer raw hit floors through *1.5 to2.
        self.assertIsNotNone(exact_candidate(c, 'Spawn'))
        self.assertIsNone(exact_candidate(c, 'Buff'))
        self.assertIsNone(collector_turn_bound(c['state'], []))
        self.assertFalse(end(c)['allowed'])

    def test_bare_unknown_symbols_and_unread_labels_never_establish_category(self):
        for label in (None, '', 'Unknown', 'Unknown intent', '?', 'Special', 'not attacking', 'Spawn'):
            with self.subTest(label=label):
                c = category(label=label, hand=[card('Defend+', 'Skill')])
                self.assertFalse(reviewed_collector_roster(c['state']))
                self.assertIsNone(collector_turn_bound(c['state'], []))
                self.assertFalse(end(c)['allowed'])
                self.assertFalse(check_plan(c, {'steps': [{'kind': 'card', 'card_id': 'c1'}]})['allowed'])

    def test_missing_source_effects_ascension_roster_and_modifiers_reject_cards_too(self):
        mutations = {
            'source': lambda c: c['state']['enemies'][0]['evidence'].update(source='unknown'),
            'kind': lambda c: c['state']['enemies'][0]['evidence'].update(kind='predicted'),
            'not_observed': lambda c: c['state']['enemies'][0]['evidence'].update(observed_intent=False),
            'missing_effects': lambda c: c['state']['enemies'][0].pop('intent_effects'),
            'incomplete_roster': lambda c: c['state'].update(enemies_complete=False),
            'ascension': lambda c: c['state'].update(ascension=3),
            'missing_strength': lambda c: c['state']['enemies'][0].update(strength=None),
            'missing_vulnerable': lambda c: c['state'].update(vulnerable=None),
            'powers': lambda c: c['state'].update(powers_complete=False),
            'unsupported_relic': lambda c: c['inventory']['current'].update(relic=['Odd Mushroom']),
            'unmodeled': lambda c: c['state'].update(unmodeled_effects=['unread status']),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                c = category(hand=[card('Defend+', 'Skill')]); mutate(c)
                self.assertIsNone(collector_turn_bound(c['state'], c['inventory']['current']['relic']))
                self.assertFalse(end(c)['allowed'])
                self.assertFalse(check_plan(c, {'steps': [{'kind': 'card', 'card_id': 'c1'}]})['allowed'])

    def test_typed_union_is_closed_not_arbitrary_enemy_effect_disjunction(self):
        for effects in ([], [{'kind': 'one_of', 'moves': ['Spawn']}],
                        [{'kind': 'one_of', 'moves': [*COLLECTOR_NONATTACK_CANDIDATES, 'Fireball']}],
                        [{'kind': 'one_of', 'moves': list(COLLECTOR_NONATTACK_CANDIDATES), 'amount': 1}]):
            with self.subTest(effects=effects):
                c = category(); c['state']['enemies'][0]['intent_effects'] = effects
                self.assertFalse(reviewed_collector_roster(c['state']))
                self.assertFalse(end(c)['allowed'])
        for name in ('Time Eater', 'Torch Head', 'Cultist'):
            c = category(); c['state']['enemies'][0]['name'] = name
            with self.assertRaises(ValueError):
                validate_snapshot(c['state'])

    def test_attack_hits_contradict_observed_nonattack_even_if_zero(self):
        for hits in ([0], [18], None):
            with self.subTest(hits=hits):
                c = category(); c['state']['enemies'][0]['intent_hits'] = hits
                self.assertFalse(reviewed_collector_roster(c['state']))
                self.assertFalse(end(c)['allowed'])

    def test_check_plan_aligns_bare_unknown_gate_even_for_an_exact_typed_move(self):
        for label in ('Unknown', 'Unknown intent', '?'):
            c = turn('Spawn', torches=0); c['state']['enemies'][0]['intent'] = label
            self.assertFalse(end(c)['allowed'])

    def test_category_cannot_be_promoted_to_an_observed_exact_move(self):
        for move in COLLECTOR_NONATTACK_CANDIDATES:
            with self.subTest(move=move):
                c = turn(move, torches=0, hand=[card('Defend+', 'Skill')])
                c['state']['enemies'][0]['intent'] = 'Unknown (not attacking)'
                self.assertFalse(reviewed_collector_roster(c['state']))
                self.assertIsNone(collector_turn_bound(c['state'], []))
                self.assertFalse(end(c)['allowed'])
                self.assertFalse(check_plan(c, {'steps': [{'kind': 'card', 'card_id': 'c1'}]})['allowed'])

    def test_unknown_nonattack_category_is_not_generic_encounter_support(self):
        for name in ('Cultist', 'Torch Head', 'Time Eater'):
            with self.subTest(enemy=name):
                c = ordinary_context([card('Defend+', 'Skill')])
                c['state']['enemies'][0].update(name=name, intent='unknown_not_attacking',
                                              intent_hits=[], intent_effects=[])
                self.assertFalse(end(c)['allowed'])
                self.assertFalse(check_plan(c, {'steps': [{'kind': 'card', 'card_id': 'c1'}]})['allowed'])

    def test_card_and_end_turn_use_shared_category_bound_in_routine_planner(self):
        c = category(hand=[card('Defend+', 'Skill')])
        direct = check_plan(c, {'steps': [{'kind': 'card', 'card_id': 'c1'}]})
        self.assertTrue(direct['allowed'], direct['reasons'])
        self.assertEqual(direct['forecast']['incoming_upper_bound'], 14)
        self.assertEqual(direct['forecast']['player_hp_lower_bound'], 74)
        plan = plan_routine_combat(observation(c, act=2), report(), encounter_name='The Collector', context=c)
        self.assertTrue(plan.ready, plan.reasons)
        self.assertEqual(plan.next_action, {'kind': 'card', 'card_id': 'c1'})
        self.assertEqual(plan.prediction.incoming_damage, 14)
        c = category(); c['state']['energy'] = 0
        plan = plan_routine_combat(observation(c, act=2), report(), encounter_name='The Collector', context=c)
        self.assertTrue(plan.ready, plan.reasons)
        self.assertEqual(plan.next_action, {'kind': 'end_turn'})
        self.assertEqual(plan.prediction.incoming_damage, 14)

    def test_unsafe_end_turn_and_wrong_boss_manifest_still_reject(self):
        c = category(); c['state']['hp'] = 14
        self.assertFalse(end(c)['allowed'])
        c = category(); c['boss_manifest'] = {'name': 'The Collector'}
        self.assertFalse(end(c)['allowed'])
        c = category(); c['encounter_type'] = 'enemy'
        self.assertFalse(end(c)['allowed'])

    def test_torch_kill_rank_keeps_summon_slot_correction(self):
        c = category(torches=1, hand=[card('Strike+')])
        c['state']['enemies'][1].update(hp=5, weak=1, intent_hits=[5])
        bound = collector_turn_bound(c['state'], [])
        self.assertEqual(bound['incoming_upper_bound'], 12)
        self.assertEqual(bound['summon_damage_upper_bound_per_slot'], 7)
        rank = _boundary_rank(c['state'], c['state']['hand'][0], {'target': 'torch0'}, [])
        self.assertEqual(rank[1], -66)  # 12 upper bound + (new7 - old5) =14.

    def test_new_thorns_threshold_adds_replacement_without_double_counting(self):
        for move in (COLLECTOR_NONATTACK_MOVE, 'Spawn', 'Revive'):
            for hp, scales, expected_incoming in ((10, True, 21), (13, True, 14),
                                                   (3, True, 21), (10, False, 14)):
                with self.subTest(move=move, hp=hp, scales=scales):
                    c = (category(torches=1, hand=[card('Strike+')]) if move == COLLECTOR_NONATTACK_MOVE
                         else turn(move, torches=1, hand=[card('Strike+')]))
                    c['state']['enemies'][1]['hp'] = hp
                    relics = ['Bronze Scales'] if scales else []
                    c['inventory']['current']['relic'] = relics
                    c['state']['powers'] = {'Thorns': 3} if scales else {}
                    before = deepcopy(c)
                    rank = _boundary_rank(c['state'], c['state']['hand'][0], {'target': 'torch0'}, relics)
                    self.assertEqual(rank[1], -(80 - expected_incoming))
                    self.assertEqual(c, before)

    def test_new_thorns_replacement_can_withhold_unsafe_card_rank(self):
        c = category(torches=1, hand=[card('Strike+')])
        c['state']['hp'] = 20
        c['state']['enemies'][1]['hp'] = 10
        c['state']['powers'] = {'Thorns': 3}
        c['inventory']['current']['relic'] = ['Bronze Scales']
        self.assertEqual(collector_turn_bound(c['state'], ['Bronze Scales'])['incoming_upper_bound'], 14)
        self.assertIsNone(_boundary_rank(c['state'], c['state']['hand'][0], {'target': 'torch0'}, ['Bronze Scales']))

    def test_all_exact_move_results_remain_exact(self):
        for move in COLLECTOR_EFFECTS:
            c = turn(move)
            bound = collector_turn_bound(c['state'], [])
            self.assertIsNotNone(bound)
            self.assertEqual(bound['boss_move_observed'], move)
            self.assertNotIn('candidate_bounds', bound)


if __name__ == '__main__':
    unittest.main()
