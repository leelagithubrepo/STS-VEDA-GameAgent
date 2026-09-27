"""Damage-priority scenarios from explicit synthetic combat facts.

No controller, capture, model, or database is used. These tests verify the
chosen next card and fresh-observation boundary, not live recognition quality.
"""
from copy import deepcopy
import unittest

from tests import test_routine_current_deck as fixtures
from veda.combat import CardEffect, CombatEnemy, CombatSnapshot, choose_verified_sequence


def situation(hand, *, hp=50, energy=1, block=0, incoming=6, enemy_hp=50):
    context = fixtures.context(hand)
    context['state'].update(hp=hp, energy=energy, block=block)
    context['state']['enemies'][0].update(
        hp=enemy_hp, intent=f'attack {incoming}', intent_hits=[incoming])
    return context


class RoutineDamagePriorityTests(unittest.TestCase):
    def choose(self, context, **limits):
        before = deepcopy(context)
        result = fixtures.plan(context, **limits)
        self.assertEqual(context, before, 'ranking must not alter the reviewed input state')
        self.assertTrue(result.ready, result.reasons)
        self.assertEqual(result.next_action['kind'], 'card')
        self.assertEqual(len(result.sequence), 1, 'lookahead must emit only one next card')
        self.assertEqual(len(result.checked_result['steps']), 1,
                         'the executable check must describe only the next card')
        self.assertTrue(result.checked_result['allowed'])
        return result

    def test_defend_preserves_49_hp_over_strike_44_hp_regardless_of_hand_order(self):
        strike = fixtures.card('Strike', ident='strike')
        defend = fixtures.card('Defend', kind='Skill', ident='defend')
        for hand in ([strike, defend], [defend, strike]):
            with self.subTest(hand=[card['name'] for card in hand]):
                result = self.choose(situation(hand))
                self.assertEqual(result.next_action, {'kind': 'card', 'card_id': 'defend'})
                self.assertEqual(result.prediction.projected_player_hp, 49)
                self.assertEqual(result.prediction.enemies[0].hp, 50)
                self.assertEqual(result.checked_result['steps'][0]['energy_after'], 0)

    def test_lethal_strike_prevents_all_incoming_damage(self):
        context = situation([
            fixtures.card('Defend', kind='Skill', ident='defend'),
            fixtures.card('Strike', ident='strike'),
        ], enemy_hp=6)
        result = self.choose(context)
        self.assertEqual(result.next_action, {'kind': 'card', 'card_id': 'strike', 'target': 'enemy'})
        self.assertEqual(result.prediction.projected_player_hp, 50)
        self.assertTrue(result.checked_result['forecast']['lethal_to_enemies'])
        self.assertEqual(result.prediction.enemies[0].hp, 0)

    def test_equal_player_hp_prefers_offense_over_redundant_block(self):
        context = situation([
            fixtures.card('Defend', kind='Skill', ident='defend'),
            fixtures.card('Strike', ident='strike'),
        ], block=6)
        result = self.choose(context)
        self.assertEqual(result.next_action, {'kind': 'card', 'card_id': 'strike', 'target': 'enemy'})
        self.assertEqual(result.prediction.projected_player_hp, 50)
        self.assertEqual(result.prediction.enemies[0].hp, 44)

    def test_known_block_from_draw_boundary_beats_direct_attack_and_defend(self):
        context = situation([
            fixtures.card('Strike', ident='strike'),
            fixtures.card('Defend', kind='Skill', ident='defend'),
            fixtures.card('Shrug It Off', kind='Skill', ident='shrug'),
        ], incoming=10)
        result = self.choose(context)
        self.assertEqual(result.next_action, {'kind': 'card', 'card_id': 'shrug'})
        self.assertTrue(result.checked_result['steps'][0]['observe_after'])
        self.assertIsNone(result.prediction, 'unobserved draw must remain an observation boundary')
        self.assertIsNone(result.checked_result['forecast'])
        self.assertEqual(result.supported_card_count, 3)

    def test_boundary_self_damage_does_not_win_just_for_extra_enemy_damage(self):
        context = situation([
            fixtures.card('Hemokinesis+', ident='hemokinesis'),
            fixtures.card('Strike', ident='strike'),
        ], block=6)
        result = self.choose(context)
        self.assertEqual(result.next_action, {'kind': 'card', 'card_id': 'strike', 'target': 'enemy'})
        self.assertEqual(result.prediction.projected_player_hp, 50)
        self.assertEqual(result.prediction.enemies[0].hp, 44)

    def test_two_defend_lookahead_can_start_before_end_turn_is_safe(self):
        context = situation([
            fixtures.card('Strike', ident='strike'),
            fixtures.card('Defend', kind='Skill', ident='defend-a'),
            fixtures.card('Defend', kind='Skill', ident='defend-b'),
        ], hp=6, energy=2, incoming=12)
        result = self.choose(context, max_depth=2)
        self.assertIn(result.next_action['card_id'], {'defend-a', 'defend-b'})
        self.assertNotIn('target', result.next_action)
        self.assertEqual(result.sequence[0].name, 'Defend')
        self.assertEqual(result.checked_result['steps'][0]['energy_after'], 1)
        self.assertEqual(result.prediction.player_block, 5)
        self.assertEqual(result.prediction.projected_player_hp, -1,
                         'first-action evidence must not claim the second Defend has already resolved')
        self.assertFalse(result.checked_result['steps'][0]['observe_after'])
        self.assertEqual(result.prediction_horizon, 'if_turn_ended_now')

    def test_legacy_verified_selector_also_preserves_hp_then_uses_offense(self):
        candidates = (
            (CardEffect('Strike', 1, 'Attack', attack_damage=6, target='Cultist'),),
            (CardEffect('Defend', 1, 'Skill', block=5),),
        )
        for initial_block, enemy_hp, expected_hp, expected_block, expected_enemy_hp in (
            (0, 50, 49, 5, 50),
            (6, 50, 50, 6, 44),
            (0, 6, 50, 0, 0),
        ):
            with self.subTest(block=initial_block, enemy_hp=enemy_hp):
                snapshot = CombatSnapshot(energy=1, player_hp=50, player_block=initial_block,
                    incoming_damage=6, incoming_hits=(6,), end_turn_damage=0,
                    hand=('Strike', 'Defend'), enemies=(CombatEnemy('Cultist', enemy_hp),))
                before = deepcopy((snapshot, candidates))
                result = choose_verified_sequence(snapshot, candidates)
                self.assertEqual((snapshot, candidates), before)
                self.assertIsNotNone(result)
                self.assertTrue(result.legal)
                self.assertFalse(result.lethal)
                self.assertEqual(result.projected_player_hp, expected_hp)
                self.assertEqual(result.player_block, expected_block)
                self.assertEqual(result.enemies[0].hp, expected_enemy_hp)
                self.assertEqual(result.energy_spent, 1)


if __name__ == '__main__':
    unittest.main()
