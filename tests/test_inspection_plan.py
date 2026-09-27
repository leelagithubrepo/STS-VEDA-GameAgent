"""The inspection contract selects evidence views without guessing or input."""
from copy import deepcopy
import unittest

from veda.inspection_plan import plan_inspections


def complete(data):
    return {"data": data, "status": "current", "complete": True, "current": True,
            "evidence": [{"origin": "independent_annotation", "epoch": 3}], "invalidated_by": []}


def snapshot():
    return {"schema": "veda.evidence-ledger.v1", "epoch": 3,
            "context": {"run_id": "run", "floor_id": "20", "combat_id": "fight", "turn_id": "2"},
            "frame": {"frame_id": "frame", "image_sha256": "a"*64, "observed_at": 1},
            "sections": {
                "player": complete({"hp": 30, "energy": 1, "block": 0}),
                "hand": complete({"cards": [], "order": []}),
                "enemies": complete({"enemies": [], "target_order": []}),
                "statuses": complete({"player": {}, "enemies": {}}),
                "ui": complete({"phase": "combat", "focused_card_id": None,
                    "selected_card_id": None, "focused_target_id": None}),
                "piles": complete({"draw": [], "discard": [], "exhaust": [], "draw_order": None,
                    "coverage": {zone: "complete" for zone in ("draw", "discard", "exhaust")},
                    "zone_evidence": {zone: "all pages inspected" for zone in ("draw", "discard", "exhaust")}}),
                "inventory": {"data": {"coverage": {"card": "unknown", "relic": "complete", "potion": "complete"},
                    "current": {"card": [], "relic": [], "potion": []}},
                    "status": "partial", "complete": False, "current": True,
                    "evidence": [{"origin": "independent_annotation", "epoch": 3}], "invalidated_by": []}},
            "runtime_authorized": False, "controller_authorized": False}


class InspectionPlanTests(unittest.TestCase):
    def test_complete_current_requirements_do_not_repeat_inspection(self):
        state = snapshot()
        result = plan_inspections(state)
        self.assertFalse(result["decision_blocked"])
        self.assertEqual(result["requests"], [])
        self.assertNotIn("piles", result["required_sections"])
        self.assertIn("inventory", result["satisfied_requirements"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["controller_authorized"])

    def test_unknown_screen_gates_other_interactive_requests(self):
        state = snapshot()
        state["sections"] = {}
        result = plan_inspections(state, decision="long_fight_setup")
        self.assertEqual([r["view"] for r in result["requests"]], ["current_screen_and_focus"])
        self.assertEqual(len(result["gaps"]), 7)
        self.assertTrue(result["decision_blocked"])

    def test_stale_complete_hand_is_not_reused_and_reason_is_retained(self):
        state = snapshot()
        state["sections"]["hand"].update(status="stale", current=False, data=None,
            invalidated_by=["input:play_card"])
        before = deepcopy(state)
        result = plan_inspections(state)
        self.assertEqual(result["gaps"][0]["invalidated_by"], ["input:play_card"])
        self.assertEqual(result["requests"][0]["view"], "hand_and_focus_sequence")
        self.assertEqual(result["requests"][0]["refresh_after_inspection"], ["ui"])
        self.assertEqual(state, before)

    def test_unknown_input_invalidates_inventory_despite_old_coverage(self):
        state = snapshot()
        state["sections"]["inventory"].update(status="stale", current=False,
            invalidated_by=["unknown_input:unlogged_action"])
        result = plan_inspections(state)
        request = result["requests"][0]
        self.assertEqual(request["inventory_categories"], ["relic", "potion"])
        self.assertEqual(result["gaps"][0]["invalidated_by"], ["unknown_input:unlogged_action"])

    def test_only_unknown_inventory_category_is_requested(self):
        state = snapshot()
        state["sections"]["inventory"]["data"]["coverage"]["relic"] = "unknown"
        result = plan_inspections(state)
        self.assertEqual(result["requests"][0]["inventory_categories"], ["relic"])
        self.assertTrue(result["decision_blocked"])

    def test_empty_inventory_list_with_unknown_coverage_is_not_empty_proof(self):
        state = snapshot()
        state["sections"]["inventory"]["data"]["coverage"]["potion"] = "unknown"
        result = plan_inspections(state)
        self.assertEqual(result["requests"][0]["inventory_categories"], ["potion"])

    def test_deck_review_does_not_request_combat_sections(self):
        state = snapshot()
        state["sections"].pop("hand")
        result = plan_inspections(state, decision="deck_review")
        self.assertEqual(result["required_sections"], ["ui", "inventory"])
        self.assertEqual(result["requests"][0]["inventory_categories"], ["card"])

    def test_long_fight_requests_piles_before_other_unresolved_combat_details(self):
        state = snapshot()
        state["sections"].pop("piles")
        state["sections"].pop("hand")
        result = plan_inspections(state, decision={"kind": "play_card", "effects": ["power"], "long_fight": True})
        self.assertEqual([r["view"] for r in result["requests"]], ["pile_pages_and_continuity", "hand_and_focus_sequence"])
        self.assertIn("sorted by rarity", result["requests"][0]["limitation"])

    def test_pile_dependent_effect_adds_requirement(self):
        state = snapshot()
        state["sections"].pop("piles")
        result = plan_inspections(state, decision={"kind": "play_card", "effects": ["draw"]})
        self.assertTrue(result["decision_blocked"])
        self.assertEqual(result["requests"][0]["view"], "pile_pages_and_continuity")

    def test_complete_piles_do_not_imply_draw_order(self):
        result = plan_inspections(snapshot(), decision={"kind": "play_card", "effects": ["topdeck"]})
        self.assertTrue(result["decision_blocked"])
        self.assertEqual([r["view"] for r in result["requests"]], ["ordered_draw_evidence"])
        state = snapshot()
        state["sections"]["piles"]["data"]["draw_order"] = []
        self.assertFalse(plan_inspections(state, decision={"kind": "play_card", "effects": ["draw_order"]})["decision_blocked"])

    def test_unknown_pile_and_order_use_one_combined_request(self):
        state = snapshot()
        state["sections"].pop("piles")
        result = plan_inspections(state, decision={"kind": "play_card", "effects": ["topdeck"]})
        self.assertEqual(len(result["requests"]), 1)
        self.assertIn("draw order", result["requests"][0]["evidence_needed"][-1])

    def test_complete_ui_explicit_null_focus_is_not_an_unknown(self):
        self.assertNotIn("ui", [gap["section"] for gap in plan_inspections(snapshot())["gaps"]])

    def test_complete_overlay_requests_combat_return_before_other_views(self):
        for phase in ("draw_pile", "map", "reward", "unknown"):
            state = snapshot()
            state["sections"]["ui"]["data"]["phase"] = phase
            state["sections"].pop("hand")
            result = plan_inspections(state)
            self.assertTrue(result["decision_blocked"])
            self.assertEqual([r["view"] for r in result["requests"]], ["return_to_combat_and_focus"])
            self.assertTrue(result["requests"][0]["requires_interactive_view"])

    def test_deck_review_does_not_require_combat_phase(self):
        state = snapshot()
        state["sections"]["ui"]["data"]["phase"] = "deck"
        result = plan_inspections(state, decision="deck_review")
        self.assertNotIn("ui", [gap["section"] for gap in result["gaps"]])

    def test_complete_required_pile_zone_suppresses_other_unknown_zones(self):
        state = snapshot()
        state["sections"]["piles"].update(complete=False, status="partial")
        state["sections"]["piles"]["data"] = {"discard": [], "coverage": {"discard": "complete"},
                                              "zone_evidence": {"discard": "confirmed empty discard"}}
        result = plan_inspections(state, decision={"effects": ["discard"]})
        self.assertFalse(result["decision_blocked"])
        self.assertEqual(result["required_pile_zones"], ["discard"])
        result = plan_inspections(state, decision={"effects": ["draw"]})
        self.assertEqual(result["requests"][0]["pile_zones"], ["draw"])

    def test_partial_pile_contents_or_missing_zone_proof_remain_unknown(self):
        for coverage, proof in (("partial", "partial page"), ("complete", "")):
            state = snapshot()
            state["sections"]["piles"].update(complete=False, status="partial")
            state["sections"]["piles"]["data"] = {"discard": [], "coverage": {"discard": coverage},
                                                  "zone_evidence": {"discard": proof}}
            result = plan_inspections(state, decision={"effects": ["discard"]})
            self.assertEqual(result["requests"][0]["pile_zones"], ["discard"])

    def test_order_uses_complete_draw_zone_even_with_other_zones_unknown(self):
        state = snapshot()
        state["sections"]["piles"].update(complete=False, status="partial")
        state["sections"]["piles"]["data"] = {"draw": [], "draw_order": [], "coverage": {"draw": "complete"},
                                              "zone_evidence": {"draw": "confirmed empty draw"}}
        self.assertFalse(plan_inspections(state, decision={"effects": ["draw_order"]})["decision_blocked"])

    def test_missing_frame_still_requires_source_bound_screen(self):
        state = snapshot()
        state["frame"] = None
        result = plan_inspections(state)
        self.assertEqual(result["requests"][0]["view"], "current_screen_and_focus")

    def test_conflict_and_missing_proof_are_not_confirmed(self):
        state = snapshot()
        state["sections"]["statuses"]["status"] = "conflict"
        state["sections"]["hand"]["evidence"] = []
        result = plan_inspections(state)
        self.assertEqual({gap["section"] for gap in result["gaps"]}, {"hand", "statuses"})

    def test_requests_are_bounded_stable_and_bound_to_epoch_and_frame(self):
        state = snapshot()
        for section in ("player", "enemies", "hand", "statuses", "inventory"):
            state["sections"].pop(section)
        a = plan_inspections(state, max_requests=1)
        self.assertEqual(len(a["requests"]), 1)
        self.assertEqual(a["requests"][0]["sections"], ["player", "enemies"])
        self.assertEqual(len(a["deferred_requests"]), 3)
        self.assertEqual(a, plan_inspections(state, max_requests=1))
        state["epoch"] += 1
        b = plan_inspections(state, max_requests=1)
        self.assertNotEqual(a["requests"][0]["request_id"], b["requests"][0]["request_id"])
        for request in a["requests"] + a["deferred_requests"]:
            self.assertIsNone(request["controller_action"])

    def test_unknown_effect_blocks_on_rule_review(self):
        result = plan_inspections(snapshot(), decision={"effects": ["unmodeled_effect"]})
        self.assertTrue(result["decision_blocked"])
        self.assertEqual(result["requests"][0]["view"], "rule_dependency_review")

    def test_invalid_contract_inputs_rejected(self):
        for decision in ("invented_decision", {"kind": []}, {"effects": "draw"}, {"long_fight": 1}, {"guess": True}):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                plan_inspections(snapshot(), decision=decision)
        for limit in (0, 9, True):
            with self.assertRaises(ValueError):
                plan_inspections(snapshot(), max_requests=limit)
        with self.assertRaises(ValueError):
            plan_inspections({})


if __name__ == "__main__":
    unittest.main()
