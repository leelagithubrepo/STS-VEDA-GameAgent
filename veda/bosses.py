"""Verified boss identity and move-profile boundary.

This is deliberately an identity guard, not a strategy engine.  It prevents a
name inferred from artwork or a remembered pattern from being treated as fact.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BossProfile:
    name: str
    base_hp: int
    opening: tuple[str, ...]
    repeating_pattern: tuple[str, ...]


HEXAGHOST = BossProfile(
    "Hexaghost", 250,
    ("Activate", "Divider"),
    ("Sear", "Tackle", "Sear", "Inflame", "Tackle", "Sear", "Inferno"),
)

ACT_ONE_BOSSES = frozenset({"Hexaghost", "Slime Boss", "The Guardian"})


def confirm_boss_identity(*, visible_name: str | None, expected_name: str | None = None) -> tuple[bool, str]:
    """Return a fail-closed identity decision based on readable name text."""
    if visible_name is None or visible_name.strip() not in ACT_ONE_BOSSES:
        return False, "boss name is not legibly confirmed from the current screen"
    if expected_name is not None and visible_name.strip() != expected_name:
        return False, f"visible boss is {visible_name.strip()}, not expected {expected_name}"
    return True, visible_name.strip()
