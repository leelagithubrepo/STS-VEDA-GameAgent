"""VEDA-owned, bounded planning for calibrated, familiar combat states."""

from __future__ import annotations

from dataclasses import dataclass

from .calibration import COMBAT_CRITICAL_FIELDS, CalibrationReport
from .combat import CardEffect, SequenceCheck, choose_verified_sequence
from .combat_state import verify_combat_state
from .encounters import check_encounter
from .vision import StructuredGameState


_STATUS_CARDS = frozenset({"Dazed", "Burn", "Slimed", "Wound", "Void"})


@dataclass(frozen=True)
class RoutinePlan:
    ready: bool
    reasons: tuple[str, ...]
    sequence: tuple[CardEffect, ...] = ()
    prediction: SequenceCheck | None = None

    @property
    def instructions(self) -> tuple[str, ...]:
        return tuple(f"Play {card.name}" + (f" targeting {card.target}" if card.target else "") for card in self.sequence)


def _effect_for(name: str, *, target: str | None, strength: int) -> CardEffect | None:
    """Exact direct effects only; special cards deliberately escalate."""
    effects = {
        "Strike": (1, "Attack", 6 + strength, 0, 0, 0),
        "Defend": (1, "Skill", 0, 5, 0, 0),
        "Bash": (2, "Attack", 8 + strength, 0, 0, 2),
        "Clothesline": (2, "Attack", 12 + strength, 0, 2, 0),
        "Twin Strike": (1, "Attack", 10 + 2 * strength, 0, 0, 0),
        "Shrug It Off": (1, "Skill", 0, 8, 0, 0),
        "Headbutt": (1, "Attack", 9 + strength, 0, 0, 0),
    }
    spec = effects.get(name)
    if spec is None:
        return None
    cost, card_type, damage, block, weak, vulnerable = spec
    return CardEffect(name, cost, card_type, attack_damage=damage, block=block, weak=weak, vulnerable=vulnerable, target=target)


def _candidate_sequences(hand: tuple[str, ...], enemy_names: tuple[str, ...], strength: int, energy: int) -> tuple[tuple[CardEffect, ...], ...]:
    options: list[tuple[int, CardEffect]] = []
    for index, name in enumerate(hand):
        if name in _STATUS_CARDS:
            continue
        if name in {"Defend", "Shrug It Off"}:
            effect = _effect_for(name, target=None, strength=strength)
            if effect:
                options.append((index, effect))
        else:
            for target in enemy_names:
                effect = _effect_for(name, target=target, strength=strength)
                if effect:
                    options.append((index, effect))
    sequences: set[tuple[CardEffect, ...]] = set()

    def visit(sequence: tuple[CardEffect, ...], used: frozenset[int], remaining: int) -> None:
        if sequence:
            sequences.add(sequence)
        if len(sequence) >= 3:
            return
        for index, effect in options:
            if index not in used and effect.cost <= remaining:
                visit((*sequence, effect), used | {index}, remaining - effect.cost)

    visit((), frozenset(), energy)
    return tuple(sequences)


def plan_routine_combat(state: StructuredGameState, calibration: CalibrationReport, *, encounter_name: str | None) -> RoutinePlan:
    """Choose a safe direct-card line, or make the reason for escalation explicit."""
    reasons: list[str] = []
    if not calibration.authorized_for(COMBAT_CRITICAL_FIELDS):
        reasons.append("local vision has not earned combat-planning authorization")
    verified = verify_combat_state(state)
    reasons.extend(verified.reasons)
    profile = check_encounter(encounter_name, act=state.act)
    if not profile.known:
        reasons.extend(profile.cautions)
    elif profile.profile and profile.profile.name == "Gremlin Nob":
        reasons.append("Gremlin Nob requires its Enrage-aware planner; routine planner defers")
    if state.player_strength is None or state.player_weak is None or state.player_frail is None:
        reasons.append("player Strength, Weak, or Frail is unconfirmed")
    unmodeled = sorted({card for card in state.hand if card not in _STATUS_CARDS and _effect_for(card, target=None, strength=state.player_strength or 0) is None})
    if unmodeled:
        reasons.append("hand contains unmodeled cards: " + ", ".join(unmodeled))
    if reasons or verified.snapshot is None:
        return RoutinePlan(False, tuple(dict.fromkeys(reasons)))
    sequences = _candidate_sequences(state.hand, tuple(enemy.name for enemy in verified.snapshot.enemies), state.player_strength, verified.snapshot.energy or 0)
    prediction = choose_verified_sequence(verified.snapshot, sequences)
    if prediction is None:
        return RoutinePlan(False, ("no non-lethal modeled sequence is available",))
    selected = next(sequence for sequence in sequences if choose_verified_sequence(verified.snapshot, (sequence,)) == prediction)
    return RoutinePlan(True, (), selected, prediction)
