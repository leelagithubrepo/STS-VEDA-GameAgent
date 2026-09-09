"""Inventory-first safety checks for potion replacement choices."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .run_ledger import RunLedger


@dataclass(frozen=True)
class PotionReplacementCheck:
    allowed: bool
    reasons: tuple[str, ...] = ()


def preflight_potion_replacement(ledger: RunLedger, *, discard: str, gain: str, capacity: int) -> PotionReplacementCheck:
    """Require named, confirmed slots before advising a discard."""
    reasons: list[str] = []
    if capacity <= 0:
        reasons.append("potion capacity must be positive")
    if not discard.strip() or not gain.strip():
        reasons.append("potion discard and gain must both be named")
    if Counter(ledger.potions)[discard] < 1:
        reasons.append(f"{discard} is not in the confirmed potion inventory")
    if len(ledger.potions) < capacity:
        reasons.append("a potion slot is open; no discard is required")
    if len(ledger.potions) > capacity:
        reasons.append("confirmed potion inventory exceeds capacity; re-read potion slots")
    return PotionReplacementCheck(not reasons, tuple(reasons))
