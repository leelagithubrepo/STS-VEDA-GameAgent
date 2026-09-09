"""Human-executed teaching mode for VEDA's gradual autonomy path.

This module deliberately has no controller, PS5, or GUI dependency.  It turns
an approved recommendation into a clear hand-off for a human, then verifies
the observed result and stores the experience.
"""

from __future__ import annotations

from dataclasses import dataclass

from .combat import CardEffect, CombatSnapshot
from .controller_verification import ControllerAttempt, verify_controller_attempt
from .decision_protocol import DecisionBrief, build_decision_brief
from .experience import DecisionRecord, ExperienceStore, evaluate_prediction
from .preflight import RecommendationPreflight, preflight_combat, verify_recommendation
from .run_ledger import RunLedger
from .vision import StructuredGameState


@dataclass(frozen=True)
class HumanGuidedRecommendation:
    preflight: RecommendationPreflight
    cards: tuple[CardEffect, ...]
    reasoning: str
    state_before: StructuredGameState
    brief: DecisionBrief
    safe_alternative: tuple[CardEffect, ...] = ()

    @property
    def instructions(self) -> tuple[str, ...]:
        return tuple(
            f"Play {card.name}" + (f" targeting {card.target}" if card.target else "")
            for card in self.cards
        )

    @property
    def safe_instructions(self) -> tuple[str, ...]:
        return tuple(
            f"Play {card.name}" + (f" targeting {card.target}" if card.target else "")
            for card in self.safe_alternative
        )


class HumanGuidedSession:
    """One-decision-at-a-time coordinator with human-only execution authority."""

    def __init__(self, experience: ExperienceStore, ledger: RunLedger | None = None) -> None:
        self.experience = experience
        self.ledger = ledger
        self._pending: HumanGuidedRecommendation | None = None

    def recommend(
        self,
        observation: StructuredGameState,
        snapshot: CombatSnapshot,
        cards: tuple[CardEffect, ...],
        reasoning: str,
        safe_alternative: tuple[CardEffect, ...] = (),
    ) -> HumanGuidedRecommendation:
        if self._pending is not None:
            raise RuntimeError("verify or abandon the pending recommendation before creating another")
        primary = preflight_combat(observation, snapshot, cards)
        brief = build_decision_brief(observation, snapshot, self.ledger)
        alternative = preflight_combat(observation, snapshot, safe_alternative) if safe_alternative else None
        if alternative is not None and not alternative.allowed:
            primary = RecommendationPreflight(
                False,
                ("safe alternative failed preflight", *alternative.reasons),
                "No action prediction: safe alternative is invalid.",
            )
        recommendation = HumanGuidedRecommendation(primary, cards, reasoning, observation, brief, safe_alternative)
        if recommendation.preflight.allowed:
            if self.ledger is not None:
                self.ledger.confirm_snapshot(hp=observation.hp, max_hp=observation.max_hp, gold=observation.gold)
                self.ledger.confirm_location(act=observation.act, floor=observation.floor)
                self.ledger.confirm_combat_snapshot(
                    energy=observation.energy, hand=observation.hand, source="verified teaching-mode observation",
                )
            self._pending = recommendation
        return recommendation

    def verify(self, after: StructuredGameState) -> DecisionRecord:
        if self._pending is None:
            raise RuntimeError("there is no approved recommendation to verify")
        pending = self._pending
        before = pending.state_before.as_observation()
        after_observation = after.as_observation()
        verification = verify_recommendation(before, after_observation)
        controller = verify_controller_attempt(
            ControllerAttempt(
                action="; ".join(card.name for card in pending.cards),
                expected=pending.preflight.predicted_state or {}, before=before,
            ), after_observation,
        )
        actions = tuple({"card": card.name, "target": card.target, "cost": card.cost} for card in pending.cards)
        record = DecisionRecord(
            state_before=before,
            legal_actions=actions,
            selected_action={"human_executed": list(actions)},
            reasoning=pending.reasoning,
            prediction=pending.preflight.prediction,
            state_after=after_observation,
            immediate_outcome=(
                f"controller {controller.status}; verified changes: " + ", ".join(change.field for change in verification.changes)
                if verification.changes else f"controller {controller.status}; no tracked state change was visible"
            ),
            predicted_state=pending.preflight.predicted_state,
            prediction_evaluation=evaluate_prediction(pending.preflight.predicted_state, after_observation),
            decision_brief=pending.brief.as_dict(),
        )
        self.experience.append(record)
        self._pending = None
        return record

    def abandon(self, reason: str) -> None:
        """Clear stale guidance without recording it as a completed decision."""
        if not reason.strip():
            raise ValueError("an abandonment reason is required")
        self._pending = None
