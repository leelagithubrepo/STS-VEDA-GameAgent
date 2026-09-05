"""The game-agnostic observe → reason → act → verify orchestration loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .experience import DecisionRecord, ExperienceStore
from .knowledge import Claim, KnowledgeBase


@dataclass(frozen=True)
class Decision:
    action: dict[str, Any] | None
    reasoning: str
    prediction: str
    confidence: float


class GameAdapter(Protocol):
    def observe(self) -> dict[str, Any]: ...
    def legal_actions(self, state: dict[str, Any]) -> list[dict[str, Any]]: ...
    def execute(self, action: dict[str, Any]) -> None: ...


class DecisionPolicy(Protocol):
    def decide(
        self,
        state: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        knowledge: list[Claim],
        experience: list[DecisionRecord],
    ) -> Decision: ...


class NoopPolicy:
    """Safe initial policy: research and observe, but never presses a control."""
    def decide(self, state: dict[str, Any], legal_actions: list[dict[str, Any]], knowledge: list[Claim], experience: list[DecisionRecord]) -> Decision:
        return Decision(None, "No action policy is configured.", "No game state will change.", 1.0)


class AutonomousAgent:
    def __init__(self, adapter: GameAdapter, knowledge: KnowledgeBase, experience: ExperienceStore, policy: DecisionPolicy | None = None) -> None:
        self.adapter, self.knowledge, self.experience = adapter, knowledge, experience
        self.policy = policy or NoopPolicy()

    def step(self) -> DecisionRecord:
        state_before = self.adapter.observe()
        tags = set(state_before.get("tags", []))
        legal_actions = self.adapter.legal_actions(state_before)
        decision = self.policy.decide(
            state_before, legal_actions, self.knowledge.retrieve(tags), self.experience.related(tags)
        )
        if decision.action is None:
            record = DecisionRecord(state_before, tuple(legal_actions), {}, decision.reasoning, decision.prediction)
        else:
            if decision.action not in legal_actions:
                raise ValueError("policy selected an action not legal for the observed state")
            self.adapter.execute(decision.action)
            state_after = self.adapter.observe()
            record = DecisionRecord(
                state_before, tuple(legal_actions), decision.action, decision.reasoning,
                decision.prediction, state_after=state_after,
                immediate_outcome="state verified after action",
            )
        self.experience.append(record)
        return record
