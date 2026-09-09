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
    decision_brief: dict[str, Any] | None = None
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
            "decision_brief": self.decision_brief,
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "DecisionRecord":
        """Restore an exported record without turning it into new knowledge."""
        recorded_at = document.get("recorded_at")
        return cls(
            state_before=document["state_before"],
            legal_actions=tuple(document["legal_actions"]),
            selected_action=document["selected_action"],
            reasoning=document["reasoning"],
            prediction=document["prediction"],
            state_after=document.get("state_after"),
            immediate_outcome=document.get("immediate_outcome"),
            combat_outcome=document.get("combat_outcome"),
            run_outcome=document.get("run_outcome"),
            predicted_state=document.get("predicted_state"),
            prediction_evaluation=document.get("prediction_evaluation"),
            decision_brief=document.get("decision_brief"),
            id=document.get("id", str(uuid4())),
            recorded_at=datetime.fromisoformat(recorded_at) if recorded_at else datetime.now(timezone.utc),
        )


class ExperienceStore:
    """Auditable local experience store with optional write-through persistence.

    Persisting a verified record is deliberately different from learning a rule:
    it only saves evidence for later, repeated evaluation.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._records: list[DecisionRecord] = []
        if path is not None and path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            self._records = [DecisionRecord.from_dict(record) for record in document.get("records", [])]

    def append(self, record: DecisionRecord) -> None:
        self._records.append(record)
        if self.path is not None:
            self.flush()

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

    def flush(self) -> None:
        """Atomically save local evidence if this store was given a path."""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        self.export_json(
            temporary,
            metadata={
                "schema": "veda.experience.v1",
                "purpose": "observed decision evidence; not automatic strategic knowledge",
            },
        )
        temporary.replace(self.path)

    def prediction_telemetry(self) -> dict[str, Any]:
        """Return current forecast calibration without changing knowledge."""
        from .prediction_telemetry import summarize_prediction_telemetry
        return summarize_prediction_telemetry(self.records).as_dict()


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
    if predicted.get("enemies") is not None and observed.get("enemies") is not None:
        observed_enemies = observed["enemies"]
        if isinstance(observed_enemies, (tuple, list)):
            observed_enemies = {
                enemy.get("name"): enemy.get("hp") for enemy in observed_enemies
                if isinstance(enemy, dict) and enemy.get("name") is not None
            }
        if isinstance(predicted["enemies"], dict) and isinstance(observed_enemies, dict):
            # A combat-winning action commonly transitions to a reward screen,
            # where the observed enemy list is empty. Treat that as a match only
            # when every named enemy was explicitly predicted dead.
            checked["enemies"] = (
                predicted["enemies"] == observed_enemies
                or (not observed_enemies and all(hp == 0 for hp in predicted["enemies"].values()))
            )
    return {"checked": checked, "all_matched": all(checked.values()) if checked else None}
