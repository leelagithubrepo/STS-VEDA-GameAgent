"""A local mailbox between standalone VEDA and a reviewing Codex session."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_review_packet(retrospective: dict[str, Any]) -> dict[str, Any]:
    """Reduce a retrospective to the facts a reviewer must address."""
    if retrospective.get("schema") != "veda.stage-retrospective.v1":
        raise ValueError("not a VEDA stage retrospective")
    lessons = retrospective.get("lessons", [])
    return {
        "schema": "veda.review-handoff.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "retrospective_id": retrospective["id"],
        "stage": retrospective["stage"],
        "outcome": retrospective["outcome"],
        "starting_state": retrospective.get("starting_state", {}),
        "ending_state": retrospective.get("ending_state", {}),
        "metrics": retrospective.get("metrics", {}),
        "notes": retrospective.get("notes", []),
        "proposed_lessons": [lesson["statement"] for lesson in lessons if lesson.get("status") == "proposed"],
        "review_status": "awaiting_codex_feedback",
        "promotion_policy": retrospective["promotion_policy"],
    }


def write_json(document: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_feedback(*, packet: dict[str, Any], feedback: str, path: Path) -> None:
    """Publish reviewer guidance for VEDA without promoting a rule itself."""
    if packet.get("schema") != "veda.review-handoff.v1":
        raise ValueError("not a VEDA review handoff")
    document = {
        "schema": "veda.codex-feedback.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "retrospective_id": packet["retrospective_id"],
        "stage": packet["stage"],
        "feedback": feedback,
        "instruction": "Treat this as guidance; do not promote a strategy rule without evidence and a regression test.",
    }
    write_json(document, path)
