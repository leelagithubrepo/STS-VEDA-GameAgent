"""Plan evidence collection from a ledger without operating or guessing the game.

``current`` means consistency with the ledger's logical epoch. It is not proof
of live capture, calibration, or controller permission. This module performs no
I/O and returns view requests, never button presses or executable actions.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

SECTIONS = ("player", "hand", "enemies", "statuses", "ui", "piles", "inventory")
COMBAT_SECTIONS = ("player", "hand", "enemies", "statuses", "ui", "inventory")
KINDS = frozenset(("combat_action", "play_card", "end_turn", "play_potion",
                   "choose_exhaust", "pile_selection", "long_fight_setup",
                   "deck_review", "inspect_state"))
PILE_EFFECTS = frozenset(("draw", "discard", "exhaust", "retrieve", "reshuffle", "topdeck", "draw_order"))
STATUSES = frozenset(("current", "partial", "missing", "stale", "conflict"))
PILE_ZONES = ("draw", "discard", "exhaust")
COMBAT_KINDS = frozenset(("combat_action", "play_card", "end_turn", "play_potion", "long_fight_setup"))


def _decision(value: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        value = {"kind": value}
    if not isinstance(value, Mapping) or set(value) - {"kind", "effects", "long_fight"}:
        raise ValueError("inspection_invalid_decision")
    kind = value.get("kind", "combat_action")
    effects = value.get("effects", [])
    long_fight = value.get("long_fight", False)
    if (not isinstance(kind, str) or kind not in KINDS or not isinstance(effects, (list, tuple)) or len(effects) > 32
            or any(not isinstance(effect, str) or not effect or len(effect) > 80 for effect in effects)
            or type(long_fight) is not bool):
        raise ValueError("inspection_invalid_decision")
    return {"kind": kind, "effects": sorted(set(effects)), "long_fight": long_fight}


def _has_evidence(section: Mapping[str, Any]) -> bool:
    evidence = section.get("evidence")
    return isinstance(evidence, list) and bool(evidence)


def _usable(section: Mapping[str, Any]) -> bool:
    return (section.get("current") is True and section.get("status") == "current"
            and section.get("complete") is True and isinstance(section.get("data"), Mapping)
            and _has_evidence(section))


def _inventory_missing(section: Mapping[str, Any], required: tuple[str, ...]) -> list[str]:
    # Full deck coverage is not required merely to consider current potions and
    # relic effects. Conversely, [] with unknown coverage never means empty.
    if (section.get("current") is not True or section.get("status") not in ("current", "partial")
            or not _has_evidence(section) or not isinstance(section.get("data"), Mapping)):
        return list(required)
    data = section["data"]
    coverage, contents = data.get("coverage", {}), data.get("current", {})
    if not isinstance(coverage, Mapping) or not isinstance(contents, Mapping):
        return list(required)
    return [category for category in required
            if coverage.get(category) != "complete" or not isinstance(contents.get(category), list)]


def _valid_frame(value: Any) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get("frame_id"), str) or not value["frame_id"]:
        return False
    digest = value.get("image_sha256")
    return isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdefABCDEF" for c in digest)


def _pile_missing(section: Mapping[str, Any], required: tuple[str, ...]) -> list[str]:
    if (section.get("current") is not True or section.get("status") not in ("current", "partial")
            or not _has_evidence(section) or not isinstance(section.get("data"), Mapping)):
        return list(required)
    data = section["data"]
    coverage, proof = data.get("coverage", {}), data.get("zone_evidence", {})
    if not isinstance(coverage, Mapping) or not isinstance(proof, Mapping):
        return list(required)
    return [zone for zone in required if coverage.get(zone) != "complete"
            or not isinstance(data.get(zone), list) or not isinstance(proof.get(zone), str)
            or not proof[zone].strip()]


def plan_inspections(
    ledger_snapshot: Mapping[str, Any], *,
    decision: str | Mapping[str, Any] = "combat_action", max_requests: int = 3,
) -> dict[str, Any]:
    """Return a bounded, deterministic plan for the missing decision evidence.

    Input is ``EvidenceLedger.snapshot()``. The ledger validates section data and
    completeness proofs; the planner checks availability and decision-specific
    dependencies. Partial inventory is sufficient only for categories explicitly
    complete in ``data.coverage``. UI null focus is valid when the ledger has
    complete evidence: null is not automatically an unknown field.

    Supported decision mappings contain ``kind``, ``effects`` and ``long_fight``.
    Pile-dependent effects and setup requests require piles. ``deck_review`` also
    requires the card inventory. Unknown effect names require a rule review;
    they are never silently accepted as a known, harmless effect.
    """
    if (not isinstance(ledger_snapshot, Mapping)
            or ledger_snapshot.get("schema") != "veda.evidence-ledger.v1"
            or not isinstance(ledger_snapshot.get("sections"), Mapping)
            or type(ledger_snapshot.get("epoch")) is not int or ledger_snapshot["epoch"] < 0
            or not isinstance(ledger_snapshot.get("context"), Mapping)):
        raise ValueError("inspection_invalid_ledger_snapshot")
    if type(max_requests) is not int or not 1 <= max_requests <= 8:
        raise ValueError("inspection_invalid_request_limit")
    selected = _decision(decision)
    effects = set(selected["effects"])
    # Effects outside this vocabulary need a rule dependency review, not an
    # invented screen request that could never establish the mechanic.
    known_effects = PILE_EFFECTS | {"attack", "block", "heal", "status", "power", "energy", "deck_change"}
    unknown_effects = sorted(effects - known_effects)
    pile_zones = set()
    if selected["kind"] in ("pile_selection", "long_fight_setup", "inspect_state") or selected["long_fight"]:
        pile_zones.update(PILE_ZONES)
    for effect, zones in {"draw": ("draw", "discard"), "reshuffle": ("draw", "discard"),
                          "discard": ("discard",), "retrieve": ("discard",), "exhaust": ("exhaust",),
                          "topdeck": ("draw",), "draw_order": ("draw",)}.items():
        if effect in effects:
            pile_zones.update(zones)
    pile_required = tuple(zone for zone in PILE_ZONES if zone in pile_zones)
    required = ["ui", "inventory"] if selected["kind"] == "deck_review" else list(COMBAT_SECTIONS)
    if pile_required:
        required.insert(required.index("inventory"), "piles")
    inventory_required = ("card", "relic", "potion") if selected["kind"] in ("deck_review", "inspect_state") or "deck_change" in effects else ("relic", "potion")
    sections = {name: value if isinstance(value, Mapping) else {}
                for name,value in ledger_snapshot["sections"].items()}
    gaps = []
    satisfied = []
    wrong_phase = False
    for name in required:
        section = sections.get(name, {})
        if not isinstance(section, Mapping):
            section = {}
        status = section.get("status", "missing")
        status = status if isinstance(status, str) and status in STATUSES else "missing"
        missing_categories = _inventory_missing(section, inventory_required) if name == "inventory" else []
        missing_zones = _pile_missing(section, pile_required) if name == "piles" else []
        usable = (not missing_categories if name == "inventory" else
                  not missing_zones if name == "piles" else _usable(section))
        if name == "ui" and not _valid_frame(ledger_snapshot.get("frame")):
            usable = False
            reason = "A source-bound current view is needed before other inspections can be interpreted."
        elif name == "ui" and usable and selected["kind"] in COMBAT_KINDS and section["data"].get("phase") != "combat":
            usable = False
            wrong_phase = True
            reason = "The confirmed screen is not the combat view required for this decision."
        elif status == "stale":
            reason = "Earlier evidence was invalidated; it cannot describe the present decision."
        elif status == "conflict":
            reason = "Conflicting evidence must be resolved from a new source-bound inspection."
        else:
            reason = "The required evidence is missing, partial, or lacks a completeness proof."
        if usable:
            satisfied.append(name)
            continue
        invalidated = section.get("invalidated_by", [])
        gaps.append({"section": name, "status": status, "reason": reason,
                     "invalidated_by": deepcopy(invalidated) if isinstance(invalidated, list) else [],
                     "inventory_categories": missing_categories, "pile_zones": missing_zones})

    epoch = ledger_snapshot["epoch"]
    frame = ledger_snapshot.get("frame")
    frame_id = frame.get("frame_id", "unbound") if isinstance(frame, Mapping) else "unbound"
    gap_names = {gap["section"] for gap in gaps}
    requests = []

    def request(view, names, priority, why, needed, *, interactive=False, limitation=None):
        requests.append({"request_id": f"{epoch}:{frame_id}:{view}:{','.join(names)}",
                         "view": view, "sections": names, "priority": priority,
                         "why_decision_is_blocked": why, "evidence_needed": needed,
                         "depends_on": [], "requires_interactive_view": interactive,
                         "refresh_after_inspection": ["ui"] if interactive else [],
                         "limitation": limitation, "controller_action": None})

    # Unknown phase/focus is a dependency gate. Do not ask for several incompatible
    # menus when it is not yet established what screen the user is viewing.
    if "ui" in gap_names:
        request("return_to_combat_and_focus" if wrong_phase else "current_screen_and_focus", ["ui"], 0,
                "The confirmed menu or overlay cannot establish a playable combat screen." if wrong_phase else
                "The screen phase and existing focus are not established.",
                ["An unobscured view with a source hash and current context.",
                 "Screen phase, open selection/menu, and observed card/target focus, including explicitly observed no focus."],
                interactive=wrong_phase,
                limitation="Do not treat a tooltip preview as a selected playable card or infer focus from card order.")
    else:
        overview = [name for name in ("player", "enemies") if name in gap_names]
        if overview:
            request("combat_overview", overview, 10,
                    "Player resources or the complete enemy roster and displayed attacks are unresolved.",
                    ["Player HP, energy and block; an absent or covered block badge is not zero.",
                     "Every visible enemy with stable identity/target mapping, HP/block and complete displayed intent.",
                     "Use an enemy intent tooltip when a numeral, hit multiplier or companion effect is unreadable."],
                    limitation="A subtotal of recognized attacks is not total incoming damage; a missing multiplier is not one hit.")
        if "hand" in gap_names:
            request("hand_and_focus_sequence", ["hand"], 20,
                    "The complete current hand and its confirmed order are needed for card choices.",
                    ["Enumerate every hand member with a stable ID and current title, upgrade, type, cost and playability.",
                     "Resolve covered headers with individual previews; retain continuity and confirm which hand member each preview belongs to.",
                     "Establish hand/focus order from evidence; previews and pile cards are not extra hand members."], interactive=True,
                    limitation="A count of OCR depictions, a sorted grid or visual left-to-right positions alone cannot certify controller order.")
        if "statuses" in gap_names:
            request("player_and_enemy_status_tooltips", ["statuses"], 20,
                    "Statuses, powers and counters can change costs, damage, block and end-turn effects.",
                    ["Player status names, stacks/durations, powers, counters and end-turn damage.",
                     "Status/power evidence for each confirmed enemy ID, including explicitly confirmed absence."], interactive=True,
                    limitation="Do not replace unexamined icons or effects with empty lists or zero stacks.")
        if "inventory" in gap_names:
            categories = next(gap["inventory_categories"] for gap in gaps if gap["section"] == "inventory")
            request("inventory_tooltips", ["inventory"], 15 if selected["kind"] == "play_potion" else 25,
                    "Available potions and relic effects must be considered before committing to a line.",
                    ["Confirm every occupied and empty potion slot, potion names and relevant tooltip effects." if "potion" in categories else "Preserve already complete potion evidence.",
                     "Inspect each unresolved relic name and relevant effect/counter, including overflow or hidden entries." if "relic" in categories else "Preserve already complete relic evidence."]
                    + (["Confirm the full current deck inventory and upgrades; do not use the combat hand as the deck."] if "card" in categories else []),
                    interactive=True, limitation="Only incomplete categories need inspection. Previous inventory is invalid after an unlogged input or a declared inventory change.")
            requests[-1]["inventory_categories"] = categories
        if "piles" in gap_names:
            missing_zones = next(gap["pile_zones"] for gap in gaps if gap["section"] == "piles")
            request("pile_pages_and_continuity", ["piles"], 15 if selected["long_fight"] or selected["kind"] == "long_fight_setup" else 30,
                    "The requested setup or card effect depends on cards outside the hand.",
                    ["Identify these unresolved pile views explicitly and inspect their relevant pages without an intervening unlogged action: " + ", ".join(missing_zones) + ".",
                     "Record card identities/upgrades and which pile each belongs to; reconcile page coverage and counts before claiming completeness.",
                     "For long-fight setup, establish whether required setup cards remain available before recommending effects that can remove future options."],
                    interactive=True, limitation="Draw-pile grids may be sorted by rarity, not draw order. Only an explicit ordered view or a verified placement event with continuity can establish a known top card.")
            requests[-1]["pile_zones"] = missing_zones
            if effects & {"draw_order", "topdeck"}:
                requests[-1]["evidence_needed"].append("Also establish the explicitly required draw order from ordered evidence; a sorted browser cannot satisfy this requirement.")
    if unknown_effects:
        request("rule_dependency_review", [], 5,
                "The declared effect is not covered by this inspection dependency contract.",
                ["Resolve the rule and its required state sections for: " + ", ".join(unknown_effects)],
                limitation="A screenshot cannot by itself validate an unsupported game mechanic.")
    if "draw_order" in effects or "topdeck" in effects:
        # Pile completeness does not imply ordered draw knowledge. This extra
        # request is suppressed only by an explicit current ordered pile field.
        piles = sections.get("piles", {})
        data = piles.get("data", {}) if isinstance(piles, Mapping) else {}
        if "ui" not in gap_names and "piles" not in gap_names and not (not _pile_missing(piles, ("draw",)) and isinstance(data.get("draw_order"), list)):
            request("ordered_draw_evidence", ["piles"], 15,
                    "The decision depends on the next draw order, which an unordered pile grid cannot establish.",
                    ["An explicitly ordered draw view, or a verified top-card placement event tied to the current source/context with no intervening draw or shuffle."],
                    limitation="Do not guess ordering from the pile browser, deck list, prior turns or card names.")
    requests.sort(key=lambda item: (item["priority"], item["view"]))
    return {"schema": "veda.inspection-plan.v1", "context": deepcopy(dict(ledger_snapshot["context"])),
            "epoch": epoch, "frame": deepcopy(frame), "decision": selected,
            "required_sections": required, "required_inventory_categories": list(inventory_required),
            "required_pile_zones": list(pile_required),
            "satisfied_requirements": satisfied, "gaps": gaps,
            "decision_blocked": bool(gaps or requests), "requests": requests[:max_requests],
            "deferred_requests": requests[max_requests:],
            "planning_scope": "Logical current ledger evidence only; no live freshness, recognition calibration or action safety is established.",
            "runtime_authorized": False, "controller_authorized": False}
