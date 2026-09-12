#!/usr/bin/env python3
"""Publish the latest stage retrospective to VEDA's local review mailbox."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.handoff import build_review_packet, write_json
from veda.handoff_dashboard import append_handoff, render_handoff_dashboard
from veda.floor_telemetry import load_floor_log
from veda.relic_inventory import inventory_summary, load_relic_inventory
from veda.mailbox import send_message


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a VEDA-to-Codex review handoff")
    parser.add_argument("--retrospective", type=Path)
    parser.add_argument("--latest", action="store_true", help="use the newest local stage retrospective")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "handoff" / "latest-review.json")
    parser.add_argument("--screenshot", type=Path, help="optional screenshot explicitly tied to this stage")
    args = parser.parse_args()
    if bool(args.retrospective) == args.latest:
        parser.error("use exactly one of --retrospective or --latest")
    if args.latest:
        candidates = list((ROOT / "artifacts" / "retrospectives").glob("*.json"))
        if not candidates:
            parser.error("no retrospectives found")
        source = max(candidates, key=lambda path: path.stat().st_mtime)
    else:
        source = args.retrospective
    packet = build_review_packet(json.loads(source.read_text(encoding="utf-8")))
    write_json(packet, args.output)
    send_message(
        mailbox_path=ROOT / "artifacts" / "mailbox" / "review-mailbox.json",
        sender="veda", recipient="llm", kind="review_request",
        body=f"Review requested: {packet['stage']} ({packet['outcome']}).",
        related_id=packet["retrospective_id"],
    )
    screenshot = args.screenshot
    log_path = ROOT / "data" / "handoff_runs.json"
    append_handoff(
        log_path=log_path, asset_dir=ROOT / "docs" / "assets" / "handoffs", packet=packet, screenshot=screenshot,
    )
    dashboard = ROOT / "docs" / "report-card.html"
    render_handoff_dashboard(
        json.loads(log_path.read_text(encoding="utf-8")), dashboard,
        load_floor_log(ROOT / "data" / "floor_runs.json")["runs"],
        inventory_summary(load_relic_inventory(ROOT / "data" / "relic_inventory.json")),
    )
    print(f"{args.output}\n{dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
