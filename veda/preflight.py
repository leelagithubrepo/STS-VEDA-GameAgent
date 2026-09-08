"""One mandatory, inspectable preflight before VEDA may recommend an action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .combat import CardEffect, CombatSnapshot, SequenceCheck, validate_and_predict
from .combat_state import CombatStateVerification, verify_combat_state
from .map_reader import MapAssessment, MapNode, assess_map
from .state_diff import StateVerification, verify_transition
from .vision import StructuredGameState, combat_action_readiness


@dataclass(frozen=True)
class RecommendationPreflight:
    allowed: bool
    reasons: tuple[str, ...]
    prediction: str
    combat: SequenceCheck | None = None
    map_assessment: MapAssessment | None = None
    predicted_state: dict[str, Any] | None = None


def preflight_combat(
    observation: StructuredGameState,
    snapshot: CombatSnapshot,
    sequence: tuple[CardEffect, ...],
) -> RecommendationPreflight:
    """Fail closed if either visual evidence or arithmetic is insufficient."""
    readiness = combat_action_readiness(observation)
    check = validate_and_predict(snapshot, sequence)
    # The observation is the authoritative hand, not a remembered deck list.
    # Preserve multiplicity: one visible Strike cannot support two plays.
    visible_hand = list(observation.hand)
    hand_reasons: list[str] = []
    for card in sequence:
        try:
            visible_hand.remove(card.name)
        except ValueError:
            hand_reasons.append(f"{card.name} is not in the observed hand")
    reasons = (*readiness.reasons, *check.reasons, *hand_reasons)
    if reasons:
        return RecommendationPreflight(False, tuple(dict.fromkeys(reasons)), "No action prediction: preflight failed.", check)
    enemies = ", ".join(f"{enemy.name}: {enemy.hp} HP" for enemy in check.enemies)
    prediction = (
        f"Spend {check.energy_spent} energy; {check.energy_remaining} remains. "
        f"Player Block after cards: {check.player_block}. Predicted enemies: {enemies}."
    )
    if check.projected_player_hp is not None:
        prediction += (
            f" Confirmed incoming: {check.incoming_damage}; confirmed end-of-turn damage: "
            f"{check.end_turn_damage}; projected HP: {check.projected_player_hp}."
        )
    predicted_state = {
        "energy": check.energy_remaining,
        "block": check.player_block,
        "player_hp": check.projected_player_hp,
        "enemies": {enemy.name: enemy.hp for enemy in check.enemies},
    }
    return RecommendationPreflight(True, (), prediction, check, predicted_state=predicted_state)


def preflight_verified_combat(
    observation: StructuredGameState,
    sequence: tuple[CardEffect, ...],
) -> tuple[RecommendationPreflight, CombatStateVerification]:
    """Use only vision-verified arithmetic inputs for a combat recommendation."""
    verified = verify_combat_state(observation)
    if not verified.ready or verified.snapshot is None:
        return RecommendationPreflight(False, verified.reasons, "No action prediction: combat state is unverified."), verified
    return preflight_combat(observation, verified.snapshot, sequence), verified


def preflight_map(*, boss: str | None, boss_confidence: float, nodes: tuple[MapNode, ...]) -> RecommendationPreflight:
    assessment = assess_map(boss=boss, boss_confidence=boss_confidence, nodes=nodes)
    if not assessment.ready:
        return RecommendationPreflight(False, assessment.reasons, "No route prediction: map preflight failed.", map_assessment=assessment)
    choices = ", ".join(f"{node.kind}:{node.node_id}" for node in assessment.reachable)
    return RecommendationPreflight(True, (), f"Confirmed reachable nodes: {choices}.", map_assessment=assessment)


def verify_recommendation(before: dict[str, Any], after: dict[str, Any]) -> StateVerification:
    """Mandatory result comparison after a human or future controller acts."""
    return verify_transition(before, after)
