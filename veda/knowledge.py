"""Attributed knowledge storage; claims are evidence, not imperative rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


class ClaimKind(StrEnum):
    FACT = "game_fact"
    PRINCIPLE = "strategic_principle"
    SITUATIONAL = "situational_strategy"
    HYPOTHESIS = "hypothesis"


@dataclass(frozen=True)
class Source:
    url: str
    publisher: str
    source_type: str
    reliability: float
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    excerpt: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.reliability <= 1:
            raise ValueError("reliability must be between 0 and 1")


@dataclass(frozen=True)
class Claim:
    statement: str
    kind: ClaimKind
    sources: tuple[Source, ...]
    tags: frozenset[str] = frozenset()
    conditions: str | None = None
    rationale: str | None = None
    confidence: float = 0.5
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("claim statement cannot be blank")
        if not self.sources:
            raise ValueError("a claim requires at least one source")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


class KnowledgeBase:
    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}

    def add(self, claim: Claim) -> Claim:
        if claim.id in self._claims:
            raise ValueError(f"duplicate claim id: {claim.id}")
        self._claims[claim.id] = claim
        return claim

    def retrieve(self, tags: set[str]) -> list[Claim]:
        """Return all relevant claims, including disagreement, by confidence."""
        return sorted(
            (claim for claim in self._claims.values() if claim.tags & tags),
            key=lambda claim: claim.confidence,
            reverse=True,
        )
