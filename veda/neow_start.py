"""One reviewed Neow opening Talk step, with no capture, storage or controller I/O.

The default PS5 confirm mapping is a named control-profile rule, not a claim
that a button hint or successful hardware transition was seen in the image.
The operator still declares the exact reviewed frame and current run context;
the reviewed-play adapter owns source validation, authority and pending input.
"""
from __future__ import annotations

from copy import deepcopy

from .choice_execution import (SCHEMA, _checked, _json, _require, _text, _time,
                               plan_choice_step, verify_choice_step)

CONTROL_PROFILE = "ps5-default-cross-confirm-v1"
CONTROL_RULE = "neow-opening-talk-confirm-v1"
LAYOUT = "neow-opening-ps5-default-v1"
RULE_KIND = "documented_control_profile"
_CHANGING_FACTS = ["event_phase", "dialogue_text"]


def _validate_neow_observation(obs):
    facts, ui, context = obs["facts"], obs["ui"], obs["context"]
    _require(facts.get("event_id") == "neow"
             and facts.get("event_phase") == "opening_dialogue"
             and type(facts.get("act")) is int and facts["act"] == 1
             and type(facts.get("floor")) is int and facts["floor"] == 0,
             "Neow opening dialogue at act 1 floor 0 must be explicitly reviewed")
    _require(facts.get("character") in {"Ironclad", "Silent", "Defect", "Watcher"}
             and type(facts.get("ascension")) is int and 0 <= facts["ascension"] <= 20
             and _text(facts.get("dialogue_text")), "reviewed character, ascension and dialogue required")
    _require(context.get("combat_id") is None and context.get("turn_id") is None,
             "Neow Talk cannot use a combat or turn context")
    resources = obs["resources"]
    _require(all(type(resources.get(k)) is int and resources[k] >= 0 for k in ("hp", "max_hp", "gold"))
             and 0 < resources["hp"] <= resources["max_hp"], "reviewed living HP, maximum HP and gold required")
    _require(ui.get("screen") == "event" and ui.get("phase") == "choose"
             and ui.get("layout_id") == LAYOUT
             and ui.get("selection_mode") == "immediate" and ui.get("required_count") == 1
             and ui.get("focused_id") == "talk" and ui.get("order") == ["talk"]
             and ui.get("selected_ids") == [] and ui.get("pending_ids") == []
             and ui.get("navigation") == [] and ui.get("confirm") is None,
             "default-profile Neow Talk needs one immediate focused option")
    options = ui.get("options")
    _require(isinstance(options, list) and len(options) == 1, "Neow opening must show only Talk")
    option = options[0]
    _require(option.get("id") == "talk" and option.get("label") in {"Talk", "[Talk]"}
             and option.get("role") == "neow_talk" and option.get("enabled") is True
             and option.get("costs") == {} and option.get("shortcut") is None,
             "Neow opening Talk must be enabled, free and focused; reward choices are separate")


def uses_neow_control_rule(observation):
    """Identify the scoped profile binding before validating its full contract."""
    ui = observation.get("ui", {})
    bindings = [ui.get("confirm")]
    for option in ui.get("options", []):
        bindings.extend((option.get("activate"), option.get("shortcut")))
    return any(isinstance(binding, dict)
               and isinstance(binding.get("evidence"), dict)
               and binding["evidence"].get("kind") == RULE_KIND for binding in bindings)


def validate_neow_control_binding(binding, observation, meaning, *, navigation=False):
    _validate_neow_observation(observation)
    proof, frame = binding["evidence"], observation["frame"]
    _require(not navigation and binding.get("button") == "cross" and meaning == "activate:talk"
             and proof.get("control_profile") == CONTROL_PROFILE
             and proof.get("rule_id") == CONTROL_RULE
             and proof.get("frame_id") == frame["frame_id"]
             and proof.get("image_sha256") == frame["image_sha256"],
             "named Neow rule requires the explicitly selected default PS5 profile and current source")
    _require(not any(key in proof for key in ("hint_text", "before_sha256", "after_sha256", "reference_id")),
             "control-profile rule must not claim a visible hint or hardware transition")


def neow_postconditions(observation):
    return {"screen": "event", "phase": "choose", "context": deepcopy(observation["context"]),
            "resources": deepcopy(observation["resources"]), "inventory_digest": "unchanged",
            "facts": {k: deepcopy(v) for k, v in observation["facts"].items() if k not in _CHANGING_FACTS},
            "allow_changed_facts": list(_CHANGING_FACTS)}


def validate_neow_choice(choice, observation):
    _validate_neow_observation(observation)
    _require(choice.get("kind") == "event" and choice.get("option_ids") == ["talk"]
             and choice.get("postconditions") == neow_postconditions(observation),
             "Neow Talk only advances dialogue/options with unchanged run, floor, resources and inventory")


def verify_neow_result(before, after):
    facts = after["facts"]
    _require(facts.get("event_phase") in {"dialogue", "reward_options"}
             and _text(facts.get("dialogue_text")), "Neow Talk needs a reviewed dialogue or reward-options result")
    def visible_options(obs):
        # Option IDs and roles are bookkeeping, not evidence that the visible
        # dialogue advanced. Renaming them cannot verify a successful Talk.
        return [{k: option.get(k) for k in ("label", "enabled", "costs")}
                for option in obs["ui"]["options"]]
    _require(facts["dialogue_text"] != before["facts"]["dialogue_text"]
             or visible_options(after) != visible_options(before),
             "Neow Talk needs changed visible dialogue or options, not just a phase declaration")


@_checked
def build_neow_talk(observation, *, control_profile, now=None, max_age_seconds=30):
    """Build one ordinary choice packet; never grant permission or send Cross."""
    obs = _json(observation)
    _require(control_profile == CONTROL_PROFILE, "explicit default PS5 control profile required")
    _validate_neow_observation(obs)
    frame, ui = obs["frame"], obs["ui"]
    _require(ui["options"][0].get("activate") is None, "do not replace an existing button binding")
    ui["options"][0]["activate"] = {"button": "cross", "evidence": {
        "kind": RULE_KIND, "reviewer": obs["review"]["reviewer"], "meaning": "activate:talk",
        "layout_id": LAYOUT, "control_profile": control_profile, "rule_id": CONTROL_RULE,
        "frame_id": frame["frame_id"], "image_sha256": frame["image_sha256"]}}
    choice = {"kind": "event", "choice_id": ui["choice_id"], "option_ids": ["talk"],
              "review": dict(obs["review"], kind="reviewed_choice"),
              "postconditions": neow_postconditions(obs)}
    proposal = plan_choice_step(obs, choice, now=now, max_age_seconds=max_age_seconds)
    return {"observation": obs, "choice": choice, "proposal": proposal}


@_checked
def build_neow_talk_from_review(*, frame, context, reviewer, resources, inventory_digest,
                                facts, visible_options, focused_id, control_profile,
                                review_complete=False, now=None, max_age_seconds=30):
    """Expand a small explicit review into standard choice contracts.

    ``visible_options`` is the complete reviewed list, not an inferred subset.
    Input facts must identify Neow's opening, act/floor, character/ascension and
    visible dialogue; neither a generic Talk label nor remembered state suffices.
    """
    _require(review_complete is True, "explicit completed source review required")
    options = _json(visible_options)
    _require(isinstance(options, list) and len(options) == 1 and isinstance(options[0], dict)
             and set(options[0]) == {"id", "label", "enabled", "costs"},
             "supply the complete single visible Talk option with explicit costs")
    options[0]["role"] = "neow_talk"
    observation = {"schema": SCHEMA, "frame": frame, "context": context,
        "review": {"kind": "reviewed_choice_ui", "reviewer": reviewer, "complete": True,
                   "frame_id": frame["frame_id"], "image_sha256": frame["image_sha256"]},
        "resources": resources, "inventory_digest": inventory_digest, "facts": facts,
        "ui": {"screen": "event", "phase": "choose", "choice_id": "neow-opening-talk",
               "layout_id": LAYOUT, "options": options, "order": [option["id"] for option in options],
               "focused_id": focused_id, "selection_mode": "immediate", "required_count": 1,
               "selected_ids": [], "pending_ids": [], "navigation": []}}
    return build_neow_talk(observation, control_profile=control_profile, now=now,
                           max_age_seconds=max_age_seconds)


@_checked
def build_neow_talk_result_from_review(before_observation, *, frame, reviewer, resources,
                                       inventory_digest, facts, visible_options, focused_id,
                                       action_id, observed_result, review_complete=False,
                                       now=None, max_age_seconds=30):
    """Expand an actual Talk-result review, without predicting reward contents.

    Return only the ordinary after observation. The adapter must still match
    the action to its durable pending record and verify the actual source file.
    A source-bound review declaration here is neither that pending record nor
    proof that controller input occurred. No next-choice controls are assigned.
    """
    _require(review_complete is True, "explicit completed result review required")
    before = _json(before_observation)
    _validate_neow_observation(before)
    _require(uses_neow_control_rule(before), "result requires the original scoped Neow Talk observation")
    options = _json(visible_options)
    _require(isinstance(options, list) and 1 <= len(options) <= 128
             and all(isinstance(option, dict) and set(option) == {"id", "label", "enabled", "costs"}
                     for option in options), "complete reviewed result options with explicit costs required")
    after = {"schema": SCHEMA, "frame": frame, "context": deepcopy(before["context"]),
        "review": {"kind": "reviewed_choice_ui", "reviewer": reviewer, "complete": True,
                   "frame_id": frame["frame_id"], "image_sha256": frame["image_sha256"],
                   "outcome": {"action_id": action_id,
                       "before_frame_id": before["frame"]["frame_id"],
                       "before_sha256": before["frame"]["image_sha256"],
                       "choice_id": before["ui"]["choice_id"], "option_ids": ["talk"],
                       "observed_result": observed_result}},
        "resources": resources, "inventory_digest": inventory_digest, "facts": facts,
        "ui": {"screen": "event", "phase": "choose", "choice_id": "neow-after-talk",
               "layout_id": "neow-after-talk-reviewed-v1", "options": options,
               "order": [option["id"] for option in options], "focused_id": focused_id,
               "selection_mode": "immediate", "required_count": 1,
               "selected_ids": [], "pending_ids": [], "navigation": []}}
    # Verification permits a legitimately older before frame, but assesses the
    # after frame at the current clock. This does not prepare another input.
    choice = {"kind": "event", "choice_id": before["ui"]["choice_id"], "option_ids": ["talk"],
              "review": dict(before["review"], kind="reviewed_choice"),
              "postconditions": neow_postconditions(before)}
    proposal = plan_choice_step(before, choice, now=_time(before["frame"]["observed_at"]),
                                max_age_seconds=max_age_seconds, action_id=action_id)
    verify_choice_step(proposal, before, after, now=now)
    return _json(after)
