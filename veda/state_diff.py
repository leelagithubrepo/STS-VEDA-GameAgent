"""Verification utilities for comparing two structured observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: Any
    after: Any


@dataclass(frozen=True)
class StateVerification:
    changes: tuple[FieldChange, ...]
    missing_after: tuple[str, ...]

    def confirms(self, field: str, value: Any) -> bool:
        return any(change.field == field and change.after == value for change in self.changes)


TRACKED_FIELDS = ("screen_type", "hp", "max_hp", "energy", "block", "gold", "hand", "enemies", "selected_item")


def verify_transition(before: dict[str, Any], after: dict[str, Any]) -> StateVerification:
    changes = []
    missing = []
    for field in TRACKED_FIELDS:
        if field not in after:
            missing.append(field)
        elif before.get(field) != after[field]:
            changes.append(FieldChange(field, before.get(field), after[field]))
    return StateVerification(tuple(changes), tuple(missing))
