"""Persistent floor telemetry and a dependency-free HTML run dashboard."""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = "veda.floor-telemetry.v1"


def load_floor_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": SCHEMA, "runs": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA or not isinstance(document.get("runs"), list):
        raise ValueError("not a VEDA floor telemetry log")
    return document


def record_floor(
    *,
    log_path: Path,
    screenshot: Path,
    screenshot_dir: Path,
    act: int,
    floor: int,
    outcome: str,
    hp: int | None,
    max_hp: int | None,
    gold: int | None,
    notes: tuple[str, ...] = (),
    telemetry: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Append one completed floor and copy its evidence image for the site."""
    if act < 1 or floor < 0:
        raise ValueError("act and floor must be non-negative (act begins at 1)")
    if outcome not in {"victory", "defeat", "event", "shop", "rest", "treasure", "unknown"}:
        raise ValueError("unrecognized floor outcome")
    if not screenshot.is_file():
        raise FileNotFoundError(f"screenshot not found: {screenshot}")

    document = load_floor_log(log_path)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    suffix = screenshot.suffix.lower() if screenshot.suffix else ".png"
    asset_name = f"act-{act}-floor-{floor}-{uuid4().hex[:8]}{suffix}"
    asset_path = screenshot_dir / asset_name
    shutil.copy2(screenshot, asset_path)
    entry = {
        "id": str(uuid4()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "act": act,
        "floor": floor,
        "outcome": outcome,
        "hp": hp,
        "max_hp": max_hp,
        "gold": gold,
        "screenshot": f"assets/floor-runs/{asset_name}",
        "notes": list(notes),
        "telemetry": telemetry or {},
    }
    document["runs"].append(entry)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = log_path.with_suffix(log_path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(log_path)
    return entry


def render_floor_dashboard(document: dict[str, Any], output_path: Path) -> None:
    """Render a portable, static dashboard from the floor log."""
    runs = list(reversed(document["runs"]))
    victories = sum(run["outcome"] == "victory" for run in runs)
    cards: list[str] = []
    for run in runs:
        metrics = " · ".join(
            value for value in (
                f"HP {run['hp']}/{run['max_hp']}" if run.get("hp") is not None and run.get("max_hp") is not None else None,
                f"Gold {run['gold']}" if run.get("gold") is not None else None,
                *[f"{key}: {value}" for key, value in run.get("telemetry", {}).items()],
            ) if value
        ) or "No numeric telemetry recorded"
        notes = "<br>".join(html.escape(note) for note in run.get("notes", [])) or "No notes recorded."
        cards.append(f"""
        <article class=\"floor\">
          <img src=\"{html.escape(run['screenshot'])}\" alt=\"Act {run['act']} floor {run['floor']} evidence screenshot\">
          <div class=\"copy\"><p class=\"eyebrow\">Act {run['act']} · Floor {run['floor']} · {html.escape(run['outcome'])}</p>
          <h2>{html.escape(metrics)}</h2><p>{notes}</p><small>{html.escape(run['recorded_at'])}</small></div>
        </article>""")
    body = "\n".join(cards) or "<p class=\"empty\">No floors logged yet. Run <code>scripts/veda_floor_log.py</code> after a floor.</p>"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>VEDA — Floor telemetry</title><style>
:root{{--bg:#0e1820;--panel:#172832;--ink:#e9e4d4;--muted:#b9b6a9;--gold:#e2b758;--line:#4c5960}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(130deg,#0a131a,#172832);color:var(--ink);font:16px/1.55 Georgia,serif}}main{{width:min(1050px,calc(100% - 32px));margin:auto;padding:52px 0 80px}}a{{color:#ffe08a}}h1{{font-size:clamp(2.6rem,7vw,5rem);margin:0;color:#fff0bf}}.lede,.empty{{color:var(--muted);font-size:1.08rem}}.metrics{{display:flex;gap:14px;flex-wrap:wrap;margin:28px 0}}.metric{{padding:13px 16px;border:1px solid var(--line);background:var(--panel)}}.metric strong{{display:block;color:var(--gold);font-size:1.6rem}}.floor{{display:grid;grid-template-columns:42% 1fr;margin:18px 0;border:1px solid var(--line);background:var(--panel)}}.floor img{{width:100%;height:100%;min-height:210px;object-fit:cover;background:#0b1116}}.copy{{padding:24px}}.copy h2{{margin:8px 0 13px;color:#fff0bf;font-size:1.3rem}}.eyebrow{{color:var(--gold);font:700 .75rem/1.2 system-ui,sans-serif;letter-spacing:.11em;text-transform:uppercase}}small{{color:var(--muted)}}code{{color:#ffe08a}}@media(max-width:680px){{.floor{{grid-template-columns:1fr}}}}</style></head>
<body><main><a href=\"index.html\">← VEDA project report</a><p class=\"eyebrow\">Human-guided evidence log</p><h1>Floor telemetry</h1><p class=\"lede\">Screenshots and confirmed end-of-floor state. Proposed lessons remain separate until reviewed and tested.</p><div class=\"metrics\"><div class=\"metric\"><strong>{len(runs)}</strong> floors logged</div><div class=\"metric\"><strong>{victories}</strong> victories</div></div>{body}</main></body></html>""", encoding="utf-8")
