"""Publish a concise, evidence-first VEDA Report Card."""
from __future__ import annotations

import html, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA = "veda.handoff-dashboard.v1"

def newest_screenshot(*directories: Path) -> Path | None:
    images = [p for d in directories if d.exists() for p in d.glob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    return max(images, key=lambda p: p.stat().st_mtime) if images else None

def append_handoff(*, log_path: Path, asset_dir: Path, packet: dict[str, Any], screenshot: Path | None) -> dict[str, Any]:
    if packet.get("schema") != "veda.review-handoff.v1": raise ValueError("not a VEDA review handoff")
    doc = json.loads(log_path.read_text()) if log_path.exists() else {"schema": SCHEMA, "handoffs": []}
    if doc.get("schema") != SCHEMA: raise ValueError("not a VEDA handoff dashboard log")
    ref = None
    if screenshot:
        if not screenshot.is_file(): raise FileNotFoundError(f"screenshot not found: {screenshot}")
        asset_dir.mkdir(parents=True, exist_ok=True); name = f"handoff-{uuid4().hex[:8]}{screenshot.suffix.lower()}"
        shutil.copy2(screenshot, asset_dir / name); ref = f"assets/handoffs/{name}"
    entry = {"id": str(uuid4()), "packet": packet, "screenshot": ref, "review": {"status":"awaiting_codex_review","feedback":None,"implementation_status":"pending","implementation_summary":None}}
    doc["handoffs"].append(entry); log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"); return entry

def _load_document(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA: raise ValueError("not a VEDA handoff dashboard log")
    return doc

def _latest_entry(doc: dict[str, Any], retrospective_id: str | None = None) -> dict[str, Any]:
    entries = doc.get("handoffs", []); entries = [e for e in entries if e["packet"]["retrospective_id"] == retrospective_id] if retrospective_id else entries
    if not entries: raise ValueError("no matching VEDA handoff")
    return entries[-1]

def record_review_feedback(*, log_path: Path, feedback: str, retrospective_id: str | None = None) -> dict[str, Any]:
    if not feedback.strip(): raise ValueError("feedback must not be empty")
    doc = _load_document(log_path); entry = _latest_entry(doc, retrospective_id)
    entry.setdefault("review", {}).update({"status":"feedback_sent_to_veda","feedback":feedback.strip(),"feedback_at":datetime.now(timezone.utc).isoformat(),"implementation_status":"pending","implementation_summary":None})
    log_path.write_text(json.dumps(doc, indent=2, sort_keys=True)+"\n", encoding="utf-8"); return entry

def acknowledge_feedback(*, log_path: Path, implementation_status: str, summary: str, retrospective_id: str | None = None) -> dict[str, Any]:
    if implementation_status not in {"implemented","no_change_needed","needs_clarification"}: raise ValueError("invalid implementation status")
    if not summary.strip(): raise ValueError("implementation summary must not be empty")
    doc = _load_document(log_path); entry = _latest_entry(doc, retrospective_id); review = entry.setdefault("review", {})
    if review.get("status") != "feedback_sent_to_veda": raise ValueError("Codex feedback must be sent before VEDA can acknowledge it")
    review.update({"status":"handoff_complete" if implementation_status != "needs_clarification" else "veda_needs_clarification","implementation_status":implementation_status,"implementation_summary":summary.strip(),"implemented_at":datetime.now(timezone.utc).isoformat()})
    log_path.write_text(json.dumps(doc, indent=2, sort_keys=True)+"\n", encoding="utf-8"); return entry

def _entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    result=[]; seen=set()
    for entry in reversed(doc.get("handoffs", [])):
        if entry["packet"]["stage"] not in seen: seen.add(entry["packet"]["stage"]); result.append(entry)
    return result

def render_handoff_dashboard(document: dict[str, Any], output_path: Path, floor_runs: list[dict[str, Any]] | None = None, relics: list[dict[str, Any]] | None = None) -> None:
    entries=_entries(document); floor_runs=floor_runs or []; relics=relics or []; latest=entries[0] if entries else None
    status=f"Latest verified milestone: {latest['packet']['stage']}" if latest else "Run outcome not recorded"
    cards=[]
    for entry in entries:
        p=entry["packet"]; image=f'<img src="{html.escape(entry["screenshot"])}" alt="Evidence screenshot for {html.escape(p["stage"])}">' if entry.get("screenshot") else ""
        note=(p.get("notes") or ["No summary recorded."])[0]; note=re.sub(r"After this stage, the complete [\d,]+-line .*? was read\. ", "", note)
        lesson=(p.get("proposed_lessons") or ["No strategy lesson recorded."])[0]
        cards.append(f'<article>{image}<div class="card-copy{" written-evidence" if not image else ""}"><small>{"Screenshot evidence" if image else "Written evidence"} · {html.escape(p["outcome"])}</small><h3>{html.escape(p["stage"])}</h3><p>{html.escape(note)}</p><p><b>VEDA learned:</b> {html.escape(lesson)}</p></div></article>')
    hero_image=f'<img src="{html.escape(latest["screenshot"])}" alt="Latest evidence screenshot">' if latest and latest.get("screenshot") else ""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VEDA — Report Card</title><style>:root{{--bg:#0b151e;--panel:#152832;--ink:#f2ead8;--muted:#bdc1bd;--gold:#e4ba5d;--line:#45606a;--accent:#a9ded0}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 system-ui,sans-serif}}.wrap{{width:min(1100px,calc(100% - 32px));margin:auto}}a{{color:#ffe194}}header{{padding:28px 0 58px;background:radial-gradient(circle at 82% 12%,#37595a,transparent 26rem)}}small{{color:var(--gold);font-weight:800;letter-spacing:.08em;text-transform:uppercase}}h1,h2,h3{{line-height:1.06}}h1{{max-width:760px;margin:18px 0 16px;font-size:clamp(3.2rem,8vw,6.5rem);letter-spacing:-.065em}}h2{{font-size:clamp(2rem,4vw,3.3rem)}}h3{{margin:7px 0 12px;font-size:1.3rem}}.lede,p{{color:var(--muted)}}.outcome{{display:grid;grid-template-columns:1.35fr .65fr;margin-top:36px;border:1px solid var(--line)}}.outcome>*{{min-height:150px;padding:25px;background:var(--panel)}}.outcome strong{{display:block;color:var(--accent);font-size:clamp(1.5rem,3vw,2.35rem);line-height:1.1}}.outcome img,article img{{width:100%;height:100%;object-fit:cover;padding:0}}main{{padding:64px 0 88px}}section{{margin-bottom:66px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.metric,article,details{{border:1px solid var(--line);background:var(--panel)}}.metric{{padding:19px}}.metric strong{{display:block;color:var(--gold);font-size:2rem}}.timeline{{display:grid;gap:14px}}article{{display:grid;grid-template-columns:260px 1fr}}article img{{min-height:190px}}.card-copy{{padding:22px}}.card-copy.written-evidence{{grid-column:1 / -1}}article p:last-child{{color:var(--ink)}}details{{padding:18px}}summary{{cursor:pointer;font-weight:800}}@media(max-width:650px){{.outcome,article{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}.outcome img,article img{{max-height:230px}}}}</style></head><body><header><div class="wrap"><a href="index.html">← VEDA Home</a><small>Slay the Spire · current run evidence</small><h1>What VEDA has proven so far.</h1><p class="lede">A concise, evidence-first record. Verified results, incomplete logs, and proposed lessons stay distinct.</p><div class="outcome"><div><small>Run status</small><strong>{html.escape(status)}</strong><p>This record does not claim a run victory until one is explicitly logged.</p></div>{hero_image}</div></div></header><main class="wrap"><section><small>The scoreboard</small><h2>Start with what is known.</h2><div class="metrics"><div class="metric"><strong>{len(entries)}</strong><p>verified milestones</p></div><div class="metric"><strong>{len(floor_runs)}</strong><p>detailed floor logs</p></div><div class="metric"><strong>{len(relics)}</strong><p>confirmed relics</p></div></div></section><section><small>How the run moved forward</small><h2>Evidence milestones</h2><p>Screenshot-backed evidence appears when captured. Other verified evidence stays compact.</p><div class="timeline">{"".join(cards) or "<p>No milestone evidence recorded yet.</p>"}</div></section><section><small>The full record</small><h2>Detailed floor logs</h2><details><summary>{len(floor_runs)} detailed floor logs available</summary><p>{"Floor-by-floor entries are available in telemetry." if floor_runs else "No detailed floor logs have been recorded for this run yet."}</p></details></section></main></body></html>''', encoding="utf-8")
