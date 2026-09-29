#!/usr/bin/env python3
"""Poll an external bridge after a pause, without sending gameplay input."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veda.recovery_supervisor import RecoveryJournal, retry_delays


def _probe_ready(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode != 0:
        return False
    if not math.isfinite(float(result.returncode)):
        return False
    output = (result.stdout or "").lower()
    return bool(re.search(r"(?:—|-)\s*ok\s*$", output, re.MULTILINE))


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for paused PlayStation/Remote Play recovery")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pending-action-id")
    parser.add_argument("--probe", nargs="+", default=["./scripts/bridge", "status"])
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--probe-timeout", type=float, default=5.0)
    args = parser.parse_args()
    if (not 1 <= args.attempts <= 64 or not math.isfinite(args.probe_timeout)
            or args.probe_timeout <= 0):
        parser.error("--attempts must be 1..64 and --probe-timeout must be positive")
    journal = RecoveryJournal(args.run_dir, args.run_id)
    journal.append("interruption", args.reason, pending_action_id=args.pending_action_id,
                   input_sent=bool(args.pending_action_id))
    for attempt, delay in enumerate(retry_delays(args.attempts), 1):
        time.sleep(delay)
        try:
            result = subprocess.run(args.probe, capture_output=True, text=True,
                                    timeout=args.probe_timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and _probe_ready(result):
            journal.append("recovered", "connectivity probe succeeded",
                           pending_action_id=args.pending_action_id,
                           input_sent=bool(args.pending_action_id), attempt=attempt)
            print("recovered")
            return 0
    journal.append("recovery_failed", "connectivity probe did not recover",
                   pending_action_id=args.pending_action_id,
                   input_sent=bool(args.pending_action_id), attempt=args.attempts)
    print("recovery_failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
