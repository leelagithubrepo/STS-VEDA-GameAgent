from copy import deepcopy
import unittest

from veda.map_survey import merge_survey, plan_routes
from tests.test_map_survey import SurveyFixture, edge, make_view, node


class MapPlanTests(SurveyFixture):
    def review(self, *, hp=80, health="comfortable", elites="ready"):
        def inventory(names):
            return {"status": "complete", "items": [{"name": name, "count": count} for name, count in names],
                    "evidence_note": "Actual reviewed inventory fixture", "view_ids": ["bottom"]}
        return {"schema": "veda.map-plan-review.v1", "run_id": "test-run", "act": 1, "ascension": 2, "current_node_id": "current",
                "reviewer": "Test reviewer", "resources": {"hp": hp, "max_hp": 80, "gold": 99,
                    "deck": inventory([("Strike", 5), ("Defend", 4), ("Bash+", 1)]),
                    "relics": inventory([("Burning Blood", 1)]), "potions": inventory([]),
                    "evidence_note": "HUD reviewed", "view_ids": ["bottom"]},
                "readiness": {"health": health, "elites": elites, "shop": "useful",
                              "rationale": f"Reviewed {hp}/80 HP, Bash+ starter deck, Burning Blood and no potions; fixture assessment"},
                "criteria": ["merchant_access", "fewest_elites"], "boss_preparation": {"expected_name": None, "priorities": []}}

    def routes(self):
        nodes = [node("current", 0), node("left", 1, 0), node("middle", 1, 1), node("right", 1, 2),
                 node("elite", 2, 0, "elite"), node("rest", 2, 1, "rest"), node("fight", 2, 2),
                 node("merchant", 3, 0, "merchant"), node("rest2", 3, 2, "rest"), node("boss", 4, 0, "boss")]
        edges = [edge("current", name) for name in ("left", "middle", "right")]
        edges += [edge("left", "elite"), edge("elite", "merchant"), edge("merchant", "boss"),
                  edge("middle", "rest"), edge("rest", "boss"), edge("right", "fight"),
                  edge("fight", "rest2"), edge("rest2", "boss")]
        view = make_view(self.source, "bottom", nodes, edges, top=True, bottom=True, rows=range(5))
        return merge_survey(self.draft([view]))

    def test_three_cropped_choices_are_returned_without_boss_or_full_map(self):
        plan = plan_routes(merge_survey(self.draft([self.bottom()])), self.review())
        self.assertTrue(plan["immediate_choice_available"])
        self.assertEqual(plan["recommended_next_node_ids"], ["left", "middle", "right"])
        self.assertFalse(plan["full_route_planning_complete"])
        self.assertFalse(plan["controller_authorized"])
        self.assertTrue(plan["fresh_action_review_required"])

    def test_health_review_changes_conservative_priorities_and_actual_path(self):
        survey = self.routes()
        high = plan_routes(survey, self.review())
        low = plan_routes(survey, self.review(hp=9, health="critical", elites="avoid"))
        self.assertEqual(high["recommended_next_node_ids"], ["left"])
        self.assertEqual(low["recommended_next_node_ids"], ["middle"])
        self.assertEqual(low["applied_criteria"][:3], ["avoid_forced_elite", "elite_free_rest", "earliest_rest"])
        self.assertEqual(low["options"][0]["preferred_continuations"][0]["node_ids"], ["middle", "rest", "boss"])
        self.assertEqual(low["resources"]["relics"]["items"], [{"name": "Burning Blood", "count": 1}])
        self.assertNotIn("expected_damage", str(low))

    def test_elite_count_counts_a_path_not_all_optional_branches(self):
        survey = self.routes()
        # New optional elite branch from the rest route must not make that elite
        # unavoidable or count it on the existing direct boss continuation.
        survey["edges"].append(edge("rest", "merchant"))
        review = self.review()
        review["criteria"] = ["fewest_elites", "earliest_rest"]
        plan = plan_routes(survey, review)
        self.assertEqual(plan["recommended_next_node_ids"], ["middle"])
        self.assertEqual(plan["options"][0]["criteria"]["fewest_elites"]["known_elite_count_on_this_path"], 0)

    def test_expected_boss_prep_does_not_confirm_current_encounter(self):
        survey = self.routes()
        survey["expected_boss"] = {"name": "Hexaghost", "confidence": .99, "basis": "reviewed_map_portrait",
                                   "view_id": "bottom", "evidence_note": "portrait", "encounter_confirmed": False}
        review = self.review()
        review["boss_preparation"] = {"expected_name": "Hexaghost", "priorities": [
            {"need": "review available damage scaling", "reason": "Reviewer-selected preparation priority",
             "reference": "data/hexaghost_reference_pack.json; verify applicable ascension"}]}
        plan = plan_routes(survey, review)
        self.assertFalse(plan["encounter_confirmed"])
        self.assertFalse(plan["expected_boss"]["encounter_confirmed"])
        self.assertEqual(plan["research_targets"]["expected_boss"], "Hexaghost")
        self.assertIn("card or relic reward", plan["replan_after"])
        review["boss_preparation"]["expected_name"] = "The Guardian"
        with self.assertRaisesRegex(ValueError, "must match"):
            plan_routes(survey, review)

    def test_missing_relics_field_is_not_silently_an_empty_inventory(self):
        review = self.review()
        del review["resources"]["relics"]
        with self.assertRaises(ValueError):
            plan_routes(self.routes(), review)

    def test_unknown_inventory_is_explicit_warning_and_does_not_block_confirmed_choice(self):
        review = self.review()
        review["resources"]["potions"] = {"status": "unknown", "items": [], "view_ids": [],
                                          "evidence_note": "Potion bar not inspected"}
        plan = plan_routes(self.routes(), review)
        self.assertIn("potions_inventory_unknown", plan["warnings"])
        self.assertTrue(plan["immediate_choice_available"])

    def test_ledger_inventory_does_not_require_deck_pixels_on_map(self):
        review = self.review()
        review["resources"]["deck"]["view_ids"] = []
        review["resources"]["deck"]["ledger_reference"] = {"run_id": "test-run", "baseline_id": "baseline-123", "event_ids": ["bash-upgrade-event"]}
        review["resources"]["deck"]["evidence_note"] = "Reviewed exact baseline and verified Bash replacement, not deck pixels on this map"
        plan = plan_routes(self.routes(), review)
        self.assertEqual(plan["resources"]["deck"]["ledger_reference"]["baseline_id"], "baseline-123")
        self.assertEqual(plan["research_targets"]["ascension"], 2)
        review["resources"]["deck"]["ledger_reference"]["run_id"] = "another-run"
        with self.assertRaisesRegex(ValueError, "different run"):
            plan_routes(self.routes(), review)

    def test_missing_or_invalid_ascension_and_unknown_items_are_rejected(self):
        for value in (None, -1, 21, True, "2"):
            review = self.review()
            review["ascension"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan_routes(self.routes(), review)
        review = self.review()
        review["resources"]["relics"]["status"] = "unknown"
        with self.assertRaisesRegex(ValueError, "cannot assert items"):
            plan_routes(self.routes(), review)

    def test_a20_first_map_boss_does_not_claim_second_identity(self):
        survey = self.routes()
        survey["act"] = 3
        review = self.review()
        review["act"], review["ascension"] = 3, 20
        plan = plan_routes(survey, review)
        self.assertTrue(plan["research_targets"]["second_act3_boss_unresolved"])
        self.assertTrue(any("second_act3_boss" in warning for warning in plan["warnings"]))

    def test_context_inventory_evidence_and_priority_validation(self):
        variants = []
        for key, value in (("run_id", "another-run"), ("act", 2), ("current_node_id", "rest"),
                           ("criteria", ["made-up-score"])):
            review = self.review()
            review[key] = value
            variants.append(review)
        review = self.review()
        review["resources"]["relics"]["view_ids"] = ["missing"]
        variants.append(review)
        for review in variants:
            with self.subTest(review=review), self.assertRaises(ValueError):
                plan_routes(self.routes(), review)

    def test_low_confidence_start_is_not_recommended_with_supported_siblings(self):
        view = self.bottom()
        view["nodes"][1]["confidence"] = .5
        plan = plan_routes(merge_survey(self.draft([view])), self.review())
        self.assertTrue(plan["immediate_choice_available"])
        self.assertEqual(plan["recommended_next_node_ids"], ["middle", "right"])
        self.assertEqual(plan["unverified_next_nodes"][0]["node_id"], "left")

    def test_no_input_mutation(self):
        survey, review = self.routes(), self.review()
        before = deepcopy((survey, review))
        plan_routes(survey, review)
        self.assertEqual((survey, review), before)


if __name__ == "__main__":
    unittest.main()
