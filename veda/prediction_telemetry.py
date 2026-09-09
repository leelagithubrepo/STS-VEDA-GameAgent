"""Small, explicit accuracy telemetry for verified VEDA predictions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .experience import DecisionRecord


# Version-1 weights: HP affects survival most; energy and Block verify turn
# arithmetic; named enemies check damage forecasts.
PREDICTION_WEIGHTS = {"energy": 1.0, "block": 1.0, "player_hp": 2.0, "enemies": 1.0}


@dataclass(frozen=True)
class PredictionTelemetry:
    scored_decisions: int
    fully_matched_decisions: int
    weighted_correct: float
    weighted_total: float
    score: float | None
    coverage: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "veda.prediction-telemetry.v1",
            "parameters": {"weights": PREDICTION_WEIGHTS, "exact_match_only": True},
            "scored_decisions": self.scored_decisions,
            "fully_matched_decisions": self.fully_matched_decisions,
            "weighted_correct": self.weighted_correct,
            "weighted_total": self.weighted_total,
            "score": self.score,
            "coverage": self.coverage,
        }


def summarize_prediction_telemetry(records: Iterable[DecisionRecord]) -> PredictionTelemetry:
    """Return a 0--100 score over explicitly predicted, observed fields only."""
    all_records = list(records)
    scored = full = 0
    correct = total = 0.0
    for record in all_records:
        checked = (record.prediction_evaluation or {}).get("checked", {})
        if not checked:
            continue
        scored += 1
        if all(checked.values()):
            full += 1
        for field, matched in checked.items():
            weight = PREDICTION_WEIGHTS.get(field)
            if weight is not None:
                total += weight
                if matched:
                    correct += weight
    return PredictionTelemetry(scored, full, correct, total, round(100 * correct / total, 1) if total else None,
                               round(scored / len(all_records), 3) if all_records else None)
