"""Coverage wording must not turn visible depictions into a playable hand."""
import unittest

from veda.saved_frame_coverage import describe_coverage


class CoverageTests(unittest.TestCase):
    def describe(self, candidates=(), **cards):
        return describe_coverage(hud={"hp": 17, "energy": 0},
            cards={"card_candidates": list(candidates), **cards},
            combat={"player_block": 0, "enemy_hp_candidates": [{"hp": 19}], "incoming_damage": None})

    def test_zero_is_read_but_no_depictions_does_not_mean_empty_hand(self):
        result = self.describe()
        self.assertIn("energy", result["read_fields"])
        self.assertIn("player_block", result["read_fields"])
        self.assertIsNone(result["hand"]["card_count"])
        self.assertIsNone(result["hand"]["complete"])
        self.assertIn("no_lower_band_depictions_read_is_not_an_empty_hand", result["hand"]["issues"])
        self.assertFalse(result["combat_ready"])

    def test_repeated_names_are_depictions_not_unique_or_ordered_hand(self):
        result = self.describe([
            {"location": "hand_band", "name": "Strike", "current_cost": 1},
            {"location": "hand_band", "name": "Strike", "current_cost": None},
            {"location": "hand_band", "name": None, "current_cost": 0},
            {"location": "popup_or_nonhand", "name": "Bash+", "current_cost": 2},
        ], menu_text_present=True)
        hand = result["hand"]
        self.assertEqual((4,3,1,2,2), tuple(hand[k] for k in
            ("candidate_depictions", "lower_band_depictions", "popup_or_nonhand_depictions",
             "named_lower_band_depictions", "costed_lower_band_depictions")))
        self.assertIsNone(hand["ordered_hand"])
        self.assertIn("popup_or_nonhand_depiction_present", hand["issues"])
        self.assertIn("menu_text_present", hand["issues"])

    def test_processing_and_budget_gaps_are_disclosed(self):
        hand = self.describe(refinement={"header_region_budget_omissions": [19],
            "title_region_budget_omissions": [8], "cost_region_budget_omissions": [9],
            "hand_discovery_evidence": {"status": "region_failed"}})["hand"]
        self.assertIn("header_refinement_budget_exhausted", hand["issues"])
        self.assertIn("title_refinement_budget_exhausted", hand["issues"])
        self.assertIn("cost_refinement_budget_exhausted", hand["issues"])
        self.assertIn("wide_hand_discovery_unavailable", hand["issues"])

    def test_cannot_claim_complete_hand_or_arithmetic_readiness(self):
        for complete in (True, 1, 0):
            with self.subTest(complete=complete), self.assertRaises(ValueError):
                self.describe(hand_complete=complete)
        for combat in ({"incoming_damage": 0}, {"combat_ready": True},
                       {"runtime_authorized": True}, {"controller_authorized": True}):
            with self.subTest(combat=combat), self.assertRaises(ValueError):
                describe_coverage(hud={}, cards={}, combat=combat)

    def test_processed_discovery_can_still_have_an_unexamined_tail(self):
        result = self.describe(refinement={"hand_discovery_evidence": {
            "status": "processed", "detector_limits_reached": ["candidate bound exceeded"],
            "unexamined_candidate_count": None}})
        self.assertIn("wide_hand_detector_left_unevaluated_evidence", result["hand"]["issues"])
        self.assertIsNone(result["hand"]["complete"])

    def test_missing_optional_extractor_has_no_numeric_claim(self):
        result = describe_coverage(hud={}, cards={}, combat=None)
        self.assertEqual([], result["read_fields"])
        self.assertEqual(0, result["enemy_health_candidate_count"])
        self.assertIn("enemy_intents_and_hits", result["unresolved_requirements"])

    def test_individual_attack_numbers_do_not_establish_complete_incoming_damage(self):
        combat = {"intent_evidence": {"attack_candidates": [{"damage_per_hit": 6, "hits": 2}],
                  "unconfirmed_attack_candidates": [{"damage_per_hit": 9}],
                  "supported_attack_subtotal": 12, "incoming_damage": None}}
        result = describe_coverage(hud={}, cards={}, combat=combat)
        self.assertEqual(1, result["attack_candidate_count"])
        self.assertEqual(1, result["unconfirmed_attack_candidate_count"])
        self.assertFalse(result["intent_coverage_complete"])
        self.assertFalse(result["combat_ready"])
        combat["intent_evidence"]["incoming_damage"] = 12
        with self.assertRaisesRegex(ValueError, "unsupported_intent_readiness_claim"):
            describe_coverage(hud={}, cards={}, combat=combat)

    def test_nested_intent_roster_cannot_claim_completeness(self):
        for complete in (True, 1, 0):
            with self.subTest(complete=complete), self.assertRaisesRegex(ValueError, "unsupported_intent_readiness_claim"):
                describe_coverage(hud={}, cards={}, combat={"intent_evidence": {"enemy_roster_complete": complete}})


if __name__ == "__main__": unittest.main()
