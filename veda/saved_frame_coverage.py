"""Explain partial saved-image evidence without inventing a playable hand.

Counts below describe OCR depictions. They are never a count of cards in hand,
nor does screen position establish controller order or selected-card identity.
"""
from __future__ import annotations


def describe_coverage(*, hud, cards, combat):
    candidates = cards.get("card_candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 20:
        raise ValueError("coverage_invalid_card_candidates")
    if cards.get("hand_complete") is not None and cards.get("hand_complete") is not False:
        raise ValueError("unsupported_hand_completeness_claim")
    # This description may never turn partial numeric evidence into action
    # eligibility, even if a future extractor accidentally adds a ready flag.
    if combat is not None:
        if (combat.get("incoming_damage") is not None
                or combat.get("hand_complete") is not None
                or combat.get("enemy_count") is not None
                or any(combat.get(key) is not None and combat[key] is not False for key in
                       ("runtime_authorized", "controller_authorized", "runtime_authorization_eligible",
                        "runtime_ready", "combat_ready", "hand_ready"))):
            raise ValueError("unsupported_combat_readiness_claim")
    intents = (combat or {}).get("intent_evidence", {})
    if (intents.get("incoming_damage") is not None
            or intents.get("enemy_count") is not None
            or any(intents.get(key) is not None and intents[key] is not False for key in
                   ("runtime_authorized", "controller_authorized", "runtime_ready", "combat_ready",
                    "intent_coverage_complete", "enemy_roster_complete", "runtime_authorization_eligible"))):
        raise ValueError("unsupported_intent_readiness_claim")
    lower = [c for c in candidates if c.get("location") == "hand_band"]
    popup = [c for c in candidates if c.get("location") == "popup_or_nonhand"]
    issues = ["complete_hand_not_established", "hand_membership_and_order_not_established"]
    if not lower:
        issues.append("no_lower_band_depictions_read_is_not_an_empty_hand")
    if popup or cards.get("popup_or_nonhand_candidate"):
        issues.append("popup_or_nonhand_depiction_present")
    if cards.get("menu_text_present"):
        issues.append("menu_text_present")
    unknown_names = sum(c.get("name") is None for c in lower)
    unknown_costs = sum(c.get("current_cost") is None for c in lower)
    if unknown_names:
        issues.append("unresolved_lower_band_titles")
    if unknown_costs:
        issues.append("unresolved_lower_band_costs")
    refinement = cards.get("refinement", {})
    for name in ("header", "title", "cost"):
        if refinement.get(name + "_region_budget_omissions"):
            issues.append(name + "_refinement_budget_exhausted")
    discovery = refinement.get("hand_discovery_evidence", {})
    if discovery.get("status") not in ("processed", "candidate_limit"):
        issues.append("wide_hand_discovery_unavailable")
    elif discovery.get("status") == "candidate_limit":
        issues.append("card_candidate_limit_reached")
    if discovery.get("detector_limits_reached"):
        issues.append("wide_hand_detector_left_unevaluated_evidence")
    return {
        "schema": "veda.saved-frame-coverage.v1",
        "hand": {
            "complete": None, "card_count": None, "ordered_hand": None,
            "candidate_depictions": len(candidates),
            "lower_band_depictions": len(lower), "popup_or_nonhand_depictions": len(popup),
            "named_lower_band_depictions": len(lower) - unknown_names,
            "costed_lower_band_depictions": len(lower) - unknown_costs,
            "issues": issues,
        },
        "read_fields": [field for field in ("hp", "max_hp", "energy", "energy_max")
                        if hud.get(field) is not None] +
                       (["player_block"] if combat is not None and combat.get("player_block") is not None else []),
        "enemy_health_candidate_count": len(combat.get("enemy_hp_candidates", [])) if combat else 0,
        "attack_candidate_count": len(intents.get("attack_candidates", [])),
        "unconfirmed_attack_candidate_count": len(intents.get("unconfirmed_attack_candidates", [])),
        "intent_coverage_complete": False,
        "combat_ready": False,
        "unresolved_requirements": ["complete_hand_and_order", "enemy_roster_and_target_order",
            "enemy_intents_and_hits", "player_and_enemy_statuses", "potions_and_relics",
            "draw_discard_exhaust_piles", "screen_phase_and_controller_focus", "fresh_live_evidence"],
        "runtime_authorized": False, "controller_authorized": False,
    }
