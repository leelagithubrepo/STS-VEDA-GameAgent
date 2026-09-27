#!/usr/bin/env python3
"""Save one reviewed pause request. No capture, input, or history reconstruction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.advisory_checkpoint import MAX_REQUEST_BYTES, record_advisory_checkpoint
from veda.telemetry_database import TelemetryDatabase


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Atomically record a reviewed pause, optional inventory baseline and resume notes. Does not inspect the game or authorize input.")
    parser.add_argument("--database", type=Path, required=True, help="Existing private VEDA SQLite database; an active run and floor must already exist.")
    parser.add_argument("--request", type=Path, required=True, help="UTF-8 veda.advisory-checkpoint.v1 JSON request (at most 128 KiB). Capture must be no more than 180 seconds old.")
    args = parser.parse_args(argv)
    try:
        with args.request.open("rb") as stream:
            raw = stream.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("checkpoint request exceeds byte limit")
        request = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))
        result = record_advisory_checkpoint(TelemetryDatabase(args.database), request)
    except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error) as exc:
        print(json.dumps({"saved": False, "error": str(exc), "controller_authorized": False, "runtime_authorized": False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
