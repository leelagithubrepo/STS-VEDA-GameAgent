from copy import deepcopy
import unittest

from veda.map_brief import MAX_EDGES, MAX_NODES, summarize_routes
from veda.map_reader import MapAssessment, MapNode, assess_encounter_transition, assess_map
from veda.preflight import preflight_map


def node(node_id, kind="enemy", confidence=1.0, **fields):
    return {"node_id": node_id, "kind": kind, "confidence": confidence, **fields}


def edge(source, target, **fields):
    return {"from_node_id": source, "to_node_id": target, **fields}


def summary(nodes, edges, **kwargs):
    return summarize_routes(current_node_id="current", visible_nodes=[node("current"), *nodes],
                            visible_edges=edges, **kwargs)


def option(result, node_id):
    return next(value for value in result["options"] if value["node_id"] == node_id)


class MapAssessmentTests(unittest.TestCase):
    def test_unverified_sibling_does_not_block_confirmed_choice(self):
        rest, uncertain = MapNode("rest", "rest", True, .99), MapNode("maybe", "elite", True, .7)
        checked = assess_map(boss=None, boss_confidence=0, nodes=(uncertain, rest))
        self.assertTrue(checked.ready)
        self.assertEqual(checked.reachable, (rest,))
        self.assertEqual(checked.unverified_reachable, (uncertain,))
        self.assertTrue(checked.warnings)
        self.assertFalse(checked.reasons)
        preflight = preflight_map(boss=None, boss_confidence=0, nodes=(uncertain, rest))
        self.assertTrue(preflight.allowed)
        self.assertIn("rest:rest", preflight.prediction)
        self.assertNotIn("maybe", preflight.prediction)

    def test_no_confirmed_choice_blocks_and_threshold_matches_route_ledger(self):
        for value in (.89, .899999):
            with self.subTest(confidence=value):
                checked = assess_map(boss="readable boss", boss_confidence=.85,
                                     nodes=(MapNode("a", "enemy", True, value),))
                self.assertFalse(checked.ready)
                self.assertFalse(checked.reachable)
                self.assertTrue(checked.boss_confirmed)
        self.assertTrue(assess_map(boss=None, boss_confidence=0,
                                  nodes=(MapNode("a", "elite", True, .9),)).ready)

    def test_nonreachable_node_is_not_offered_and_constructor_is_compatible(self):
        checked = assess_map(boss=None, boss_confidence=0, nodes=(MapNode("a", "rest", False, 1),))
        self.assertFalse(checked.ready)
        self.assertEqual(checked.unverified_reachable, ())
        old_style = MapAssessment(True, (), (), False)
        self.assertEqual(old_style.warnings, ())
        self.assertEqual(old_style.unverified_reachable, ())

    def test_encounter_transition_uses_the_same_map_confirmation_threshold(self):
        for confidence, expected in ((.89, False), (.9, True)):
            checked = assess_encounter_transition(selected_node=MapNode("elite", "elite", True, confidence),
                                                  observed_kind="elite", observed_confidence=.99)
            self.assertEqual(checked.ready, expected)


class RouteBriefTests(unittest.TestCase):
    def test_low_hp_rest_distances_are_facts_not_a_chosen_route(self):
        resources = {"hp": 9, "max_hp": 80, "gold": 80, "deck_strength": "not reviewed"}
        result = summary([node("fight"), node("rest", "rest"), node("later", "rest")],
                         [edge("current", "fight"), edge("current", "rest"), edge("fight", "later")],
                         resources=resources, graph_complete=True)
        self.assertEqual(option(result, "rest")["rest"], {"reachable": True, "minimum_confirmed_distance": 0})
        self.assertEqual(option(result, "fight")["reachable_rest_min_distance"], {"later": 1})
        self.assertEqual(result["resources"], resources)
        resources["hp"] = 80
        self.assertEqual(result["resources"]["hp"], 9)
        self.assertEqual(result["resources_role"], "reviewer_context_only")
        self.assertNotIn("chosen_action", result)
        self.assertNotIn("score", result)
        self.assertFalse(result["controller_authorized"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["freshness_established"])

    def test_shop_alias_requires_evidence_and_gold_is_only_context(self):
        proof = {"source": "reviewed legend", "symbol": "merchant"}
        result = summary([node("shop", "shop", classification_evidence=proof)],
                         [edge("current", "shop")], resources={"gold": 7}, graph_complete=True)
        shop = option(result, "shop")
        self.assertEqual(shop["kind"], "merchant")
        self.assertEqual(shop["reachable_merchant_min_distance"], {"shop": 0})
        self.assertEqual(shop["classification_evidence"], proof)
        self.assertEqual(result["resources"], {"gold": 7})
        self.assertNotIn("affordable", shop)
        proof["source"] = "changed"
        self.assertEqual(shop["classification_evidence"]["source"], "reviewed legend")

    def test_resource_profiles_do_not_change_topology_or_order(self):
        nodes = [node("a", "rest"), node("b", "elite", classification_evidence="legend inspected")]
        edges = [edge("current", "a"), edge("current", "b")]
        low = summary(nodes, edges, resources={"hp": 1, "gold": 0}, graph_complete=True)
        high = summary(nodes, edges, resources={"hp": 80, "gold": 400}, graph_complete=True)
        self.assertEqual(low["options"], high["options"])

    def test_node_order_and_coordinates_have_no_effect(self):
        nodes = [node("c", "rest", x=10, y=10), node("b"), node("a")]
        edges = [edge("current", "b"), edge("current", "a"), edge("a", "c"), edge("b", "c")]
        expected = summary(nodes, edges, graph_complete=True)
        nodes.reverse()
        nodes[0]["x"] = 900
        self.assertEqual(summary(nodes, list(reversed(edges)), graph_complete=True), expected)
        self.assertEqual([row["node_id"] for row in expected["options"]], ["a", "b"])

    def test_arbitrary_directed_ids_merging_and_shortest_paths(self):
        result = summary([node("z-route"), node("left-17"), node("z-last", "rest")],
                         [edge("current", "z-route"), edge("z-route", "left-17"),
                          edge("z-route", "z-last"), edge("left-17", "z-last")], graph_complete=True)
        row = option(result, "z-route")
        self.assertEqual(row["reachable_rest_min_distance"], {"z-last": 1})
        self.assertEqual(row["branching_exits"], [{"node_id": "z-route", "confirmed_exit_ids": ["left-17", "z-last"],
                                                 "unverified_exit_ids": [], "outgoing_complete": True}])

    def test_forced_elite_before_rest_with_complete_exits(self):
        result = summary([node("a"), node("elite", "elite", classification_evidence="reviewed"), node("rest", "rest")],
                         [edge("current", "a"), edge("a", "elite"), edge("elite", "rest")], graph_complete=True)
        row = option(result, "a")
        self.assertTrue(row["elite_before_rest_unavoidable"])
        self.assertEqual(row["known_elite_node_ids"], ["elite"])
        self.assertFalse(row["has_confirmed_elite_free_rest_path"])
        self.assertEqual(row["reachable_rest_min_distance"], {"rest": 2})

    def test_first_elite_is_unavoidable_even_with_unseen_future(self):
        result = summary([node("elite", "elite", classification_evidence="reviewed")], [edge("current", "elite")])
        row = option(result, "elite")
        self.assertTrue(row["elite_before_rest_unavoidable"])
        self.assertFalse(row["has_confirmed_elite_free_rest_path"])
        self.assertIsNone(row["rest"]["reachable"])

    def test_known_rest_bypass_disproves_forced_elite_despite_missing_exits(self):
        result = summary([node("a"), node("elite", "elite", classification_evidence="reviewed"), node("rest", "rest")],
                         [edge("current", "a"), edge("a", "elite"), edge("a", "rest")])
        row = option(result, "a")
        self.assertFalse(row["elite_before_rest_unavoidable"])
        self.assertTrue(row["has_confirmed_elite_free_rest_path"])

    def test_incomplete_exits_cannot_establish_forced_elite_or_absent_shop(self):
        result = summary([node("a"), node("elite", "elite", classification_evidence="reviewed")],
                         [edge("current", "a"), edge("a", "elite")])
        row = option(result, "a")
        self.assertIsNone(row["elite_before_rest_unavoidable"])
        self.assertIsNone(row["merchant"]["reachable"])
        self.assertEqual(row["frontier_node_ids"], ["a", "elite"])
        self.assertFalse(result["current_exits_complete"])

    def test_complete_leaf_establishes_no_future_rest_or_merchant_not_zero_distance(self):
        row = option(summary([node("end", "boss")], [edge("current", "end")], graph_complete=True), "end")
        self.assertEqual(row["rest"], {"reachable": False, "minimum_confirmed_distance": None})
        self.assertEqual(row["merchant"], {"reachable": False, "minimum_confirmed_distance": None})
        self.assertFalse(row["elite_before_rest_unavoidable"])

    def test_per_node_outgoing_completeness_overrides_graph_default(self):
        result = summary([node("end", outgoing_complete=False)], [edge("current", "end")], graph_complete=True)
        self.assertIsNone(option(result, "end")["rest"]["reachable"])
        result = summary([node("end", outgoing_complete=True)], [edge("current", "end")])
        self.assertFalse(option(result, "end")["rest"]["reachable"])

    def test_unknown_intermediate_separates_topological_and_confirmed_paths(self):
        result = summary([node("a"), node("unknown", "unknown"), node("rest", "rest")],
                         [edge("current", "a"), edge("a", "unknown"), edge("unknown", "rest")], graph_complete=True)
        row = option(result, "a")
        self.assertEqual(row["reachable_rest_min_distance"], {})
        self.assertIsNone(row["rest"]["reachable"])
        self.assertTrue(row["has_rest_path_avoiding_known_elites"])
        self.assertIsNone(row["has_confirmed_elite_free_rest_path"])
        self.assertIsNone(row["elite_before_rest_unavoidable"])
        self.assertEqual(row["uncertain_node_ids"], ["unknown"])

    def test_event_path_is_explicitly_not_a_safety_promise(self):
        result = summary([node("a"), node("question", "event"), node("rest", "rest")],
                         [edge("current", "a"), edge("a", "question"), edge("question", "rest")], graph_complete=True)
        row = option(result, "a")
        self.assertEqual(row["reachable_rest_min_distance"], {"rest": 2})
        self.assertTrue(row["has_confirmed_elite_free_rest_path"])
        self.assertEqual(row["event_node_ids"], ["question"])
        self.assertTrue(any("not a safety prediction" in warning for warning in result["warnings"]))

    def test_uncertain_first_nodes_are_not_legal_options(self):
        result = summary([node("good", "rest"), node("unknown", None), node("low", "enemy", .89)],
                         [edge("current", key) for key in ("low", "good", "unknown")], graph_complete=True)
        self.assertFalse(result["blocked"])
        self.assertEqual([row["node_id"] for row in result["options"]], ["good"])
        self.assertEqual([row["node_id"] for row in result["unverified_reachable"]], ["low", "unknown"])

    def test_missing_elite_or_merchant_basis_is_retained_as_uncertainty(self):
        for kind in ("elite", "merchant", "shop"):
            for proof in (None, "", "   ", [], {}):
                with self.subTest(kind=kind, proof=proof):
                    result = summary([node("x", kind, classification_evidence=proof)], [edge("current", "x")])
                    self.assertTrue(result["blocked"])
                    self.assertIn("missing_classification_evidence", result["unverified_reachable"][0]["reasons"])

    def test_uncertain_edge_never_supplies_a_confirmed_option_or_rest_path(self):
        result = summary([node("a"), node("rest", "rest")],
                         [edge("current", "a"), edge("current", "rest", confidence=.89),
                          edge("a", "rest", confidence=.89)], graph_complete=True)
        self.assertEqual([row["node_id"] for row in result["options"]], ["a"])
        row = option(result, "a")
        self.assertIsNone(row["rest"]["reachable"])
        self.assertEqual(row["uncertain_edges"], [edge("a", "rest", confidence=.89)])
        self.assertIsNone(row["has_rest_path_avoiding_known_elites"])

    def test_threshold_inclusive_and_no_input_mutation(self):
        nodes = [node("a", "rest", .90)]
        edges = [edge("current", "a", confidence=.90)]
        resources = {"deck_strength": {"assessment": "unknown", "provenance": ["reviewer"]}}
        before = deepcopy((nodes, edges, resources))
        self.assertFalse(summary(nodes, edges, resources=resources)["blocked"])
        self.assertEqual((nodes, edges, resources), before)

    def test_disconnected_nodes_do_not_create_route_facts(self):
        result = summary([node("a"), node("distant", "rest")], [edge("current", "a")], graph_complete=True)
        self.assertEqual(option(result, "a")["reachable_rest_min_distance"], {})

    def test_invalid_graph_shapes_are_rejected(self):
        cases = [
            ([node("a"), node("a")], [edge("current", "a")]),
            ([node("a")], [edge("current", "absent")]),
            ([node("a")], [edge("current", "a"), edge("current", "a")]),
            ([node("a")], [edge("a", "a")]),
            ([node("a"), node("b")], [edge("current", "a"), edge("a", "b"), edge("b", "a")]),
            ([node("a"), node("b")], [edge("a", "b", confidence=.1), edge("b", "a", confidence=.1)]),
        ]
        for nodes, edges in cases:
            with self.subTest(nodes=nodes, edges=edges), self.assertRaises(ValueError):
                summary(nodes, edges)
        with self.assertRaises(ValueError):
            summarize_routes(current_node_id="missing", visible_nodes=[node("a")], visible_edges=[])

    def test_tight_types_and_sizes_reject_ambiguous_values(self):
        for bad in (True, None, "1", -1, 2, float("nan"), float("inf"), 10**1000):
            with self.subTest(confidence=bad), self.assertRaises(ValueError):
                summary([node("a", confidence=bad)], [edge("current", "a")])
            with self.subTest(edge_confidence=bad), self.assertRaises(ValueError):
                summary([node("a")], [edge("current", "a", confidence=bad)])
        for bad in ("?", "combat", "REST", ["rest"], True):
            with self.subTest(kind=bad), self.assertRaises(ValueError):
                summary([node("a", bad)], [])
        for bad in (None, 0, "true"):
            with self.subTest(completeness=bad), self.assertRaises(ValueError):
                summary([node("a", outgoing_complete=bad)], [])
        with self.assertRaises(ValueError):
            summary([], [], graph_complete=1)
        with self.assertRaises(ValueError):
            summary([node(str(i)) for i in range(MAX_NODES)], [])
        with self.assertRaises(ValueError):
            summary([node("a")], [edge("current", "a")] * (MAX_EDGES + 1))
        with self.assertRaises(ValueError):
            summary([node(" a")], [])
        with self.assertRaises(ValueError):
            summary([node("a\n")], [])

    def test_resources_are_bounded_and_unknowns_not_defaulted(self):
        result = summary([node("a")], [edge("current", "a")], resources={"hp": None, "gold": None})
        self.assertEqual(result["resources"], {"hp": None, "gold": None})
        for resources in ({"hp": True}, {"gold": -1}, {"hp": 81, "max_hp": 80},
                          {"deck_strength": float("nan")}, {"notes": "x" * 1025},
                          {"notes": [0] * 257}, {"notes": 10**1000}, {"notes": set()}):
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                summary([node("a")], [], resources=resources)


if __name__ == "__main__":
    unittest.main()
