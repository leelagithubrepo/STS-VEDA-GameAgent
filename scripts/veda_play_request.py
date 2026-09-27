#!/usr/bin/env python3
"""Package a reviewed image into a request file; no capture, connection or input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.play_requests import PlayRequestError, SCREENS, write_arm_request
from veda.reviewed_play import MAX_AGE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    arm = sub.add_parser("arm", help="Create an arm request for later submission to the persistent adapter.",
        description="Write a request only. You must inspect the exact capture and explicitly declare the current-run arming phrase. The adapter rechecks evidence before arming.")
    arm.add_argument("--run-id", required=True, help="Explicit current run ID; this helper does not look up the run.")
    arm.add_argument("--capture", required=True, type=Path,
        help=f"Inspected PNG from the game-window capture tool, with adjacent .capture.json; capture start must be within {MAX_AGE} seconds.")
    arm.add_argument("--screen", required=True, choices=SCREENS, help="Screen you observed in this exact Slay the Spire image.")
    arm.add_argument("--reviewer", required=True, help="Name of the person or advisor that inspected this image.")
    arm.add_argument("--evidence-note", required=True, help="What you inspected and any source limitations; not inferred by this helper.")
    arm.add_argument("--phrase", required=True, help="Explicit current-run arming phrase; never supplied automatically.")
    arm.add_argument("--reviewed", action="store_true", required=True,
        help="Declare you inspected this exact image and verified Slay the Spire and the supplied screen. Does not certify complete combat state.")
    arm.add_argument("--exclusive-client-confirmed", action="store_true", required=True,
        help="Declare the intended controller client is exclusive; no automatic connection check occurs here.")
    arm.add_argument("--output", required=True, type=Path, help="New JSON file in an existing directory; no existing file is overwritten.")
    args = parser.parse_args(argv)
    try:
        result = write_arm_request(run_id=args.run_id, capture=args.capture, screen=args.screen,
            reviewer=args.reviewer, evidence_note=args.evidence_note, phrase=args.phrase,
            reviewed=args.reviewed, exclusive_client_confirmed=args.exclusive_client_confirmed,
            output=args.output)
    except PlayRequestError as error:
        print(json.dumps({"request_file": None, "error": str(error), "controller_input_sent": False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
