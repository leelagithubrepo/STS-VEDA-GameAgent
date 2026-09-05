"""Auditable decision experience, kept separate from sourced knowledge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class DecisionRecord:
    state_before: dict[str, Any]
    legal_actions: tuple[dict[str, Any], ...]
    selected_action: dict[str, Any]
    reasoning: str
    prediction: str
    state_after: dict[str, Any] | None = None
    immediate_outcome: str | None = None
    combat_outcome: str | None = None
    run_outcome: str | None = None
    predicted_state: dict[str, Any] | None = None
    prediction_evaluation: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, Any]:
        """Return an inspectable, JSON-safe representation of this experience."""
        return {
            "id": self.id,
            "recorded_at": self.recorded_at.isoformat(),
            "state_before": self.state_before,
            "legal_actions": list(self.legal_actions),
            "selected_action": self.selected_action,
            "reasoning": self.reasoning,
            "prediction": self.prediction,
            "state_after": self.state_after,
            "immediate_outcome": self.immediate_outcome,
            "combat_outcome": self.combat_outcome,
            "run_outcome": self.run_outcome,
            "predicted_state": self.predicted_state,
            "prediction_evaluation": self.prediction_evaluation,
        }


class ExperienceStore:
    def __init__(self) -> None:
        self._records: list[DecisionRecord] = []

    def append(self, record: DecisionRecord) -> None:
        self._records.append(record)

    def related(self, tags: set[str]) -> list[DecisionRecord]:
        return [r for r in self._records if tags & set(r.state_before.get("tags", []))]

    @property
    def records(self) -> tuple[DecisionRecord, ...]:
        return tuple(self._records)

    def export_json(self, path: Path, *, metadata: dict[str, Any] | None = None) -> None:
        """Write experience without promoting it to researched knowledge."""
        document = {
            "metadata": metadata or {},
            "records": [record.as_dict() for record in self._records],
        }
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def evaluate_prediction(predicted: dict[str, Any] | None, observed: dict[str, Any]) -> dict[str, Any] | None:
    """Compare only explicit predictions; never manufacture a learning rule."""
    if predicted is None:
        return None
    checked: dict[str, bool] = {}
    for field in ("energy", "block"):
        if predicted.get(field) is not None and observed.get(field) is not None:
            checked[field] = predicted[field] == observed[field]
    if predicted.get("player_hp") is not None and observed.get("hp") is not None:
        checked["player_hp"] = predicted["player_hp"] == observed["hp"]
    return {"checked": checked, "all_matched": all(checked.values()) if checked else None}
