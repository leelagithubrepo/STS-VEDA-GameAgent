"""Field-level authorization for local vision on real QuickTime frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


COMBAT_CRITICAL_FIELDS = frozenset({
    "screen_type", "hp", "max_hp", "energy", "block", "hand", "enemies",
    "end_turn_damage",
})
MAP_CRITICAL_FIELDS = frozenset({"screen_type", "map_nodes"})


@dataclass(frozen=True)
class LabeledFrame:
    frame_id: str
    expected: dict[str, Any]


@dataclass(frozen=True)
class CalibrationReport:
    field_accuracy: dict[str, float]
    total_frames: int

    def authorized_for(self, fields: frozenset[str], *, threshold: float = 0.95, minimum_frames: int = 12) -> bool:
        """Authorization must be earned separately for each action domain."""
        return self.total_frames >= minimum_frames and all(self.field_accuracy.get(field, 0.0) >= threshold for field in fields)


class StateProvider(Protocol):
    def observe(self, frame_id: str) -> dict[str, Any]: ...


def calibrate(provider: StateProvider, frames: tuple[LabeledFrame, ...]) -> CalibrationReport:
    counts: dict[str, list[int]] = {}
    for frame in frames:
        observed = provider.observe(frame.frame_id)
        for field, expected in frame.expected.items():
            matched, total = counts.setdefault(field, [0, 0])
            counts[field] = [matched + int(observed.get(field) == expected), total + 1]
    return CalibrationReport(
        {field: matched / total for field, (matched, total) in counts.items() if total}, len(frames),
    )
