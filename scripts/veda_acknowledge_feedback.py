#!/usr/bin/env python3
"""Let standalone VEDA acknowledge reviewed feedback and close a handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.handoff_dashboard import acknowledge_feedback, render_handoff_dashboard


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
    render_handoff_dashboard(json.loads(log.read_text(encoding="utf-8")), ROOT / "docs" / "handoffs.html")
    print(f"Handoff {entry['review']['status']}: {entry['packet']['stage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
