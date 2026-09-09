"""Human-controller intent tracking with observation-backed confirmation.

This module deliberately does not send controller input. In teaching mode a
button press is only an attempt until a newer game observation proves the
expected state change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ControllerAttempt:
    """A human's intended input and the observable result VEDA expects."""

    action: str
    expected: dict[str, Any]
    before: dict[str, Any]


@dataclass(frozen=True)
class ControllerVerification:
    status: str
    matched: tuple[str, ...]
    mismatched: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


def verify_controller_attempt(attempt: ControllerAttempt, after: dict[str, Any]) -> ControllerVerification:
    """Classify an attempted input without inferring a result from the input."""
    matched: list[str] = []
    mismatched: list[str] = []
    missing: list[str] = []
    for field, expected in attempt.expected.items():
        if expected is None:
            continue
        observed_field = "hp" if field == "player_hp" else field
        if observed_field not in after or after[observed_field] is None:
            missing.append(field)
        elif _normalized_value(field, after[observed_field]) == expected:
            matched.append(field)
        else:
            mismatched.append(field)
    if missing:
        status = "needs_fresh_observation"
    elif mismatched:
        status = "unexpected_transition"
    elif matched:
        status = "confirmed"
    else:
        status = "needs_explicit_expectation"
    return ControllerVerification(status, tuple(matched), tuple(mismatched), tuple(missing))


def _normalized_value(field: str, value: Any) -> Any:
    """Normalize the serialised enemy list used by StructuredGameState."""
    if field == "enemies" and isinstance(value, (tuple, list)):
        return {
            enemy.get("name"): enemy.get("hp") for enemy in value
            if isinstance(enemy, dict) and enemy.get("name") is not None
        }
    return value
