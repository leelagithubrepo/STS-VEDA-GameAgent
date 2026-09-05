"""Map and encounter-transition confidence gates independent of vision."""

from __future__ import annotations

from dataclasses import dataclass


NODE_KINDS = frozenset({"enemy", "elite", "event", "merchant", "treasure", "rest", "boss"})


@dataclass(frozen=True)
class MapNode:
    node_id: str
    kind: str
    reachable: bool
    confidence: float

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(f"unsupported map node kind: {self.kind}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("map-node confidence must be between 0 and 1")


@dataclass(frozen=True)
class MapAssessment:
    ready: bool
    reasons: tuple[str, ...]
    reachable: tuple[MapNode, ...]
    boss_confirmed: bool


@dataclass(frozen=True)
class EncounterAssessment:
    """Checks the room we entered against the node selected on the map.

    A map icon is a route fact, not proof of the current encounter.  This
    boundary prevents downstream logic from calling a normal combat an Elite
    merely because an earlier map interpretation was wrong.
    """

    ready: bool
    reasons: tuple[str, ...]
    expected_kind: str | None = None
    observed_kind: str | None = None


def assess_map(*, boss: str | None, boss_confidence: float, nodes: tuple[MapNode, ...]) -> MapAssessment:
    reasons: list[str] = []
    reachable = tuple(node for node in nodes if node.reachable)
    if not reachable:
        reasons.append("no reachable map node is confirmed")
    if any(node.confidence < 0.85 for node in reachable):
        reasons.append("a reachable node type is below the confidence threshold")
    boss_confirmed = bool(boss and boss_confidence >= 0.85)
    return MapAssessment(not reasons, tuple(reasons), reachable, boss_confirmed)


def assess_encounter_transition(
    *, selected_node: MapNode | None, observed_kind: str | None, observed_confidence: float,
) -> EncounterAssessment:
    """Authorize encounter-specific advice only after two independent facts agree."""
    reasons: list[str] = []
    expected = selected_node.kind if selected_node else None
    if selected_node is None or not selected_node.reachable or selected_node.confidence < 0.85:
        reasons.append("the selected map node is not confidently confirmed")
    if observed_kind not in {"enemy", "elite", "boss"} or observed_confidence < 0.9:
        reasons.append("the entered encounter type is not confidently confirmed")
    if expected and observed_kind and expected != observed_kind:
        reasons.append(f"map selected {expected}, but current encounter is {observed_kind}")
    return EncounterAssessment(not reasons, tuple(reasons), expected, observed_kind)
