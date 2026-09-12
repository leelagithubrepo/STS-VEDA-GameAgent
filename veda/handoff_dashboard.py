"""Publish a player-facing, screenshot-backed VEDA Report Card."""

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


def render_handoff_dashboard(
    document: dict[str, Any], output_path: Path, floor_runs: list[dict[str, Any]] | None = None,
    relics: list[dict[str, Any]] | None = None,
) -> None:
    """Render one player-facing Report Card from floor and stage evidence."""
    entries = list(reversed(document.get("handoffs", [])))
    floor_runs = list(reversed(floor_runs or []))
    relics = relics or []
    stage_cards: list[str] = []
    for entry in entries:
        packet = entry["packet"]
        review = entry.get("review", {})
        state = {**packet.get("starting_state", {}), **packet.get("ending_state", {})}
        ascension = state.get("ascension", "not recorded")
        image = "<div class=\"missing\">Stage summary</div>"
        lessons = "<br>".join(html.escape(item) for item in packet.get("proposed_lessons", [])) or "No strategy lesson recorded."
        notes = "<br>".join(html.escape(item) for item in packet.get("notes", [])) or "No summary recorded."
        stage_cards.append(f'''<article>{image}<div><p class="eyebrow">Ascension {html.escape(str(ascension))} · {html.escape(packet["outcome"])}</p><h2>{html.escape(packet["stage"])}</h2><p><strong>What was completed:</strong> {notes}</p><p><strong>Strategy lesson:</strong> {lessons}</p></div></article>''')
    floor_cards: list[str] = []
    for run in floor_runs:
        details = " · ".join(value for value in (
            f"HP {run['hp']}/{run['max_hp']}" if run.get("hp") is not None and run.get("max_hp") is not None else None,
            f"Gold {run['gold']}" if run.get("gold") is not None else None,
        ) if value) or "Telemetry not recorded"
        strategy = run.get("strategy") or run.get("telemetry", {}).get("recommendation", "No VEDA strategy recorded.")
        notes = " ".join(run.get("notes", [])) or "No floor note recorded."
        screenshots = run.get("screenshots") or [run["screenshot"]]
        images = "".join(f'<img src="{html.escape(path)}" alt="Act {run["act"]} floor {run["floor"]} screenshot">' for path in screenshots[:2])
        trophies = "; ".join(run.get("trophies", [])) or "None recorded"
        losses = "; ".join(run.get("losses", [])) or "None recorded"
        actions = "; ".join(run.get("actions", [])) or notes
        ascension = run.get("ascension", "not recorded")
        floor_cards.append(f'''<article><div class="shots">{images}</div><div><p class="eyebrow">Ascension {html.escape(str(ascension))} · Act {run["act"]} · Floor {run["floor"]} · {html.escape(run["outcome"])}</p><h2>{html.escape(details)}</h2><p><strong>Win / outcome:</strong> {html.escape(notes)}</p><p><strong>Trophies / rewards:</strong> {html.escape(trophies)}</p><p><strong>Losses / risk:</strong> {html.escape(losses)}</p><p><strong>What VEDA did:</strong> {html.escape(actions)}</p><p><strong>VEDA strategy:</strong> {html.escape(strategy)}</p></div></article>''')
    stages = "\n".join(stage_cards) or "<p class=\"empty\">No stage handoffs yet.</p>"
    floors = "\n".join(floor_cards) or "<p class=\"empty\">No individual floors logged yet. The next completed floor will appear here with its screenshot.</p>"
    relic_cards = "".join(
        f'<li><strong>{html.escape(relic["name"])}</strong> — {html.escape(relic["property"])} '
        f'<small>({html.escape(relic["source"])} · confidence {relic["confidence"]:.0%})</small></li>'
        for relic in relics
    ) or "<li>No confirmed relics recorded yet.</li>"
    wins = sum(run.get("outcome") == "victory" for run in floor_runs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VEDA — Report Card</title><style>:root{{--bg:#101b24;--panel:#172832;--ink:#e9e4d4;--gold:#e2b758;--line:#4c5960;--muted:#b9b6a9}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(135deg,#0b151e,#172832);color:var(--ink);font:16px/1.55 Georgia,serif}}main{{width:min(1080px,calc(100% - 32px));margin:auto;padding:48px 0 80px}}a{{color:#ffe08a}}h1{{font-size:clamp(2.8rem,7vw,5.5rem);color:#fff0bf;margin:8px 0 12px}}h2{{color:#ffe5a0;margin:6px 0 14px}}h3{{margin:42px 0 12px;color:#fff0bf;font-size:1.6rem}}article{{display:grid;grid-template-columns:42% 1fr;margin:18px 0;border:1px solid var(--line);background:var(--panel)}}img,.missing{{width:100%;min-height:240px;height:100%;object-fit:cover;background:#0b1116}}.shots{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px;background:#0b1116}}.shots img:only-child{{grid-column:span 2}}.missing{{display:grid;place-items:center;color:var(--muted)}}article div:last-child{{padding:24px}}.eyebrow{{color:var(--gold);font:700 .75rem system-ui,sans-serif;letter-spacing:.11em;text-transform:uppercase}}.lede,.empty,small{{color:var(--muted);font-size:1.08rem}}.metrics{{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}}.metric,.inventory{{padding:12px 18px;border:1px solid var(--line);background:var(--panel)}}.metric strong{{display:block;color:var(--gold);font-size:1.5rem}}.inventory li{{margin:8px 0}}@media(max-width:680px){{article{{grid-template-columns:1fr}}}}</style></head><body><main><a href="index.html">← VEDA project report</a><p class="eyebrow">Human-guided run evidence</p><h1>Report Card</h1><p class="lede">Floor-by-floor outcomes, rewards, risks, VEDA’s actions, strategy, relics, and one or two evidence screenshots.</p><div class="metrics"><div class="metric"><strong>{len(floor_runs)}</strong> floors logged</div><div class="metric"><strong>{wins}</strong> victories</div><div class="metric"><strong>{len(entries)}</strong> run milestones</div><div class="metric"><strong>{len(relics)}</strong> confirmed relics</div></div><h3>Confirmed relic inventory</h3><ul class="inventory">{relic_cards}</ul><h3>Run milestones</h3>{stages}<h3>Floor-by-floor record</h3>{floors}</main></body></html>''', encoding="utf-8")
