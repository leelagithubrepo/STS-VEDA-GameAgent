#!/usr/bin/env python3
"""Publish Codex's reviewed recommendation to VEDA's shared handoff log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.handoff_dashboard import record_review_feedback, render_handoff_dashboard


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Send reviewed feedback back to VEDA")
    parser.add_argument("--feedback", required=True)
    parser.add_argument("--retrospective-id")
    args = parser.parse_args()
    log = ROOT / "data" / "handoff_runs.json"
    entry = record_review_feedback(log_path=log, feedback=args.feedback, retrospective_id=args.retrospective_id)
    render_handoff_dashboard(json.loads(log.read_text(encoding="utf-8")), ROOT / "docs" / "handoffs.html")
    print(f"Feedback sent to VEDA for {entry['packet']['stage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
