"""Scoped PS5 menu rules for explicitly reviewed UI, with no input or storage.

These named rules express the selected default control profile. They are not
visible hints, recorded controller transitions, automatic recognition or run
authority. Final upgrade confirmation always needs its own current visible hint.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .choice_execution import (_checked, _observation, _proof, _require,
                               _same_json, _text)

CONTROL_PROFILE = "ps5-default-cross-confirm-v1"
RULE_KIND = "documented_control_profile"
EVENT_RULE = "ps5-event-focused-activate-v1"
EVENT_FOCUS_RULE = "ps5-event-adjacent-focus-v1"
GRID_FOCUS_RULE = "ps5-upgrade-grid-adjacent-focus-v1"
GRID_SELECT_RULE = "ps5-upgrade-grid-select-v1"
_RULES = {EVENT_RULE, EVENT_FOCUS_RULE, GRID_FOCUS_RULE, GRID_SELECT_RULE}
_DELTAS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}


def _base(obs, family):
    ui = obs["ui"]
    _require(ui.get("menu_family") == family and ui.get("control_layout") == "ps5_default",
             "explicit supported menu family and default PS5 layout required")
    _require(obs["context"].get("combat_id") is None and obs["context"].get("turn_id") is None,
             "menu profile does not apply to combat choices")
    _require(ui.get("required_count") == 1 and ui.get("focused_id") in ui.get("order", []),
             "one-card/menu quota and reviewed focus required")
    _require(all(option.get("shortcut") is None for option in ui["options"]),
             "scoped menus cannot inherit unrelated shortcuts")
    return ui


def _event(obs):
    ui = _base(obs, "event_options")
    _require(ui.get("screen") == "event" and ui.get("phase") == "choose"
             and ui.get("selection_mode") == "immediate"
             and not ui.get("selected_ids") and not ui.get("pending_ids")
             and ui.get("confirm") is None and _text(obs["facts"].get("event_id")),
             "event rule needs a reviewed immediate event-option menu")
    _require(obs["facts"].get("event_id") != "neow"
             or obs["facts"].get("event_phase") == "reward_options",
             "Neow requires reviewed reward_options here; Talk uses its narrower opening rule")
    if ui.get("grid") is not None:
        _grid(ui)
    return ui


def _upgrade(obs):
    ui = _base(obs, "card_upgrade")
    _require(ui.get("screen") == "selection" and ui.get("phase") in {"choose", "confirm"}
             and ui.get("selection_purpose") == "upgrade" and ui.get("selection_mode") == "toggle",
             "upgrade rules require a reviewed upgrade-only selection screen")
    for option in ui["options"]:
        card = option.get("card")
        _require(isinstance(card, dict) and set(card) == {"name", "upgrade_name"}
                 and _text(card["name"]) and _text(card["upgrade_name"])
                 and card["name"] != card["upgrade_name"] and option["label"] == card["name"]
                 and option.get("costs") == {},
                 "upgrade options need distinct identities, reviewed card/upgrade names and no selection cost")
    if ui["phase"] == "choose":
        _require(not ui["selected_ids"] and not ui["pending_ids"] and ui.get("confirm") is None
                 and ui.get("upgrade_preview") is None, "upgrade grid cannot assume a selection or preview")
        _grid(ui)
    else:
        _preview(obs)
    return ui


def _grid(ui):
    grid = ui.get("grid")
    _require(isinstance(grid, dict) and set(grid) == {"complete", "cells"} and grid["complete"] is True
             and isinstance(grid["cells"], list) and len(grid["cells"]) == len(ui["options"]),
             "complete reviewed menu grid required; hidden/clipped pages are unsupported")
    cells, positions = {}, set()
    for cell in grid["cells"]:
        _require(isinstance(cell, dict) and set(cell) == {"id", "row", "column"}
                 and cell["id"] in ui["order"] and cell["id"] not in cells
                 and all(type(cell[k]) is int and 0 <= cell[k] < 128 for k in ("row", "column")),
                 "unique explicit grid identities and integer coordinates required")
        position = (cell["row"], cell["column"])
        _require(position not in positions, "overlapping grid cells")
        cells[cell["id"]] = position
        positions.add(position)
    _require(sorted(cells, key=cells.get) == ui["order"], "grid order must match the reviewed row-major order")
    return cells


def _preview(obs):
    ui = obs["ui"]
    selected = ui.get("selected_ids")
    _require(isinstance(selected, list) and len(selected) == 1 and ui.get("pending_ids") == selected
             and ui.get("focused_id") == selected[0], "upgrade confirmation needs exactly the reviewed selected card")
    option = next((o for o in ui["options"] if o["id"] == selected[0]), None)
    preview = ui.get("upgrade_preview")
    _require(option is not None and option["enabled"] is True
             and isinstance(preview, dict) and set(preview) == {
                 "option_id", "before_name", "after_name", "observed_upgrade_text"}
             and preview["option_id"] == selected[0]
             and preview["before_name"] == option["card"]["name"]
             and preview["after_name"] == option["card"]["upgrade_name"]
             and _text(preview["observed_upgrade_text"]),
             "actual selected-card upgrade preview must match its reviewed identity and upgrade")
    _require(not ui["navigation"] and all(o.get("activate") is None for o in ui["options"]),
             "preview cannot carry grid controls forward")
    confirm = ui.get("confirm")
    _require(isinstance(confirm, dict) and confirm.get("button") in {"cross", "triangle"}
             and isinstance(confirm.get("evidence"), dict)
             and confirm["evidence"].get("kind") == "visible_hint",
             "upgrade commit requires its freshly visible Cross/Triangle confirm hint")
    _proof(confirm, obs, "confirm:" + ui["choice_id"])
    return preview


def validate_menu_control_binding(binding, observation, meaning, *, navigation=False):
    """Single registry entry point, including the existing exact Neow Talk rule."""
    from .neow_start import CONTROL_RULE, validate_neow_control_binding
    proof, frame = binding["evidence"], observation["frame"]
    if proof.get("rule_id") == CONTROL_RULE:
        return validate_neow_control_binding(binding, observation, meaning, navigation=navigation)
    rule = proof.get("rule_id")
    _require(rule in _RULES and proof.get("control_profile") == CONTROL_PROFILE
             and proof.get("frame_id") == frame["frame_id"]
             and proof.get("image_sha256") == frame["image_sha256"],
             "known scoped control rule, selected default profile and current source required")
    _require(not any(k in proof for k in ("hint_text", "reference_id", "before_sha256", "after_sha256")),
             "control rule must not claim observed hints or hardware transitions")
    if rule in {EVENT_RULE, EVENT_FOCUS_RULE}:
        ui = _event(observation)
        if rule == EVENT_RULE:
            # The planner invokes activation only after focus equals its target;
            # bindings for other visible options support a later fresh replan.
            _require(not navigation and binding["button"] == "cross"
                     and any(meaning == "activate:" + o["id"] and o["enabled"] is True for o in ui["options"]),
                     "event rule only activates an enabled visible option after reviewed focus")
        else:
            _adjacent_binding(binding, ui, meaning, navigation)
    else:
        ui = _upgrade(observation)
        _require(ui["phase"] == "choose", "grid controls do not commit an upgrade preview")
        if rule == GRID_SELECT_RULE:
            _require(not navigation and binding["button"] == "cross"
                     and any(meaning == "activate:" + o["id"] and o["enabled"] is True for o in ui["options"]),
                     "upgrade select rule needs an enabled grid card")
        else:
            _adjacent_binding(binding, ui, meaning, navigation)


def _adjacent_binding(binding, ui, meaning, navigation):
    cells = _grid(ui)
    source, target = binding.get("from"), binding.get("to")
    enabled = {o["id"] for o in ui["options"] if o["enabled"]}
    _require(navigation and binding["button"] in _DELTAS and source in enabled and target in enabled
             and meaning == f"focus:{source}->{target}", "grid focus needs two enabled reviewed cells")
    dr, dc = _DELTAS[binding["button"]]
    _require(cells[target] == (cells[source][0] + dr, cells[source][1] + dc),
             "grid rule allows adjacent geometry only; no wrap, skip or hidden cell")


def validate_menu_choice(choice, observation):
    """Menu semantics augment, and never replace, ordinary outcome/cost checks."""
    family = observation["ui"].get("menu_family")
    if family == "event_options":
        _event(observation)
    elif family == "card_upgrade":
        _upgrade(observation)
        post = choice.get("postconditions")
        _require(choice.get("kind") == "selection" and isinstance(post, dict)
                 and post.get("inventory_digest") not in {None, "unchanged", observation["inventory_digest"]}
                 and _same_json(post.get("resources"), observation["resources"])
                 and post.get("context") == observation["context"],
                 "upgrade must declare its exact changed inventory and unchanged resources/run context")
        wanted = choice.get("option_ids", [])
        target = next((o for o in observation["ui"]["options"] if o["id"] in wanted), None)
        _require(target is not None and post.get("facts", {}).get("upgraded_card_id") == target["id"]
                 and post["facts"].get("upgraded_card_name") == target["card"]["upgrade_name"],
                 "upgrade outcome must name exactly the selected card and its upgraded name")


def verify_upgrade_selection(proposal, before, after):
    """Allow the observed selected-card preview to replace the original grid.

    The ordinary verifier has already required a fresh source and unchanged
    resources, inventory, facts and context. This only scopes the UI transition.
    """
    b, a = _upgrade(before), _upgrade(after)
    wanted = proposal["choice"]["option_ids"]
    _require(b["phase"] == "choose" and a["phase"] == "confirm"
             and a["choice_id"] == b["choice_id"] and a["layout_id"] == b["layout_id"]
             and a["selected_ids"] == wanted and a["pending_ids"] == wanted,
             "upgrade preview must follow this exact grid selection")
    def option(o):
        return {k: v for k, v in o.items() if k not in {"activate", "shortcut"}}
    before_options = {o["id"]: option(o) for o in b["options"]}
    _require(a["order"] in [wanted, b["order"]]
             and all(_same_json(option(o), before_options.get(o["id"])) for o in a["options"]),
             "preview options must be the selected card or the unchanged reviewed grid")
    _require(a.get("grid") is None or _same_json(a["grid"], b["grid"]), "preview changed grid geometry")
    # Keep all unrelated UI declarations exact; only these observed selection
    # fields may change. Never hide gameplay changes in an arbitrary UI key.
    changed = {"phase", "options", "order", "selected_ids", "pending_ids", "navigation",
               "confirm", "upgrade_preview", "grid"}
    _require(_same_json({k: v for k, v in b.items() if k not in changed},
                        {k: v for k, v in a.items() if k not in changed}),
             "upgrade preview changed unrelated UI facts")


def _binding(obs, button, meaning, rule):
    return {"button": button, "evidence": {"kind": RULE_KIND, "reviewer": obs["review"]["reviewer"],
        "meaning": meaning, "layout_id": obs["ui"]["layout_id"], "rule_id": rule,
        "control_profile": CONTROL_PROFILE, "frame_id": obs["frame"]["frame_id"],
        "image_sha256": obs["frame"]["image_sha256"]}}


def _bind_adjacency(obs, rule):
    ui = obs["ui"]
    cells = _grid(ui)
    positions = {position: identity for identity, position in cells.items()}
    enabled = {o["id"] for o in ui["options"] if o["enabled"]}
    existing = {(edge["from"], edge["button"]) for edge in ui["navigation"]}
    for source, (row, column) in cells.items():
        for button, (dr, dc) in _DELTAS.items():
            target = positions.get((row + dr, column + dc))
            if source in enabled and target in enabled and (source, button) not in existing:
                ui["navigation"].append(dict(_binding(obs, button, f"focus:{source}->{target}", rule),
                                              **{"from": source, "to": target}))


@_checked
def bind_reviewed_menu_controls(observation, *, control_profile, now=None, max_age_seconds=30):
    """Copy a complete current review and add only the named controls in scope.

    Preview confirmation is validated from a fresh visible hint, never supplied
    by the profile. Review/capture, strategy, inventory and authority stay with
    callers. Existing bindings are preserved and independently validated.
    """
    _require(control_profile == CONTROL_PROFILE, "explicit default PS5 control profile required")
    _require(max_age_seconds is not None, "freshness cannot be bypassed when attaching controls")
    clock = now or datetime.now(timezone.utc)
    obs = _observation(observation, clock, max_age_seconds)
    ui = obs["ui"]
    if ui.get("menu_family") == "event_options":
        _event(obs)
        for option in ui["options"]:
            if option["enabled"] and option.get("activate") is None:
                option["activate"] = _binding(obs, "cross", "activate:" + option["id"], EVENT_RULE)
        if ui.get("grid") is not None:
            _bind_adjacency(obs, EVENT_FOCUS_RULE)
    elif ui.get("menu_family") == "card_upgrade":
        _upgrade(obs)
        if ui["phase"] == "choose":
            for option in ui["options"]:
                if option["enabled"] and option.get("activate") is None:
                    option["activate"] = _binding(obs, "cross", "activate:" + option["id"], GRID_SELECT_RULE)
            _bind_adjacency(obs, GRID_FOCUS_RULE)
    else:
        _require(False, "no default control rule for this menu family")
    checked = _observation(obs, clock, max_age_seconds)
    for option in checked["ui"]["options"]:
        if option.get("activate") is not None:
            _proof(option["activate"], checked, "activate:" + option["id"])
    return checked
