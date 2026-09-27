#!/usr/bin/env python3
"""Report historical implementation coverage without operating the game."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.autonomy_readiness import assess_automation_readiness, load_checkpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        "Exit 0 means a report was produced, not autonomous readiness. The report never authorizes game input.")
    parser.add_argument("checkpoint", type=Path, help="Historical strategy checkpoint JSON, at most 1 MB")
    parser.add_argument("--output", type=Path, help="Optional new report file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        checkpoint, source = load_checkpoint(args.checkpoint)
        report = assess_automation_readiness(checkpoint)
        report["checkpoint_source"] = source
        encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output:
            if args.output.resolve() == args.checkpoint.resolve():
                raise ValueError("report must not overwrite its checkpoint")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x") as stream:
                stream.write(encoded)
        print(encoded, end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        print(json.dumps({"schema": "veda.automation-readiness-error.v1", "error": str(error),
                          "runtime_authorized": False, "controller_authorized": False, "autonomy_ready": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
