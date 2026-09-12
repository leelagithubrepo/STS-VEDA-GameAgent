#!/usr/bin/env python3
"""Save a stage-end VEDA retrospective for later Codex review."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.experience import ExperienceStore
from veda.retrospective import build_stage_retrospective, write_stage_retrospective
from veda.review_handoff import submit_for_review


ROOT = Path(__file__).resolve().parents[1]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "stage"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and submit an evidence-first VEDA stage retrospective")
    parser.add_argument("--stage", required=True, help="for example: Act 2 or Floor 23")
    parser.add_argument("--outcome", required=True, choices=("completed", "failed", "abandoned"))
    parser.add_argument("--experience", type=Path, default=ROOT / "artifacts" / "experience.json")
    parser.add_argument("--start-hp", type=int)
    parser.add_argument("--end-hp", type=int)
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--lesson", action="append", default=[], help="proposed lesson; it remains unvalidated")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-review-request", action="store_true", help="save only; do not notify the LLM reviewer")
    args = parser.parse_args()

    records = ExperienceStore(args.experience).records if args.experience.exists() else ()
    document = build_stage_retrospective(
        stage=args.stage,
        outcome=args.outcome,
        records=records,
        starting_state={"hp": args.start_hp} if args.start_hp is not None else {},
        ending_state={"hp": args.end_hp} if args.end_hp is not None else {},
        notes=args.note,
        proposed_lessons=args.lesson,
    )
    destination = args.output or ROOT / "artifacts" / "retrospectives" / f"{_slug(args.stage)}.json"
    write_stage_retrospective(document, destination)
    if args.no_review_request:
        print(destination)
        return 0
    _, review, dashboard = submit_for_review(root=ROOT, retrospective_path=destination)
    print(f"{destination}\n{review}\n{dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
