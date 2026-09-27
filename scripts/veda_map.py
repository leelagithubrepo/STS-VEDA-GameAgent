#!/usr/bin/env python3
"""Validate reviewed map views and compare next routes; never sends game input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.map_survey import merge_survey, plan_routes, read_json, validate_view_draft, write_artifact, write_bound_view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--survey", type=Path, help="veda.map-survey.v1 with inspected views and original image hashes")
    inputs.add_argument("--bind-view", type=Path, help="Source-free veda.map-survey-view-draft.v1; derive image identity from its original capture receipt")
    parser.add_argument("--plan", type=Path, help="Optional veda.map-plan-review.v1 resources and strategy review")
    parser.add_argument("--validate", action="store_true", help="Check source-free view structure before capture; writes no artifact")
    parser.add_argument("--capture", type=Path, help="Exact inspected fresh PNG with original capture receipt")
    parser.add_argument("--reviewer", help="Person or advisor who inspected this exact capture")
    parser.add_argument("--evidence-note", help="Actual reviewed content and limitations")
    parser.add_argument("--reviewed", action="store_true", help="Declare inspection of this exact image")
    parser.add_argument("--output", type=Path, help="New private JSON artifact; existing files are never overwritten")
    args = parser.parse_args(argv)
    if args.bind_view:
        if args.plan:
            parser.error("--plan is only valid with --survey")
        if args.validate:
            if args.output or args.capture or args.reviewer or args.evidence_note or args.reviewed:
                parser.error("--validate is source-free; omit output, capture and review flags")
        elif not (args.output and args.capture and args.reviewer and args.evidence_note and args.reviewed):
            parser.error("--bind-view requires --capture, --reviewer, --evidence-note, --reviewed and --output")
    elif args.validate or args.capture or args.reviewer or args.evidence_note or args.reviewed:
        parser.error("capture, review and --validate options require --bind-view")
    try:
        if args.bind_view:
            draft = read_json(args.bind_view)
            result = validate_view_draft(draft) if args.validate else write_bound_view(
                draft, capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, output=args.output)
            print(json.dumps(result, sort_keys=True, allow_nan=False))
            return 0
        survey = merge_survey(read_json(args.survey))
        plan = plan_routes(survey, read_json(args.plan)) if args.plan else None
        artifact = {"survey": survey, "plan": plan}
        result = {"valid": True, "nodes": len(survey["nodes"]), "coverage": survey["coverage"],
                  "expected_boss": survey["expected_boss"], "unknowns": survey["unknowns"],
                  "controller_authorized": False, "runtime_authorized": False}
        if plan:
            result.update({key: plan[key] for key in ("recommended_next_node_ids", "applied_criteria", "immediate_choice_available", "warnings")})
            result["options"] = [{key: row[key] for key in ("node_id", "kind", "rank", "criteria")} for row in plan["options"]]
        if args.output:
            result["artifact_file"] = write_artifact(args.output, artifact, survey)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
        print(json.dumps({"valid": False, "error": str(exc), "controller_authorized": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
