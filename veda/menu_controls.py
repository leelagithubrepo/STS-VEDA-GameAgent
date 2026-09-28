"""Scoped PS5 menu rules for explicitly reviewed UI, with no input or storage.

These named rules express the selected default control profile. They are not
visible hints, recorded controller transitions, automatic recognition or run
authority. Final upgrade confirmation always needs its own current visible hint.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .choice_execution import (_checked, _observation, _proof, _require,
                               _same_json, _text)
from . import shop_controls

CONTROL_PROFILE = "ps5-default-cross-confirm-v1"
RULE_KIND = "documented_control_profile"
EVENT_RULE = "ps5-event-focused-activate-v1"
EVENT_FOCUS_RULE = "ps5-event-adjacent-focus-v1"
NEOW_LEAVE_RULE = "ps5-neow-granted-leave-v1"
GRID_FOCUS_RULE = "ps5-upgrade-grid-adjacent-focus-v1"
GRID_SELECT_RULE = "ps5-upgrade-grid-select-v1"
MAP_FOCUS_RULE = "ps5-map-adjacent-sibling-focus-v1"
MAP_SELECT_RULE = "ps5-map-focused-node-select-v1"
MAP_INSPECT_RULE = "ps5-map-directional-inspection-v1"
LOOT_SELECT_RULE = "ps5-loot-focused-select-v1"
LOOT_FOCUS_RULE = "ps5-loot-adjacent-focus-v1"
_RULES = {LOOT_SELECT_RULE, LOOT_FOCUS_RULE, EVENT_RULE, EVENT_FOCUS_RULE, NEOW_LEAVE_RULE, GRID_FOCUS_RULE, GRID_SELECT_RULE,
          MAP_FOCUS_RULE, MAP_SELECT_RULE, MAP_INSPECT_RULE}
_DELTAS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
_MAP_SCREENS = {"enemy": {"combat"}, "elite": {"combat"}, "boss": {"combat"},
                "rest": {"rest"}, "merchant": {"shop"}, "treasure": {"treasure", "reward"},
                "event": {"event", "combat", "shop", "treasure", "reward"}}


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


def _loot(obs):
    family = obs['ui'].get('menu_family')
    ui = _base(obs, family)
    _require(family in {'loot_rewards', 'loot_cards'}
             and ui['screen'] == ('reward' if family == 'loot_rewards' else 'card_reward')
             and ui['phase'] == 'choose' and ui['selection_mode'] == 'immediate'
             and not ui['selected_ids'] and not ui['pending_ids'] and ui.get('confirm') is None,
             'loot controls require a reviewed immediate reward screen')
    _require(obs['facts'].get('reward_source') == 'combat', 'routine loot is scoped to combat rewards')
    _grid(ui)
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


def _neow_leave(obs):
    """Only the observed post-reward Neow Leave, never a generic event exit."""
    ui = _base(obs, "event_leave")
    facts, resources = obs["facts"], obs["resources"]
    _require(facts.get("event_id") == "neow"
             and facts.get("event_phase") == "reward_resolved"
             and type(facts.get("act")) is int and facts["act"] == 1
             and type(facts.get("floor")) is int and facts["floor"] == 0
             and facts.get("dialogue_text") in {"Granted...", "Granted…"},
             "Neow Leave needs the reviewed Granted result at act 1 floor 0")
    _require(all(type(resources.get(k)) is int and resources[k] >= 0 for k in ("hp", "max_hp", "gold"))
             and 0 < resources["hp"] <= resources["max_hp"],
             "Neow Leave needs reviewed living HP, maximum HP and gold")
    _require(ui.get("screen") == "event" and ui.get("phase") == "choose"
             and ui.get("selection_mode") == "immediate"
             and ui.get("focused_id") == "leave" and ui.get("order") == ["leave"]
             and ui.get("selected_ids") == [] and ui.get("pending_ids") == []
             and ui.get("navigation") == [] and ui.get("confirm") is None
             and ui.get("grid") is None and ui.get("upgrade_preview") is None
             and len(ui["options"]) == 1,
             "Neow Leave requires exactly one immediate focused option without selection or navigation")
    option = ui["options"][0]
    _require(option.get("id") == "leave" and option.get("label") in {"Leave", "[Leave]"}
             and option.get("enabled") is True and option.get("costs") == {}
             and option.get("shortcut") is None,
             "Neow Leave must be enabled, free and focused")
    activate = option.get("activate")
    _require(activate is None or isinstance(activate, dict)
             and activate.get("button") == "cross" and isinstance(activate.get("evidence"), dict)
             and activate["evidence"].get("kind") == RULE_KIND
             and activate["evidence"].get("rule_id") == NEOW_LEAVE_RULE,
             "Neow Leave only uses its named default-profile Cross rule")
    return ui


def _map_ui(obs, family):
    ui = _base(obs, family)
    _require(ui.get("screen") == "map" and ui.get("phase") == "choose"
             and ui.get("selection_mode") == "immediate"
             and ui.get("selected_ids") == [] and ui.get("pending_ids") == []
             and ui.get("confirm") is None and ui.get("grid") is None
             and ui.get("upgrade_preview") is None,
             "map controls require a reviewed immediate map without a card grid or confirmation")
    facts, resources = obs["facts"], obs["resources"]
    _require(type(facts.get("act")) is int and 1 <= facts["act"] <= 4
             and type(facts.get("floor")) is int and 0 <= facts["floor"] <= 99
             and _text(facts.get("current_node_id")),
             "map controls require the reviewed act, floor and stable current node")
    _require(all(type(resources.get(k)) is int and resources[k] >= 0 for k in ("hp", "max_hp", "gold"))
             and 0 < resources["hp"] <= resources["max_hp"],
             "map controls require reviewed living HP, maximum HP and gold")
    _require(all(o.get("enabled") is True and o.get("costs") == {} for o in ui["options"]),
             "map controls require enabled free options")
    return ui


def _map_nodes(obs):
    """A complete current set of selectable siblings, not a two-dimensional grid.

    Completeness, geometry and reachability are explicit reviewer declarations.
    They do not recognize pixels, establish a full-act route or identify a boss.
    An unread sibling kind stays unknown; selecting another known node remains
    possible once every selectable sibling and its focus order are reviewed.
    """
    ui = _map_ui(obs, "map_nodes")
    siblings = ui.get("map_siblings")
    _require(ui.get("map_inspection") is None and isinstance(siblings, dict)
             and set(siblings) == {"complete", "selectable_count", "from_node_id", "evidence_note"}
             and siblings["complete"] is True
             and type(siblings["selectable_count"]) is int
             and siblings["selectable_count"] == len(ui["options"])
             and 1 <= siblings["selectable_count"] <= 7
             and siblings["from_node_id"] == obs["facts"]["current_node_id"]
             and _text(siblings["evidence_note"]),
             "complete reviewed selectable siblings, exact count and current-node evidence required")
    positions = []
    for option in ui["options"]:
        node = option.get("node")
        _require(isinstance(node, dict) and set(node) == {
            "node_id", "kind", "act", "floor", "x", "y", "reachable",
            "reachability_evidence", "classification_evidence"}
                 and node["node_id"] == option["id"]
                 and node["node_id"] != siblings["from_node_id"]
                 and node["kind"] in set(_MAP_SCREENS) | {"unknown"}
                 and type(node["act"]) is int and node["act"] == obs["facts"]["act"]
                 and type(node["floor"]) is int and node["floor"] == obs["facts"]["floor"] + 1
                 and all(type(node[k]) is int and 0 <= node[k] <= 32768 for k in ("x", "y"))
                 and node["reachable"] is True and _text(node["reachability_evidence"])
                 and _text(node["classification_evidence"]),
                 "each map sibling needs a stable next-floor identity, kind, coordinates and reachability evidence")
        positions.append(node["x"])
        activate = option.get("activate")
        _require(activate is None or isinstance(activate, dict)
                 and activate.get("button") == "cross" and isinstance(activate.get("evidence"), dict)
                 and activate["evidence"].get("kind") == RULE_KIND
                 and activate["evidence"].get("rule_id") == MAP_SELECT_RULE,
                 "map nodes use only their explicitly selected default-profile Cross rule")
    _require(positions == sorted(set(positions)),
             "map sibling order must be the complete unique left-to-right screen order")
    for edge in ui["navigation"]:
        _require(edge.get("evidence", {}).get("kind") == RULE_KIND
                 and edge["evidence"].get("rule_id") == MAP_FOCUS_RULE,
                 "map siblings cannot inherit unrelated navigation controls")
    return ui


def _map_inspect(obs):
    """A declared survey action; the profile is not a fabricated visible option."""
    ui = _map_ui(obs, "map_inspect")
    inspection = ui.get("map_inspection")
    _require(ui.get("map_siblings") is None and isinstance(inspection, dict)
             and set(inspection) == {"direction", "evidence_note", "purpose"}
             and inspection["direction"] in {"up", "down"}
             and inspection["purpose"] == "survey" and _text(inspection["evidence_note"]),
             "map inspection requires a reviewed upper/lower survey purpose")
    direction = inspection["direction"]
    identity = "inspect-" + direction
    _require(ui["order"] == [identity] and ui["focused_id"] == identity
             and ui["navigation"] == [] and len(ui["options"]) == 1,
             "map inspection is exactly one directional survey action")
    option = ui["options"][0]
    _require(option.get("role") == "map_inspection"
             and option.get("label") == ("Inspect upper map" if direction == "up" else "Inspect lower map")
             and option.get("node") is None,
             "map inspection must be identified as a survey, not a selectable node")
    activate = option.get("activate")
    _require(activate is None or isinstance(activate, dict)
             and activate.get("button") == direction and isinstance(activate.get("evidence"), dict)
             and activate["evidence"].get("kind") == RULE_KIND
             and activate["evidence"].get("rule_id") == MAP_INSPECT_RULE,
             "map inspection uses only its named directional default-profile rule")
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
    _require(rule in _RULES | shop_controls.RULES and proof.get("control_profile") == CONTROL_PROFILE
             and proof.get("frame_id") == frame["frame_id"]
             and proof.get("image_sha256") == frame["image_sha256"],
             "known scoped control rule, selected default profile and current source required")
    _require(not any(k in proof for k in ("hint_text", "reference_id", "before_sha256", "after_sha256")),
             "control rule must not claim observed hints or hardware transitions")
    if rule in shop_controls.RULES:
        shop_controls.validate_binding(binding, observation, meaning, navigation)
    elif rule in {LOOT_SELECT_RULE, LOOT_FOCUS_RULE}:
        ui = _loot(observation)
        if rule == LOOT_SELECT_RULE:
            _require(not navigation and binding['button'] == 'cross'
                     and any(meaning == 'activate:' + o['id'] for o in ui['options']),
                     'loot select only activates the reviewed focused option')
        else:
            _adjacent_binding(binding, ui, meaning, navigation)
    elif rule == NEOW_LEAVE_RULE:
        _neow_leave(observation)
        _require(not navigation and binding["button"] == "cross" and meaning == "activate:leave",
                 "Neow Leave rule only activates the reviewed Leave with Cross")
    elif rule == MAP_INSPECT_RULE:
        ui = _map_inspect(observation)
        direction = ui["map_inspection"]["direction"]
        _require(not navigation and binding["button"] == direction
                 and meaning == "activate:inspect-" + direction,
                 "map inspection rule only sends its single declared survey direction")
    elif rule in {MAP_SELECT_RULE, MAP_FOCUS_RULE}:
        ui = _map_nodes(observation)
        if rule == MAP_SELECT_RULE:
            _require(not navigation and binding["button"] == "cross"
                     and any(meaning == "activate:" + o["id"] for o in ui["options"]),
                     "map select rule only activates a reviewed reachable sibling")
        else:
            source, target = binding.get("from"), binding.get("to")
            _require(navigation and binding["button"] in {"left", "right"}
                     and source in ui["order"] and target in ui["order"]
                     and meaning == f"focus:{source}->{target}",
                     "map focus needs two reviewed siblings and a horizontal direction")
            delta = -1 if binding["button"] == "left" else 1
            _require(ui["order"].index(target) == ui["order"].index(source) + delta,
                     "map focus allows one adjacent sibling only; no wrap, skip or hidden node")
    elif rule in {EVENT_RULE, EVENT_FOCUS_RULE}:
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
    if family in shop_controls.FAMILIES:
        shop_controls.checked_ui(observation)
    elif family == "event_options":
        _event(observation)
    elif family == "map_nodes":
        ui = _map_nodes(observation)
        wanted = choice.get("option_ids", [])
        _require(choice.get("kind") == "map" and len(wanted) == 1,
                 "map selection requires exactly one reviewed node")
        target = next((o["node"] for o in ui["options"] if o["id"] == wanted[0]), None)
        _require(target is not None and target["kind"] in _MAP_SCREENS,
                 "chosen map node kind needs review; an unread sibling can remain unknown")
        post = choice.get("postconditions", {})
        branches = post.get("alternatives", [{"postconditions": post}])
        for branch in branches:
            result = branch["postconditions"]
            _require(result.get("screen") in _MAP_SCREENS[target["kind"]]
                     and result.get("phase") == "result"
                     and result.get("inventory_digest") in {"unchanged", observation["inventory_digest"]}
                     and result.get("resources", {}).get("max_hp") == observation["resources"]["max_hp"],
                     "map entry requires a matching room result, preserved inventory and exact maximum HP")
            context = result.get("context", {})
            _require(context.get("floor_id") != observation["context"]["floor_id"]
                     and (all(_text(context.get(key)) for key in ("combat_id", "turn_id"))
                          if result["screen"] == "combat"
                          else context.get("combat_id") is None and context.get("turn_id") is None),
                     "map entry needs a new provisional floor and combat context only for a combat room")
            required = {"act": target["act"], "floor": target["floor"],
                        "current_node_id": target["node_id"], "node_type": target["kind"]}
            _require(all(key in result.get("facts", {})
                         and _same_json(result["facts"][key], value) for key, value in required.items()),
                     "map entry must constrain the selected act, next floor, node identity and map kind")
    elif family == "map_inspect":
        ui = _map_inspect(observation)
        post = choice.get("postconditions", {})
        _require(choice.get("kind") == "map" and choice.get("option_ids") == ui["order"]
                 and post.get("screen") == "map" and post.get("phase") == "result"
                 and _same_json(post.get("context"), observation["context"])
                 and _same_json(post.get("resources"), observation["resources"])
                 and post.get("inventory_digest") in {"unchanged", observation["inventory_digest"]}
                 and post.get("allow_changed_facts") == []
                 and all(key in observation["facts"] and _same_json(value, observation["facts"][key])
                         for key, value in post.get("facts", {}).items()),
                 "map inspection must preserve gameplay context, resources, inventory and facts")
    elif family == "event_leave":
        _neow_leave(observation)
        post = choice.get("postconditions")
        _require(choice.get("kind") == "event" and choice.get("option_ids") == ["leave"]
                 and isinstance(post, dict) and post.get("screen") == "map"
                 and post.get("phase") in {"choose", "result"}
                 and _same_json(post.get("context"), observation["context"])
                 and _same_json(post.get("resources"), observation["resources"])
                 and post.get("inventory_digest") in {"unchanged", observation["inventory_digest"]},
                 "Neow Leave only arrives at the map with unchanged context, resources and inventory")
        changing = {"event_phase", "dialogue_text"}
        _require(set(post.get("allow_changed_facts", [])) <= changing
                 and all(key in changing or key in observation["facts"]
                         and _same_json(value, observation["facts"][key])
                         for key, value in post.get("facts", {}).items()),
                 "Neow Leave cannot change unrelated reviewed gameplay facts")
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
    clock = now or datetime.now(timezone.utc)
    obs = _observation(observation, clock, max_age_seconds)
    ui = obs["ui"]
    if ui.get('menu_family') in shop_controls.FAMILIES:
        shop_controls.bind(obs)
    elif ui.get('menu_family') in {'loot_rewards', 'loot_cards'}:
        _loot(obs)
        for option in ui['options']:
            hint = option.pop('activate_hint', None)
            if hint is not None:
                _require(isinstance(hint, dict) and set(hint) == {'button', 'hint_text'}
                         and option.get('activate') is None, 'one explicit loot activation hint required')
                option['activate'] = {'button': hint['button'], 'evidence': {
                    'kind': 'visible_hint', 'reviewer': obs['review']['reviewer'],
                    'meaning': 'activate:' + option['id'], 'layout_id': ui['layout_id'],
                    'frame_id': obs['frame']['frame_id'], 'image_sha256': obs['frame']['image_sha256'],
                    'hint_text': hint['hint_text']}}
            if option['enabled'] and option.get('activate') is None:
                option['activate'] = _binding(obs, 'cross', 'activate:' + option['id'], LOOT_SELECT_RULE)
        _bind_adjacency(obs, LOOT_FOCUS_RULE)
    elif ui.get("menu_family") == "event_options":
        _event(obs)
        for option in ui["options"]:
            if option["enabled"] and option.get("activate") is None:
                option["activate"] = _binding(obs, "cross", "activate:" + option["id"], EVENT_RULE)
        if ui.get("grid") is not None:
            _bind_adjacency(obs, EVENT_FOCUS_RULE)
    elif ui.get("menu_family") == "event_leave":
        _neow_leave(obs)
        option = ui["options"][0]
        if option.get("activate") is None:
            option["activate"] = _binding(obs, "cross", "activate:leave", NEOW_LEAVE_RULE)
    elif ui.get("menu_family") == "card_upgrade":
        _upgrade(obs)
        if ui["phase"] == "choose":
            for option in ui["options"]:
                if option["enabled"] and option.get("activate") is None:
                    option["activate"] = _binding(obs, "cross", "activate:" + option["id"], GRID_SELECT_RULE)
            _bind_adjacency(obs, GRID_FOCUS_RULE)
    elif ui.get("menu_family") == "map_nodes":
        _map_nodes(obs)
        for option in ui["options"]:
            if option.get("activate") is None:
                option["activate"] = _binding(obs, "cross", "activate:" + option["id"], MAP_SELECT_RULE)
        existing = {(edge["from"], edge["button"]) for edge in ui["navigation"]}
        for index, source in enumerate(ui["order"]):
            for button, offset in (("left", -1), ("right", 1)):
                target_index = index + offset
                if 0 <= target_index < len(ui["order"]) and (source, button) not in existing:
                    target = ui["order"][target_index]
                    ui["navigation"].append(dict(_binding(obs, button, f"focus:{source}->{target}", MAP_FOCUS_RULE),
                                                  **{"from": source, "to": target}))
    elif ui.get("menu_family") == "map_inspect":
        _map_inspect(obs)
        direction = ui["map_inspection"]["direction"]
        option = ui["options"][0]
        if option.get("activate") is None:
            option["activate"] = _binding(obs, direction, "activate:" + option["id"], MAP_INSPECT_RULE)
    else:
        _require(False, "no default control rule for this menu family")
    checked = _observation(obs, clock, max_age_seconds)
    for option in checked["ui"]["options"]:
        if option.get("activate") is not None:
            _proof(option["activate"], checked, "activate:" + option["id"])
    return checked
