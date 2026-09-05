"""Research intake that preserves source-level provenance and disagreement."""

from __future__ import annotations

from dataclasses import dataclass

from .knowledge import Claim, ClaimKind, KnowledgeBase, Source


@dataclass(frozen=True)
class ResearchNote:
    """A human- or tool-produced reading note before it becomes a claim.

    `verbatim_excerpt` is deliberately distinct from `interpretation`: VEDA can
    later inspect whether a conclusion was directly stated or inferred.
    """
    source: Source
    verbatim_excerpt: str
    interpretation: str
    tags: frozenset[str]
    conditions: str | None = None


class ResearchIntake:
    def __init__(self, knowledge: KnowledgeBase) -> None:
        self.knowledge = knowledge
        self._notes: list[ResearchNote] = []

    def capture(self, note: ResearchNote) -> None:
        if not note.verbatim_excerpt.strip() or not note.interpretation.strip():
            raise ValueError("research notes require both excerpt and interpretation")
        self._notes.append(note)

    def publish_claim(self, statement: str, kind: ClaimKind, notes: list[ResearchNote], *, rationale: str | None = None, confidence: float = 0.5) -> Claim:
        """Publish a claim only from explicitly selected notes.

        This makes corroboration reviewable and prevents a scraper's output from
        becoming trusted knowledge merely because it was retrieved.
        """
        if not notes:
            raise ValueError("select one or more research notes as evidence")
        if any(note not in self._notes for note in notes):
            raise ValueError("all evidence notes must be captured first")
        return self.knowledge.add(Claim(
            statement=statement,
            kind=kind,
            sources=tuple(note.source for note in notes),
            tags=frozenset().union(*(note.tags for note in notes)),
            conditions="; ".join(n.conditions for n in notes if n.conditions) or None,
            rationale=rationale,
            confidence=confidence,
        ))
