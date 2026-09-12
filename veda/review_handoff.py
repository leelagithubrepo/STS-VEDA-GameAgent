"""One durable VEDA-to-LLM review submission for a completed retrospective."""

from __future__ import annotations

import json
from pathlib import Path

from .floor_telemetry import load_floor_log
from .handoff import build_review_packet, write_json
from .handoff_dashboard import append_handoff, render_handoff_dashboard
from .mailbox import send_message
from .relic_inventory import inventory_summary, load_relic_inventory


def submit_for_review(*, root: Path, retrospective_path: Path, screenshot: Path | None = None) -> tuple[dict, Path, Path]:
    """Create the review packet, durable mailbox request, and public evidence update.

    This is intentionally the same operation used by both the retrospective
    command and an explicit re-submit command, so a saved retrospective cannot
    silently bypass the LLM review loop.
    """
    packet = build_review_packet(json.loads(retrospective_path.read_text(encoding="utf-8")))
    latest_review = root / "artifacts" / "handoff" / "latest-review.json"
    write_json(packet, latest_review)
    send_message(
        mailbox_path=root / "artifacts" / "mailbox" / "review-mailbox.json",
        sender="veda", recipient="llm", kind="review_request",
        body=f"Review requested: {packet['stage']} ({packet['outcome']}).",
        related_id=packet["retrospective_id"],
    )
    log_path = root / "data" / "handoff_runs.json"
    append_handoff(
        log_path=log_path,
        asset_dir=root / "docs" / "assets" / "handoffs",
        packet=packet,
        screenshot=screenshot,
    )
    dashboard = root / "docs" / "report-card.html"
    render_handoff_dashboard(
        json.loads(log_path.read_text(encoding="utf-8")),
        dashboard,
        load_floor_log(root / "data" / "floor_runs.json")["runs"],
        inventory_summary(load_relic_inventory(root / "data" / "relic_inventory.json")),
    )
    return packet, latest_review, dashboard
