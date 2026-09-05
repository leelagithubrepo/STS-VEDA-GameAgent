"""Turn verified visual combat facts into the arithmetic snapshot VEDA uses."""

from __future__ import annotations

from dataclasses import dataclass

from .combat import CombatEnemy, CombatSnapshot
from .vision import StructuredGameState, combat_action_readiness


@dataclass(frozen=True)
class CombatStateVerification:
    ready: bool
    reasons: tuple[str, ...]
    snapshot: CombatSnapshot | None = None


def verify_combat_state(state: StructuredGameState) -> CombatStateVerification:
    """Fail closed unless all arithmetic-critical screen facts are confirmed."""
    readiness = combat_action_readiness(state)
    if not readiness.ready:
        return CombatStateVerification(False, readiness.reasons)
    snapshot = CombatSnapshot(
        energy=state.energy,
        player_hp=state.hp,
        player_weak=state.player_weak or 0,
        player_frail=state.player_frail or 0,
        player_block=state.block or 0,
        incoming_damage=sum(enemy.intent_total_damage or 0 for enemy in state.enemies),
        end_turn_damage=state.end_turn_damage,
        hand_size=len(state.hand),
        enemies=tuple(
            CombatEnemy(enemy.name, enemy.hp or 0, block=enemy.block or 0)
            for enemy in state.enemies
        ),
    )
    return CombatStateVerification(True, (), snapshot)
