#!/usr/bin/env python3
"""Let standalone VEDA acknowledge reviewed feedback and close a handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.handoff_dashboard import acknowledge_feedback, render_handoff_dashboard
from veda.floor_telemetry import load_floor_log
from veda.relic_inventory import inventory_summary, load_relic_inventory
from veda.mailbox import send_message


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Acknowledge Codex feedback as standalone VEDA")
    parser.add_argument("--status", required=True, choices=("implemented", "no_change_needed", "needs_clarification"))
    parser.add_argument("--summary", required=True)
    parser.add_argument("--retrospective-id")
    args = parser.parse_args()
    log = ROOT / "data" / "handoff_runs.json"
    entry = acknowledge_feedback(
        log_path=log, implementation_status=args.status, summary=args.summary, retrospective_id=args.retrospective_id,
    )
    send_message(
        mailbox_path=ROOT / "artifacts" / "mailbox" / "review-mailbox.json",
        sender="veda", recipient="llm", kind="implementation_confirmation",
        body=f"{args.status}: {args.summary}", related_id=entry["packet"]["retrospective_id"],
    )
    render_handoff_dashboard(json.loads(log.read_text(encoding="utf-8")), ROOT / "docs" / "report-card.html", load_floor_log(ROOT / "data" / "floor_runs.json")["runs"], inventory_summary(load_relic_inventory(ROOT / "data" / "relic_inventory.json")))
    print(f"Handoff {entry['review']['status']}: {entry['packet']['stage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
