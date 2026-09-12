#!/usr/bin/env python3
"""Publish the latest stage retrospective to VEDA's local review mailbox."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.review_handoff import submit_for_review


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
    _, latest_review, dashboard = submit_for_review(root=ROOT, retrospective_path=source, screenshot=args.screenshot)
    if args.output != latest_review:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(latest_review.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{args.output}\n{dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
