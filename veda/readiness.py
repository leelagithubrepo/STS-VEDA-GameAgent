"""Transparent Act 1 threat-readiness assessment.

This does not choose a route or prescribe cards.  It tells the decision layer
which relevant capabilities have been observed and which facts still need to
be learned before making a higher-risk choice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThreatReadiness:
    observed_capabilities: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    cautions: tuple[str, ...]

    @property
    def ready_for_risk_assessment(self) -> bool:
        """True only when route-critical facts have been observed."""
        return not self.unresolved_questions


def assess_act1_readiness(
    *,
    deck: tuple[str, ...],
    hp: int | None,
    max_hp: int | None,
    potions: tuple[str, ...],
    boss: str | None,
    next_path_known: bool,
) -> ThreatReadiness:
    """Summarize known tools and information gaps without scoring a route.

    Card names are evidence from the visible deck, not a claim that any one
    card should be played in a particular combat.
    """
    cards = set(deck)
    capabilities: list[str] = []
    cautions: list[str] = []
    questions: list[str] = []

    if cards & {"Strike", "Bash", "Pommel Strike", "Headbutt"}:
        capabilities.append("front-loaded attack cards observed")
    if cards & {"Defend", "Shrug It Off", "Ghostly Armor"}:
        capabilities.append("block cards observed")
    if cards & {"Pommel Strike", "Shrug It Off"}:
        capabilities.append("card draw observed")
    if "Combust" in cards:
        capabilities.append("persistent multi-enemy damage observed")
    if "Shockwave" in cards:
        capabilities.append("area Weak and Vulnerable source observed")
        cautions.append("Shockwave is a Skill; Gremlin Nob's Enrage must be considered if that Elite is encountered")
    if "Ghostly Armor" in cards:
        cautions.append("Ghostly Armor is Ethereal and must be evaluated before ending a turn with it unplayed")
    if potions:
        capabilities.append("combat potion resource observed")

    if hp is None or max_hp is None:
        questions.append("current HP is not confirmed")
    if boss is None:
        questions.append("Act 1 boss identity is not confirmed from the map")
    if not next_path_known:
        questions.append("reachable route, campfire, shop, and Elite nodes are not confirmed")

    return ThreatReadiness(tuple(capabilities), tuple(questions), tuple(cautions))
