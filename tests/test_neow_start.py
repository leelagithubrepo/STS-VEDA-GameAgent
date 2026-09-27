"""Synthetic reviewed frames only; these tests do not validate console input."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from veda.choice_execution import ChoiceError, plan_choice_step, validate_choice_proposal, verify_choice_step
from veda.neow_start import (CONTROL_PROFILE, CONTROL_RULE, RULE_KIND, build_neow_talk,
                             build_neow_talk_from_review, build_neow_talk_result_from_review)

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def review_input():
    return {
        "frame": {"frame_id": "synthetic-neow-1", "image_sha256": "a" * 64,
                  "observed_at": NOW.isoformat()},
        "context": {"run_id": "synthetic-new-run", "floor_id": "synthetic-floor-0",
                    "combat_id": None, "turn_id": None},
        "reviewer": "synthetic fixture; not hardware evidence", "review_complete": True,
        "resources": {"hp": 80, "max_hp": 80, "gold": 99}, "inventory_digest": "c" * 64,
        "facts": {"event_id": "neow", "event_phase": "opening_dialogue", "act": 1, "floor": 0,
                  "character": "Ironclad", "ascension": 2, "dialogue_text": "Another try...?"},
        "visible_options": [{"id": "talk", "label": "[Talk]", "enabled": True, "costs": {}}],
        "focused_id": "talk", "control_profile": CONTROL_PROFILE, "now": NOW,
    }


def reviewed_result(packet, *, phase="reward_options"):
    before, proposal = packet["observation"], packet["proposal"]
    after = deepcopy(before)
    after["frame"] = {"frame_id": "synthetic-neow-2", "image_sha256": "b" * 64,
                      "observed_at": (NOW + timedelta(seconds=1)).isoformat()}
    after["review"].update(frame_id=after["frame"]["frame_id"], image_sha256=after["frame"]["image_sha256"])
    after["review"]["outcome"] = {
        "action_id": proposal["action_id"], "before_frame_id": before["frame"]["frame_id"],
        "before_sha256": before["frame"]["image_sha256"], "choice_id": proposal["choice"]["choice_id"],
        "option_ids": ["talk"], "observed_result": "Synthetic reviewer observed dialogue/options advance.",
    }
    after["facts"].update(event_phase=phase, dialogue_text="Synthetic newly reviewed dialogue.")
    after["ui"].update(choice_id="neow-next-choice", layout_id="synthetic-result-layout")
    if phase == "reward_options":
        after["ui"].update(options=[{"id": "newly-reviewed-option", "label": "Synthetic offer",
            "enabled": True, "costs": {}}], order=["newly-reviewed-option"], focused_id="newly-reviewed-option")
    else:
        after["ui"]["options"][0].pop("activate")
    return after


def result_input(packet, *, phase="reward_options"):
    after = reviewed_result(packet, phase=phase)
    return {"frame": after["frame"], "reviewer": after["review"]["reviewer"],
            "resources": after["resources"], "inventory_digest": after["inventory_digest"],
            "facts": after["facts"],
            "visible_options": [{key: option[key] for key in ("id", "label", "enabled", "costs")}
                                for option in after["ui"]["options"]],
            "focused_id": after["ui"]["focused_id"], "action_id": packet["proposal"]["action_id"],
            "observed_result": after["review"]["outcome"]["observed_result"],
            "review_complete": True, "now": NOW + timedelta(seconds=2)}


class NeowStartTests(unittest.TestCase):
    def packet(self):
        return build_neow_talk_from_review(**review_input())

    def verify(self, packet, after):
        return verify_choice_step(packet["proposal"], packet["observation"], after,
                                  now=NOW + timedelta(seconds=2))

    def test_builds_one_cross_with_named_profile_not_invented_screen_evidence(self):
        inputs = review_input(); original = deepcopy(inputs)
        packet = build_neow_talk_from_review(**inputs)
        self.assertEqual(inputs, original)
        proposal, observation = packet["proposal"], packet["observation"]
        self.assertEqual(proposal["command"]["buttons"], ["cross"])
        self.assertEqual(proposal["step_kind"], "commit")
        self.assertFalse(proposal["runtime_authorized"])
        self.assertFalse(proposal["controller_authorized"])
        proof = observation["ui"]["options"][0]["activate"]["evidence"]
        self.assertEqual(proof["kind"], RULE_KIND)
        self.assertEqual(proof["rule_id"], CONTROL_RULE)
        self.assertNotIn("hint_text", proof)
        self.assertNotIn("before_sha256", proof)
        self.assertNotIn("after_sha256", proof)
        self.assertEqual(packet["choice"]["postconditions"]["inventory_digest"], "unchanged")
        self.assertNotIn("rewards", packet["choice"]["postconditions"]["facts"])

    def test_generic_talk_wrong_phase_or_different_floor_cannot_use_neow_rule(self):
        for key, value in (("event_id", "mysterious-sphere"), ("event_phase", "reward_options"),
                           ("event_phase", "unknown"), ("act", 2), ("floor", 1),
                           ("floor", False), ("character", "unknown"), ("ascension", 21),
                           ("ascension", True), ("dialogue_text", "")):
            inputs = review_input(); inputs["facts"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ChoiceError):
                build_neow_talk_from_review(**inputs)

    def test_reward_cost_disabled_unfocused_or_incomplete_options_are_rejected(self):
        for mutate in (
            lambda i: i["visible_options"][0].update(label="Lose HP for a reward"),
            lambda i: i["visible_options"][0].update(costs={"hp": 1}),
            lambda i: i["visible_options"][0].update(costs={"gold": 0}),
            lambda i: i["visible_options"][0].update(costs=None),
            lambda i: i["visible_options"][0].update(enabled=False),
            lambda i: i["visible_options"].append({"id": "reward", "label": "Reward", "enabled": True, "costs": {}}),
            lambda i: i.update(visible_options=[]),
            lambda i: i.update(focused_id=None),
            lambda i: i.update(review_complete=False),
        ):
            inputs = review_input(); mutate(inputs)
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                build_neow_talk_from_review(**inputs)

    def test_custom_unknown_or_missing_profile_never_defaults_to_cross(self):
        for profile in (None, "", "unknown", "ps5-circle-confirm", "custom"):
            inputs = review_input(); inputs["control_profile"] = profile
            with self.subTest(profile=profile), self.assertRaisesRegex(ChoiceError, "profile"):
                build_neow_talk_from_review(**inputs)

    def test_stale_future_incomplete_or_wrong_source_is_rejected(self):
        for mutate in (
            lambda i: i.update(now=NOW + timedelta(seconds=31)),
            lambda i: i.update(now=NOW - timedelta(seconds=1)),
            lambda i: i["frame"].update(image_sha256="not-a-hash"),
            lambda i: i.update(inventory_digest="unknown"),
            lambda i: i.update(reviewer=""),
            lambda i: i["context"].update(run_id=""),
            lambda i: i["context"].update(floor_id=""),
            lambda i: i["context"].update(combat_id="old-combat"),
            lambda i: i["resources"].update(hp=0),
            lambda i: i["resources"].pop("gold"),
        ):
            inputs = review_input(); mutate(inputs)
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                build_neow_talk_from_review(**inputs)

    def test_standard_observation_cannot_replace_existing_bindings_or_custom_layout(self):
        packet = self.packet()
        with self.assertRaisesRegex(ChoiceError, "existing button"):
            build_neow_talk(packet["observation"], control_profile=CONTROL_PROFILE, now=NOW)
        observation = deepcopy(packet["observation"])
        observation["ui"]["options"][0].pop("activate")
        observation["ui"]["layout_id"] = "custom-layout"
        with self.assertRaises(ChoiceError):
            build_neow_talk(observation, control_profile=CONTROL_PROFILE, now=NOW)

    def test_ordinary_dispatch_validation_still_enforces_freshness_and_integrity(self):
        packet = self.packet()
        validated = validate_choice_proposal(packet["proposal"], packet["observation"], now=NOW)
        self.assertEqual(validated, packet["proposal"])
        with self.assertRaisesRegex(ChoiceError, "stale"):
            validate_choice_proposal(packet["proposal"], packet["observation"], now=NOW + timedelta(seconds=31))
        packet["proposal"]["command"]["buttons"] = ["circle"]
        with self.assertRaisesRegex(ChoiceError, "modified"):
            validate_choice_proposal(packet["proposal"], packet["observation"], now=NOW)

    def test_dialogue_and_new_options_are_verified_without_predicting_rewards(self):
        for phase in ("dialogue", "reward_options"):
            packet = self.packet()
            result = self.verify(packet, reviewed_result(packet, phase=phase))
            self.assertTrue(result["choice_complete"])
            self.assertFalse(result["runtime_authorized"])

    def test_unknown_results_resource_inventory_or_context_changes_do_not_verify(self):
        for mutate in (
            lambda a: a["facts"].update(event_phase="unknown"),
            lambda a: a["facts"].update(event_phase="opening_dialogue"),
            lambda a: a["facts"].update(event_id="other-event"),
            lambda a: a["facts"].update(dialogue_text=""),
            lambda a: a["facts"].update(reward_claimed=True),
            lambda a: a["resources"].update(hp=79),
            lambda a: a["resources"].update(gold=100),
            lambda a: a.update(inventory_digest="d" * 64),
            lambda a: a["context"].update(run_id="other-run"),
            lambda a: a["context"].update(floor_id="floor-1"),
            lambda a: a["ui"].update(screen="map"),
            lambda a: a["review"]["outcome"].update(action_id="other-input"),
        ):
            packet = self.packet(); after = reviewed_result(packet); mutate(after)
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                self.verify(packet, after)

    def test_phase_declaration_alone_does_not_verify_talk(self):
        packet = self.packet(); after = reviewed_result(packet, phase="dialogue")
        after["facts"]["dialogue_text"] = packet["observation"]["facts"]["dialogue_text"]
        with self.assertRaisesRegex(ChoiceError, "changed visible dialogue or options"):
            self.verify(packet, after)

    def test_neow_profile_cannot_expand_outcome_scope_before_dispatch(self):
        for mutate in (
            lambda p: p["resources"].update(gold={"min": 0, "max": 999}),
            lambda p: p.update(inventory_digest="d" * 64),
            lambda p: p["context"].update(floor_id="floor-1"),
            lambda p: p["allow_changed_facts"].append("reward"),
            lambda p: p["facts"].update(reward="invented"),
        ):
            packet = self.packet(); mutate(packet["choice"]["postconditions"])
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                plan_choice_step(packet["observation"], packet["choice"], now=NOW)

    def test_named_rule_revalidates_profile_button_layout_and_exact_source(self):
        for mutate in (
            lambda b, o: b.update(button="circle"),
            lambda b, o: b["evidence"].update(control_profile="custom"),
            lambda b, o: b["evidence"].update(rule_id="arbitrary-confirm"),
            lambda b, o: b["evidence"].update(frame_id="old-frame"),
            lambda b, o: b["evidence"].update(image_sha256="e" * 64),
            lambda b, o: b["evidence"].update(layout_id="custom"),
            lambda b, o: b["evidence"].update(hint_text="invented Cross hint"),
            lambda b, o: b["evidence"].update(reference_id="invented-hardware-proof"),
            lambda b, o: o["ui"].update(selection_mode="toggle"),
            lambda b, o: o["ui"]["options"][0].update(shortcut=deepcopy(b)),
        ):
            packet = self.packet(); observation = packet["observation"]
            mutate(observation["ui"]["options"][0]["activate"], observation)
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                plan_choice_step(observation, packet["choice"], now=NOW)

    def test_result_helper_expands_actual_options_without_assigning_controls(self):
        for phase in ("dialogue", "reward_options"):
            packet = self.packet(); inputs = result_input(packet, phase=phase)
            original = deepcopy((packet, inputs))
            after = build_neow_talk_result_from_review(packet["observation"], **inputs)
            self.assertEqual((packet, inputs), original)
            self.assertEqual(after["context"], packet["observation"]["context"])
            self.assertEqual(after["facts"], inputs["facts"])
            self.assertEqual(after["ui"]["options"], inputs["visible_options"])
            self.assertTrue(self.verify(packet, after)["choice_complete"])
            self.assertEqual(after["review"]["outcome"]["action_id"], packet["proposal"]["action_id"])
            self.assertNotIn("activate", after["ui"]["options"][0])
            self.assertNotIn("shortcut", after["ui"]["options"][0])

    def test_result_helper_accepts_older_before_frame_but_requires_fresh_after(self):
        packet = self.packet(); inputs = result_input(packet)
        inputs["frame"]["observed_at"] = (NOW + timedelta(seconds=60)).isoformat()
        inputs["now"] = NOW + timedelta(seconds=61)
        after = build_neow_talk_result_from_review(packet["observation"], **inputs)
        self.assertEqual(after["frame"], inputs["frame"])
        inputs["now"] = NOW + timedelta(seconds=91)
        with self.assertRaisesRegex(ChoiceError, "stale"):
            build_neow_talk_result_from_review(packet["observation"], **inputs)

    def test_result_helper_rejects_incomplete_changed_or_uncorrelated_result(self):
        for mutate in (
            lambda i: i.update(review_complete=False),
            lambda i: i.update(reviewer=""),
            lambda i: i.update(observed_result=""),
            lambda i: i.update(action_id=None),
            lambda i: i.update(inventory_digest="f" * 64),
            lambda i: i["resources"].update(hp=79),
            lambda i: i["resources"].update(gold=100),
            lambda i: i["facts"].update(event_phase="unknown"),
            lambda i: i["facts"].update(floor=1),
            lambda i: i["facts"].update(ascension=3),
            lambda i: i["facts"].update(invented_reward="gold"),
            lambda i: i.update(visible_options=[]),
            lambda i: i["visible_options"][0].update(costs=None),
            lambda i: i["visible_options"][0].update(activate={"button": "cross"}),
            lambda i: i.update(focused_id="unknown-option"),
            lambda i: i["frame"].update(image_sha256="a" * 64),
            lambda i: i["frame"].update(frame_id="synthetic-neow-1"),
            lambda i: i["frame"].update(observed_at=(NOW - timedelta(seconds=1)).isoformat()),
        ):
            packet = self.packet(); inputs = result_input(packet); mutate(inputs)
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                build_neow_talk_result_from_review(packet["observation"], **inputs)

    def test_result_helper_rejects_different_internal_ids_without_visible_change(self):
        packet = self.packet(); inputs = result_input(packet, phase="dialogue")
        inputs["facts"]["dialogue_text"] = packet["observation"]["facts"]["dialogue_text"]
        inputs["visible_options"][0]["id"] = "renamed-talk"
        inputs["focused_id"] = "renamed-talk"
        with self.assertRaisesRegex(ChoiceError, "changed visible dialogue or options"):
            build_neow_talk_result_from_review(packet["observation"], **inputs)

    def test_result_helper_requires_original_neow_control_contract(self):
        for mutate in (
            lambda o: o["facts"].update(event_id="other-event"),
            lambda o: o["ui"]["options"][0].pop("activate"),
            lambda o: o["ui"]["options"][0]["activate"]["evidence"].update(control_profile="custom"),
            lambda o: o["review"].update(complete=False),
        ):
            packet = self.packet(); inputs = result_input(packet); mutate(packet["observation"])
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                build_neow_talk_result_from_review(packet["observation"], **inputs)


if __name__ == "__main__":
    unittest.main()
