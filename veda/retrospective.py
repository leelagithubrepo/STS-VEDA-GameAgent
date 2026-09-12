"""Stage-end retrospectives built from auditable VEDA experience.

Retrospectives are review artifacts, not a learning shortcut.  A proposed
improvement remains explicitly unvalidated until a human reviews its evidence
and implements a rule plus a regression test.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .experience import DecisionRecord


VALID_LESSON_STATUSES = frozenset({"proposed", "validated", "rejected"})


def build_stage_retrospective(
    *,
    stage: str,
    outcome: str,
    records: Iterable[DecisionRecord] = (),
    starting_state: dict[str, Any] | None = None,
    ending_state: dict[str, Any] | None = None,
    notes: Iterable[str] = (),
    proposed_lessons: Iterable[str] = (),
) -> dict[str, Any]:
    """Create a JSON-safe, reviewable stage record from observed decisions."""
    if not stage.strip():
        raise ValueError("stage must not be empty")
    if outcome not in {"completed", "failed", "abandoned"}:
        raise ValueError("outcome must be completed, failed, or abandoned")

    evidence = tuple(records)
    evaluated = [record.prediction_evaluation for record in evidence if record.prediction_evaluation]
    comparisons = [item for item in evaluated if item.get("all_matched") is not None]
    matched = sum(item["all_matched"] is True for item in comparisons)
    verified = sum(record.immediate_outcome == "verified" for record in evidence)

    return {
        "schema": "veda.stage-retrospective.v1",
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "outcome": outcome,
        "starting_state": starting_state or {},
        "ending_state": ending_state or {},
        "metrics": {
            "decision_records": len(evidence),
            "verified_outcomes": verified,
            "prediction_comparisons": len(comparisons),
            "matched_predictions": matched,
            "prediction_match_rate": matched / len(comparisons) if comparisons else None,
        },
        "evidence_record_ids": [record.id for record in evidence],
        "notes": list(notes),
        "lessons": [
            {
                "statement": lesson,
                "status": "proposed",
                "evidence_record_ids": [],
                "implementation": None,
                "regression_test": None,
            }
            for lesson in proposed_lessons
            if lesson.strip()
        ],
        "promotion_policy": (
            "A lesson changes VEDA only after review, cited or run evidence, "
            "an explicit implementation, and a regression test."
        ),
    }


def validate_lesson(lesson: dict[str, Any], *, evidence_record_ids: Iterable[str], implementation: str, regression_test: str) -> dict[str, Any]:
    """Return a validated lesson only when its evidence and test are named."""
    evidence = [identifier for identifier in evidence_record_ids if identifier]
    if lesson.get("status") not in VALID_LESSON_STATUSES:
        raise ValueError("unknown lesson status")
    if not evidence or not implementation.strip() or not regression_test.strip():
        raise ValueError("validation requires evidence, implementation, and regression test")
    return {
        **lesson,
        "status": "validated",
        "evidence_record_ids": evidence,
        "implementation": implementation,
        "regression_test": regression_test,
    }


def write_stage_retrospective(document: dict[str, Any], path: Path) -> None:
    """Atomically save a review artifact without touching VEDA knowledge."""
    if document.get("schema") != "veda.stage-retrospective.v1":
        raise ValueError("not a VEDA stage retrospective")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
