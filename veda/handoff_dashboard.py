"""Publish VEDA review handoffs as a compact, screenshot-backed HTML log."""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = "veda.handoff-dashboard.v1"


def newest_screenshot(*directories: Path) -> Path | None:
    images = [image for directory in directories if directory.exists() for image in directory.glob("*") if image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    return max(images, key=lambda image: image.stat().st_mtime) if images else None


def append_handoff(*, log_path: Path, asset_dir: Path, packet: dict[str, Any], screenshot: Path | None) -> dict[str, Any]:
    if packet.get("schema") != "veda.review-handoff.v1":
        raise ValueError("not a VEDA review handoff")
    document = json.loads(log_path.read_text()) if log_path.exists() else {"schema": SCHEMA, "handoffs": []}
    if document.get("schema") != SCHEMA:
        raise ValueError("not a VEDA handoff dashboard log")
    image_reference = None
    if screenshot is not None:
        if not screenshot.is_file():
            raise FileNotFoundError(f"screenshot not found: {screenshot}")
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_name = f"handoff-{uuid4().hex[:8]}{screenshot.suffix.lower()}"
        shutil.copy2(screenshot, asset_dir / asset_name)
        image_reference = f"assets/handoffs/{asset_name}"
    entry = {
        "id": str(uuid4()),
        "packet": packet,
        "screenshot": image_reference,
        "review": {
            "status": "awaiting_codex_review",
            "feedback": None,
            "implementation_status": "pending",
            "implementation_summary": None,
        },
    }
    document["handoffs"].append(entry)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


def _load_document(log_path: Path) -> dict[str, Any]:
    document = json.loads(log_path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA:
        raise ValueError("not a VEDA handoff dashboard log")
    return document


def _latest_entry(document: dict[str, Any], retrospective_id: str | None = None) -> dict[str, Any]:
    entries = document.get("handoffs", [])
    if retrospective_id:
        entries = [entry for entry in entries if entry["packet"]["retrospective_id"] == retrospective_id]
    if not entries:
        raise ValueError("no matching VEDA handoff")
    return entries[-1]


def record_review_feedback(*, log_path: Path, feedback: str, retrospective_id: str | None = None) -> dict[str, Any]:
    """Record Codex's review in the shared mailbox and preserve its audit trail."""
    if not feedback.strip():
        raise ValueError("feedback must not be empty")
    document = _load_document(log_path)
    entry = _latest_entry(document, retrospective_id)
    review = entry.setdefault("review", {})
    review.update({
        "status": "feedback_sent_to_veda",
        "feedback": feedback.strip(),
        "feedback_at": datetime.now(timezone.utc).isoformat(),
        "implementation_status": "pending",
        "implementation_summary": None,
    })
    log_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


def acknowledge_feedback(*, log_path: Path, implementation_status: str, summary: str, retrospective_id: str | None = None) -> dict[str, Any]:
    """Let standalone VEDA close the loop after it reads Codex's feedback."""
    if implementation_status not in {"implemented", "no_change_needed", "needs_clarification"}:
        raise ValueError("invalid implementation status")
    if not summary.strip():
        raise ValueError("implementation summary must not be empty")
    document = _load_document(log_path)
    entry = _latest_entry(document, retrospective_id)
    review = entry.setdefault("review", {})
    if review.get("status") != "feedback_sent_to_veda":
        raise ValueError("Codex feedback must be sent before VEDA can acknowledge it")
    review.update({
        "status": "handoff_complete" if implementation_status in {"implemented", "no_change_needed"} else "veda_needs_clarification",
        "implementation_status": implementation_status,
        "implementation_summary": summary.strip(),
        "implemented_at": datetime.now(timezone.utc).isoformat(),
    })
    log_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


def render_handoff_dashboard(document: dict[str, Any], output_path: Path) -> None:
    entries = list(reversed(document.get("handoffs", [])))
    cards = []
    for entry in entries:
        packet = entry["packet"]
        review = entry.get("review", {})
        image = f'<img src="{html.escape(entry["screenshot"])}" alt="{html.escape(packet["stage"])} screenshot">' if entry.get("screenshot") else "<div class=\"missing\">No screenshot captured</div>"
        lessons = "<br>".join(html.escape(item) for item in packet.get("proposed_lessons", [])) or "No proposed lessons."
        notes = "<br>".join(html.escape(item) for item in packet.get("notes", [])) or "No notes."
        feedback = html.escape(review.get("feedback") or "Awaiting Codex review.")
        implementation = html.escape(review.get("implementation_summary") or "Awaiting VEDA acknowledgement.")
        status = html.escape(review.get("status", packet["review_status"]))
        cards.append(f'<article>{image}<div><p class="eyebrow">{html.escape(packet["outcome"])} · {status}</p><h2>{html.escape(packet["stage"])}</h2><p><strong>Observed:</strong> {notes}</p><p><strong>What VEDA did well / learned:</strong> {lessons}</p><p><strong>Codex recommendation:</strong> {feedback}</p><p><strong>VEDA implementation:</strong> {implementation}</p></div></article>')
    body = "\n".join(cards) or "<p>No handoffs yet.</p>"
    output_path.write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VEDA handoffs</title><style>body{{margin:0;background:#101b24;color:#e9e4d4;font:16px/1.55 Georgia,serif}}main{{width:min(1080px,calc(100% - 32px));margin:auto;padding:48px 0}}a{{color:#ffe08a}}h1{{font-size:clamp(2.6rem,7vw,5rem);color:#fff0bf;margin:8px 0 22px}}article{{display:grid;grid-template-columns:42% 1fr;margin:18px 0;border:1px solid #4c5960;background:#172832}}img,.missing{{width:100%;min-height:220px;object-fit:cover;background:#0b1116}}.missing{{display:grid;place-items:center;color:#b9b6a9}}article div:last-child{{padding:24px}}h2{{color:#ffe5a0;margin:6px 0 14px}}.eyebrow{{color:#e2b758;font:700 .75rem system-ui,sans-serif;letter-spacing:.11em;text-transform:uppercase}}@media(max-width:680px){{article{{grid-template-columns:1fr}}}}</style></head><body><main><a href="index.html">← VEDA project report</a><p class="eyebrow">Standalone review mailbox</p><h1>Stage handoffs</h1>{body}</main></body></html>''', encoding="utf-8")
