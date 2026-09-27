"""Synthetic map contracts; these declarations do not calibrate PS5 hardware."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import test_choice_execution as fixtures
from tests.test_menu_requests import make_capture
from veda.choice_execution import ChoiceError, plan_choice_step, verify_choice_step
from veda.menu_controls import (CONTROL_PROFILE, MAP_FOCUS_RULE, MAP_INSPECT_RULE,
                                MAP_SELECT_RULE, bind_reviewed_menu_controls)
from veda.menu_requests import (map_arrival_context, validate_menu_draft,
                                write_menu_request)

NOW = fixtures.NOW


def map_observation(*, act=1, floor=0, kinds=("enemy", "enemy", "enemy")):
    obs = fixtures.observation("map")
    obs["context"].update(combat_id=None, turn_id=None)
    obs["resources"] = {"hp": 80, "max_hp": 80, "gold": 99}
    obs["facts"] = {"act": act, "floor": floor, "current_node_id": f"a{act}-f{floor}-current"}
    options = []
    for index, kind in enumerate(kinds):
        identity = f"a{act}-f{floor + 1}-n{index}"
        options.append({"id": identity, "label": kind.title(), "enabled": True, "costs": {},
            "node": {"node_id": identity, "kind": kind, "act": act, "floor": floor + 1,
                "x": 100 + 150 * index, "y": 500 - index * 4, "reachable": True,
                "reachability_evidence": "Synthetic reviewed connection and selectable marker.",
                "classification_evidence": "Synthetic reviewed icon; unknown explicitly stays unread."}})
    obs["ui"].update(menu_family="map_nodes", control_layout="ps5_default", options=options,
        order=[o["id"] for o in options], focused_id=options[len(options) // 2]["id"], navigation=[],
        map_siblings={"complete": True, "selectable_count": len(options),
            "from_node_id": obs["facts"]["current_node_id"],
            "evidence_note": "Synthetic fixture: every selectable sibling is listed left to right."})
    return obs


def bound(obs=None, *, seconds=0):
    return bind_reviewed_menu_controls(obs or map_observation(), control_profile=CONTROL_PROFILE,
        now=NOW + timedelta(seconds=seconds))


def goal(obs, target=None, screen=None):
    target = target or obs["ui"]["focused_id"]
    node = next(o["node"] for o in obs["ui"]["options"] if o["id"] == target)
    if screen is None:
        screen = {"enemy": "combat", "elite": "combat", "boss": "combat", "rest": "rest",
                  "merchant": "shop", "event": "event", "treasure": "reward", "unknown": "event"}[node["kind"]]
    choice = fixtures.choice(obs, [target], kind="map")
    choice["postconditions"].update(screen=screen, phase="result",
        context=map_arrival_context(obs["context"], target, screen),
        facts={"act": node["act"], "floor": node["floor"], "current_node_id": target, "node_type": node["kind"]})
    return choice


def clear_controls(obs):
    for option in obs["ui"]["options"]:
        option.pop("activate", None)
    obs["ui"]["navigation"] = []
    return obs


def draft(*, act=1, floor=0, kinds=("enemy", "enemy", "enemy")):
    obs = map_observation(act=act, floor=floor, kinds=kinds)
    choice = goal(obs)
    post = choice["postconditions"]
    post["context"] = "next_room"
    post["inventory"] = post.pop("inventory_digest")
    return {"schema": "veda.menu-draft.v1", "context": obs["context"],
        "inventory": {"current": {"card": ["Strike", "Defend", "Bash+"],
                                      "relic": ["Burning Blood"], "potion": []},
                      "coverage": {"card": "complete", "relic": "complete", "potion": "complete"},
                      "properties": {}},
        "resources": obs["resources"], "facts": obs["facts"], "ui": obs["ui"],
        "choice": {"kind": "map", "option_ids": choice["option_ids"], "postconditions": post},
        "reasoning": "Synthetic reviewed route choice; no real game strategy or input."}


def inspection(direction="up"):
    obs = map_observation()
    identity = "inspect-" + direction
    obs["ui"].pop("map_siblings")
    obs["ui"].update(menu_family="map_inspect", options=[{"id": identity,
        "label": "Inspect upper map" if direction == "up" else "Inspect lower map",
        "role": "map_inspection", "enabled": True, "costs": {}}],
        order=[identity], focused_id=identity, navigation=[],
        map_inspection={"direction": direction, "purpose": "survey",
                        "evidence_note": "Synthetic reviewed map; upper or lower detail needs inspection."})
    return obs


def inspection_goal(obs):
    result = fixtures.choice(obs, obs["ui"]["order"], kind="map")
    result["postconditions"].update(screen="map", phase="result", facts={}, allow_changed_facts=[])
    return result


class MapControlTests(unittest.TestCase):
    def test_three_starting_nodes_center_focus_cross_uses_declared_profile(self):
        raw = map_observation(); original = deepcopy(raw); obs = bound(raw)
        step = plan_choice_step(obs, goal(obs), now=NOW)
        self.assertEqual(["cross"], step["command"]["buttons"])
        self.assertEqual("commit", step["step_kind"])
        self.assertEqual(original, raw)
        self.assertEqual(3, len(obs["ui"]["options"]))
        self.assertEqual(4, len(obs["ui"]["navigation"]))
        for option in obs["ui"]["options"]:
            proof = option["activate"]["evidence"]
            self.assertEqual(MAP_SELECT_RULE, proof["rule_id"])
            self.assertEqual("documented_control_profile", proof["kind"])
            self.assertFalse(set(proof) & {"hint_text", "reference_id", "before_sha256", "after_sha256"})
        self.assertFalse(step["runtime_authorized"])
        self.assertFalse(step["controller_authorized"])

    def test_each_direction_is_one_adjacent_sibling_followed_by_fresh_replan(self):
        for index, button in ((0, "left"), (2, "right")):
            before = bound(); target = before["ui"]["order"][index]
            step = plan_choice_step(before, goal(before, target), now=NOW)
            self.assertEqual([button], step["command"]["buttons"])
            self.assertEqual({"focused_id": target}, step["expectation"])
            self.assertEqual("focus", step["step_kind"])
            after = clear_controls(fixtures.later(before)); after["ui"]["focused_id"] = target
            after = bound(after, seconds=1)
            result = verify_choice_step(step, before, after, now=NOW + timedelta(seconds=1))
            self.assertFalse(result["choice_complete"])
            next_step = plan_choice_step(after, goal(after, target), now=NOW + timedelta(seconds=1))
            self.assertEqual(["cross"], next_step["command"]["buttons"])
        raw = map_observation(); raw["ui"]["focused_id"] = raw["ui"]["order"][0]
        obs = bound(raw); step = plan_choice_step(obs, goal(obs, obs["ui"]["order"][2]), now=NOW)
        self.assertEqual({"focused_id": obs["ui"]["order"][1]}, step["expectation"])

    def test_multiple_floors_and_room_kinds_have_bounded_room_arrivals(self):
        for act, floor in ((1, 6), (2, 24), (3, 48), (4, 53)):
            for kind in ("enemy", "elite", "event", "rest", "merchant", "treasure", "boss"):
                obs = bound(map_observation(act=act, floor=floor, kinds=(kind,)))
                step = plan_choice_step(obs, goal(obs), now=NOW)
                self.assertEqual("commit", step["step_kind"])
                self.assertEqual(floor + 1, step["choice"]["postconditions"]["facts"]["floor"])
        obs = bound(map_observation(kinds=("event",)))
        choice = goal(obs)
        event = deepcopy(choice["postconditions"])
        combat = goal(obs, screen="combat")["postconditions"]
        choice["postconditions"] = {"alternatives": [
            {"id": "event-room", "postconditions": event}, {"id": "combat-room", "postconditions": combat}]}
        self.assertEqual("commit", plan_choice_step(obs, choice, now=NOW)["step_kind"])

    def test_unknown_neighbor_does_not_invalidate_known_target(self):
        obs = bound(map_observation(kinds=("unknown", "rest", "elite")))
        self.assertEqual("commit", plan_choice_step(obs, goal(obs), now=NOW)["step_kind"])
        with self.assertRaisesRegex(ChoiceError, "chosen map node kind"):
            plan_choice_step(obs, goal(obs, obs["ui"]["order"][0]), now=NOW)

    def test_missing_siblings_or_completion_evidence_never_gain_controls(self):
        patches = [lambda o: o["ui"].pop("map_siblings"),
            lambda o: o["ui"]["map_siblings"].update(complete=False),
            lambda o: o["ui"]["map_siblings"].update(complete=1),
            lambda o: o["ui"]["map_siblings"].update(selectable_count=2),
            lambda o: o["ui"]["map_siblings"].update(selectable_count=True),
            lambda o: o["ui"]["map_siblings"].update(evidence_note=""),
            lambda o: o["ui"]["map_siblings"].update(from_node_id="another"),
            lambda o: o["ui"].update(focused_id=None),
            lambda o: o["ui"].update(focused_id="unlisted"),
            lambda o: o["facts"].pop("current_node_id")]
        for patch in patches:
            obs = map_observation(); patch(obs)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError): bound(obs)

    def test_wrong_kind_coordinate_floor_or_reachability_fails_before_input(self):
        patches = [{"node_id": "wrong"}, {"kind": "shop"}, {"reachable": False}, {"reachable": 1},
            {"reachability_evidence": ""}, {"classification_evidence": ""},
            {"act": 2}, {"act": True}, {"floor": 2}, {"floor": True},
            {"x": -1}, {"x": True}, {"x": 100.5}, {"y": 32769}]
        for patch in patches:
            obs = map_observation(); obs["ui"]["options"][0]["node"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError): bound(obs)
        obs = map_observation(); obs["ui"]["options"][1]["node"]["x"] = 100
        with self.assertRaisesRegex(ChoiceError, "left-to-right"): bound(obs)
        obs = map_observation(); obs["ui"]["options"].reverse(); obs["ui"]["order"].reverse()
        with self.assertRaisesRegex(ChoiceError, "left-to-right"): bound(obs)

    def test_no_card_grid_shortcut_custom_mapping_cost_or_disabled_sibling(self):
        for key, value in (("grid", {"complete": True, "cells": []}), ("phase", "confirm"),
                           ("selection_mode", "toggle"), ("control_layout", "custom"),
                           ("screen", "event"), ("selected_ids", ["a1-f1-n1"])):
            obs = map_observation(); obs["ui"][key] = value
            with self.subTest(key=key), self.assertRaises(ChoiceError): bound(obs)
        for key, value in (("enabled", False), ("costs", {"gold": 1}),
                           ("shortcut", fixtures.binding("triangle", "activate:a1-f1-n0")),
                           ("activate", fixtures.binding("cross", "activate:a1-f1-n0"))):
            obs = map_observation(); obs["ui"]["options"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ChoiceError): bound(obs)

    def test_left_right_cannot_wrap_skip_or_be_relabelled_vertical(self):
        for button, source, target in (("left", 0, 2), ("right", 0, 2), ("up", 1, 0), ("down", 1, 2)):
            obs = bound(); order = obs["ui"]["order"]
            edge = deepcopy(obs["ui"]["navigation"][0])
            edge.update(button=button, **{"from": order[source], "to": order[target]})
            edge["evidence"]["meaning"] = f"focus:{order[source]}->{order[target]}"
            obs["ui"]["navigation"] = [edge]
            with self.subTest(button=button, source=source), self.assertRaises(ChoiceError):
                plan_choice_step(obs, goal(obs), now=NOW)

    def test_stale_or_forged_control_provenance_remains_rejected(self):
        with self.assertRaisesRegex(ChoiceError, "stale"):
            bound(map_observation(), seconds=31)
        for patch in ({"frame_id": "wrong"}, {"image_sha256": "f" * 64}, {"hint_text": "invented Cross"},
                      {"reference_id": "not observed"}, {"rule_id": MAP_FOCUS_RULE}, {"control_profile": "custom"}):
            obs = bound(); obs["ui"]["options"][1]["activate"]["evidence"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError):
                plan_choice_step(obs, goal(obs), now=NOW)

    def test_arrival_does_not_imply_an_encounter_hand_or_combat_plan(self):
        before = bound(); step = plan_choice_step(before, goal(before), now=NOW)
        after = fixtures.outcome(before, step)
        after["ui"].pop("menu_family"); after["ui"].pop("map_siblings")
        result = verify_choice_step(step, before, after, now=NOW + timedelta(seconds=1))
        self.assertTrue(result["choice_complete"])
        self.assertFalse(set(after["facts"]) & {"enemy", "encounter", "hand", "draw_pile"})
        with self.assertRaises(ChoiceError):
            plan_choice_step(after, fixtures.choice(after, kind="map"), now=NOW + timedelta(seconds=1))

    def test_wrong_room_floor_or_inventory_cannot_be_planned_or_verified(self):
        before = bound()
        for patch in ({"screen": "rest"}, {"phase": "choose"}, {"inventory_digest": "d" * 64},
                      {"context": before["context"]},
                      {"resources": {"hp": 80, "max_hp": {"min": 80, "max": 90}, "gold": 99}}):
            choice = goal(before); choice["postconditions"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError):
                plan_choice_step(before, choice, now=NOW)
        for key, value in (("act", 2), ("floor", 2), ("node_type", "elite"), ("current_node_id", "wrong")):
            choice = goal(before); choice["postconditions"]["facts"][key] = value
            with self.subTest(key=key), self.assertRaises(ChoiceError):
                plan_choice_step(before, choice, now=NOW)
        step = plan_choice_step(before, goal(before), now=NOW)
        patches = [lambda a: a["resources"].update(hp=79), lambda a: a["facts"].update(floor=2),
                   lambda a: a["facts"].update(current_node_id="wrong"),
                   lambda a: a.update(inventory_digest="e" * 64), lambda a: a["ui"].update(screen="rest")]
        for patch in patches:
            after = fixtures.outcome(before, step); patch(after)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError):
                verify_choice_step(step, before, after, now=NOW + timedelta(seconds=1))

    def test_reviewed_resource_bounds_are_explicit_not_guessed(self):
        obs = bound(); choice = goal(obs)
        choice["postconditions"]["resources"]["gold"] = {"min": 99, "max": 111}
        step = plan_choice_step(obs, choice, now=NOW)
        after = fixtures.outcome(obs, step); after["resources"]["gold"] = 111
        self.assertTrue(verify_choice_step(step, obs, after, now=NOW + timedelta(seconds=1))["choice_complete"])
        after["resources"]["gold"] = 112
        with self.assertRaisesRegex(ChoiceError, "resource outcome"):
            verify_choice_step(step, obs, after, now=NOW + timedelta(seconds=1))

    def test_compact_request_context_and_source_are_derived_without_fabricating_game_facts(self):
        value = draft(); original = deepcopy(value)
        check = validate_menu_draft(value, CONTROL_PROFILE)
        self.assertTrue(check["draft_valid"]); self.assertFalse(check["dispatchable"])
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve(); image = make_capture(root, NOW - timedelta(seconds=2)); output = root / "request.json"
            write_menu_request(value, capture=image, reviewer="Synthetic test reviewer", evidence_note="Synthetic exact-image review.",
                reviewed=True, control_profile=CONTROL_PROFILE, output=output, now=NOW)
            request = json.loads(output.read_text())
        self.assertEqual(original, value)
        expected = map_arrival_context(value["context"], value["choice"]["option_ids"][0], "combat")
        self.assertEqual(expected, request["choice"]["postconditions"]["context"])
        self.assertEqual(value["context"], request["context"])
        self.assertEqual(["Burning Blood"], request["inventory"]["current"]["relic"])
        self.assertEqual("map", request["observation"]["ui"]["screen"])
        self.assertFalse(set(request["choice"]["postconditions"]["facts"]) & {"encounter", "hand"})

    def test_provisional_context_same_floor_across_outcomes_and_changes_with_actual_scope(self):
        obs = map_observation(); context = obs["context"]
        combat = map_arrival_context(context, "node-1", "combat")
        event = map_arrival_context(context, "node-1", "event")
        self.assertEqual(combat["floor_id"], event["floor_id"])
        self.assertIsNone(event["combat_id"]); self.assertIsNone(event["turn_id"])
        for other_context, node in ((dict(context, run_id="other"), "node-1"),
                                    (dict(context, floor_id="other"), "node-1"), (context, "node-2")):
            self.assertNotEqual(combat["floor_id"], map_arrival_context(other_context, node, "combat")["floor_id"])

    def test_compact_next_room_expands_each_complete_alternative_only_for_map_nodes(self):
        value = draft(kinds=("event",)); event = deepcopy(value["choice"]["postconditions"])
        combat = deepcopy(event); combat["screen"] = "combat"
        value["choice"]["postconditions"] = {"alternatives": [
            {"id": "observed-event", "postconditions": event},
            {"id": "observed-combat", "postconditions": combat}]}
        self.assertTrue(validate_menu_draft(value, CONTROL_PROFILE)["draft_valid"])
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve(); image = make_capture(root, NOW - timedelta(seconds=2)); output = root / "request.json"
            write_menu_request(value, capture=image, reviewer="Synthetic test reviewer", evidence_note="Synthetic exact-image review.",
                reviewed=True, control_profile=CONTROL_PROFILE, output=output, now=NOW)
            request = json.loads(output.read_text())
        branches = request["choice"]["postconditions"]["alternatives"]
        first, second = [branch["postconditions"]["context"] for branch in branches]
        self.assertEqual(first["floor_id"], second["floor_id"])
        self.assertIsNone(first["combat_id"]); self.assertIsNotNone(second["combat_id"])
        from tests.test_menu_requests import draft as event_draft
        other = event_draft(); other["choice"]["postconditions"]["context"] = "next_room"
        with self.assertRaisesRegex(ChoiceError, "next_room context"):
            validate_menu_draft(other, CONTROL_PROFILE)

    def test_directional_inspection_is_named_profile_and_never_cross_selection(self):
        for direction in ("up", "down"):
            obs = bound(inspection(direction)); choice = inspection_goal(obs)
            step = plan_choice_step(obs, choice, now=NOW)
            self.assertEqual([direction], step["command"]["buttons"])
            self.assertEqual("inspect", step["step_kind"])
            proof = obs["ui"]["options"][0]["activate"]["evidence"]
            self.assertEqual(MAP_INSPECT_RULE, proof["rule_id"])
            self.assertEqual("documented_control_profile", proof["kind"])
            self.assertFalse(set(proof) & {"hint_text", "reference_id"})

    def test_inspection_cannot_change_resources_inventory_context_or_facts(self):
        obs = bound(inspection())
        for patch in ({"screen": "combat"}, {"phase": "choose"}, {"inventory_digest": "d" * 64},
                      {"context": dict(obs["context"], floor_id="next")},
                      {"resources": dict(obs["resources"], gold=100)},
                      {"facts": {"floor": 1}}, {"allow_changed_facts": ["floor"]}):
            choice = inspection_goal(obs); choice["postconditions"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError):
                plan_choice_step(obs, choice, now=NOW)

    def test_inspection_rejects_other_directions_node_masks_and_unreviewed_purpose(self):
        for patch in ({"direction": "left"}, {"direction": "cross"}, {"purpose": "select"}, {"evidence_note": ""}):
            obs = inspection(); obs["ui"]["map_inspection"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError): bound(obs)
        for patch in ({"id": "real-node"}, {"role": "node"}, {"label": "Select node"}, {"node": {}}):
            obs = inspection(); obs["ui"]["options"][0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError): bound(obs)


if __name__ == "__main__":
    unittest.main()
