"""Intent-category checks, not pixel recognition or live-game validation."""
from dataclasses import replace
import unittest

from veda.vision import (
    StructuredGameState, VisibleEnemy, combat_action_readiness,
    explicit_nonattack_intent,
)


def frame(intent='Unknown (not attacking)', **overrides):
    enemy = VisibleEnemy('The Collector', 282, 282, intent, block=0,
                         intent_hits=(), intent_total_damage=0,
                         intent_damage_confidence=1)
    return StructuredGameState('COMBAT', 1, ascension=2, hp=35, max_hp=80,
        energy=3, block=10, hand=('Strike+',), hand_complete=True,
        player_vulnerable=0, end_turn_damage=0, end_turn_damage_confidence=1,
        enemies=(replace(enemy, **overrides),))


class NonattackObservationTests(unittest.TestCase):
    def test_readable_nonattack_category_does_not_require_exact_move(self):
        for text in ('Unknown (not attacking)', 'unknown_not_attacking',
                     "This enemy's intentions are Unknown (not attacking).",
                     "This enemy’s intentions are Unknown (not attacking)."):
            with self.subTest(text=text):
                self.assertTrue(explicit_nonattack_intent(text))
                self.assertTrue(combat_action_readiness(frame(text)).ready)
                self.assertEqual(frame(text).enemies[0].intent, text)

    def test_missing_or_bare_unknown_never_proves_nonattack(self):
        for text in (None, '', 'Unknown', 'Unknown intent', '?'):
            with self.subTest(text=text):
                self.assertFalse(explicit_nonattack_intent(text))
                self.assertFalse(combat_action_readiness(frame(text)).ready)

    def test_readable_category_does_not_fill_missing_or_conflicting_numbers(self):
        for values in ({'intent_hits': None}, {'intent_hits': (0,)},
                       {'intent_hits': (7,), 'intent_total_damage': 7},
                       {'intent_total_damage': None}, {'intent_total_damage': 7},
                       {'intent_damage_confidence': .5}):
            with self.subTest(values=values):
                self.assertFalse(combat_action_readiness(frame(**values)).ready)

    def test_nonattack_category_does_not_mask_unread_second_enemy(self):
        observed = frame()
        unread = VisibleEnemy('Torch Head', 40, 40, None, block=0)
        self.assertFalse(combat_action_readiness(
            replace(observed, enemies=observed.enemies + (unread,))).ready)
