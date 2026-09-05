"""Human-executed teaching mode for VEDA's gradual autonomy path.

This module deliberately has no controller, PS5, or GUI dependency.  It turns
an approved recommendation into a clear hand-off for a human, then verifies
the observed result and stores the experience.
"""

from __future__ import annotations

from dataclasses import dataclass

from .combat import CardEffect, CombatSnapshot
from .experience import DecisionRecord, ExperienceStore, evaluate_prediction
from .preflight import RecommendationPreflight, preflight_combat, verify_recommendation
from .vision import StructuredGameState


@dataclass(frozen=True)
class HumanGuidedRecommendation:
    preflight: RecommendationPreflight
    cards: tuple[CardEffect, ...]
    reasoning: str
    state_before: StructuredGameState

    @property
    def instructions(self) -> tuple[str, ...]:
        return tuple(
            f"Play {card.name}" + (f" targeting {card.target}" if card.target else "")
            for card in self.cards
        )


class HumanGuidedSession:
    """One-decision-at-a-time coordinator with human-only execution authority."""

    def __init__(self, experience: ExperienceStore) -> None:
        self.experience = experience
        self._pending: HumanGuidedRecommendation | None = None

    def recommend(
        self,
        observation: StructuredGameState,
        snapshot: CombatSnapshot,
        cards: tuple[CardEffect, ...],
        reasoning: str,
    ) -> HumanGuidedRecommendation:
        if self._pending is not None:
            raise RuntimeError("verify or abandon the pending recommendation before creating another")
        recommendation = HumanGuidedRecommendation(
            preflight_combat(observation, snapshot, cards), cards, reasoning, observation,
        )
        if recommendation.preflight.allowed:
            self._pending = recommendation
        return recommendation

    def verify(self, after: StructuredGameState) -> DecisionRecord:
        if self._pending is None:
            raise RuntimeError("there is no approved recommendation to verify")
        pending = self._pending
        before = pending.state_before.as_observation()
        after_observation = after.as_observation()
        verification = verify_recommendation(before, after_observation)
        actions = tuple({"card": card.name, "target": card.target, "cost": card.cost} for card in pending.cards)
        record = DecisionRecord(
            state_before=before,
            legal_actions=actions,
            selected_action={"human_executed": list(actions)},
            reasoning=pending.reasoning,
            prediction=pending.preflight.prediction,
            state_after=after_observation,
            immediate_outcome=(
                "verified changes: " + ", ".join(change.field for change in verification.changes)
                if verification.changes else "no tracked state change was visible"
            ),
            predicted_state=pending.preflight.predicted_state,
            prediction_evaluation=evaluate_prediction(pending.preflight.predicted_state, after_observation),
        )
        self.experience.append(record)
        self._pending = None
        return record

    def abandon(self, reason: str) -> None:
        """Clear stale guidance without recording it as a completed decision."""
        if not reason.strip():
            raise ValueError("an abandonment reason is required")
        self._pending = None
