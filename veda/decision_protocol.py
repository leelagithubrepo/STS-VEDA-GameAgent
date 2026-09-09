"""Evidence-first decision packets for human-guided play.

The LLM may propose a tactical idea, but this module makes the information
needed to evaluate that idea explicit and inspectable. It intentionally does
not choose cards or operate a controller.
"""

from __future__ import annotations

from dataclasses import dataclass

from .combat import CombatSnapshot
from .run_ledger import RunLedger
from .vision import StructuredGameState


@dataclass(frozen=True)
class DecisionBrief:
    """What is proven on the current frame, and what remains unknown."""

    verified: tuple[str, ...]
    unknowns: tuple[str, ...]
    immediate_threat: str
    ledger_context: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "verified": list(self.verified),
            "unknowns": list(self.unknowns),
            "immediate_threat": self.immediate_threat,
            "ledger_context": list(self.ledger_context),
        }


def build_decision_brief(
    observation: StructuredGameState,
    snapshot: CombatSnapshot,
    ledger: RunLedger | None = None,
) -> DecisionBrief:
    """Create the mandatory board read before an LLM recommendation.

    Nullable fields are reported as unknown rather than being filled from the
    previous turn. The combat preflight remains the authorization gate; this
    brief gives the human and LLM the evidence to audit it.
    """
    verified: list[str] = []
    unknowns: list[str] = []

    def fact(label: str, value: object | None) -> None:
        if value is None:
            unknowns.append(label)
        else:
            verified.append(f"{label}: {value}")

    fact("screen", observation.screen_type)
    fact("player HP", observation.hp)
    fact("player max HP", observation.max_hp)
    fact("energy", observation.energy)
    fact("Block", observation.block)
    fact("player Strength", observation.player_strength)
    fact("player Weak", observation.player_weak)
    fact("player Frail", observation.player_frail)
    fact("end-of-turn damage", observation.end_turn_damage)
    if observation.hand:
        verified.append("hand: " + ", ".join(observation.hand))
    else:
        unknowns.append("playable hand")

    enemy_lines: list[str] = []
    incoming = 0
    for enemy in observation.enemies:
        missing = []
        if enemy.hp is None:
            missing.append("HP")
        if enemy.block is None:
            missing.append("Block")
        if enemy.intent_total_damage is None:
            missing.append("intent total")
        if missing:
            unknowns.append(f"{enemy.name} " + "/".join(missing))
            continue
        incoming += enemy.intent_total_damage
        enemy_lines.append(f"{enemy.name}: {enemy.hp} HP, {enemy.block} Block, {enemy.intent_total_damage} incoming")
    if enemy_lines:
        verified.extend(enemy_lines)
    else:
        unknowns.append("enemy board")

    if snapshot.incoming_damage is None and len(enemy_lines) == len(observation.enemies):
        # The brief can state the visible aggregate even when the caller has
        # not yet copied it into the arithmetic snapshot. Preflight still
        # requires the snapshot for a full survival forecast.
        immediate_threat = f"confirmed incoming damage: {incoming}"
    elif snapshot.incoming_damage is None:
        immediate_threat = "incoming damage is unverified"
    else:
        immediate_threat = f"confirmed incoming damage: {snapshot.incoming_damage}"
        if snapshot.incoming_damage != incoming:
            unknowns.append("incoming damage disagrees with observed enemies")

    ledger_context: list[str] = []
    if ledger is not None:
        if ledger.relics:
            ledger_context.append("confirmed relics: " + ", ".join(ledger.relics))
        if ledger.potions:
            ledger_context.append("confirmed potions: " + ", ".join(ledger.potions))
        if ledger.pending:
            ledger_context.append("unconfirmed ledger items: " + ", ".join(event.value for event in ledger.pending))

    return DecisionBrief(
        tuple(dict.fromkeys(verified)), tuple(dict.fromkeys(unknowns)), immediate_threat, tuple(ledger_context),
    )
