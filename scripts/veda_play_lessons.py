#!/usr/bin/env python3
"""Retrieve compact verified historical outcomes; never authorizes game input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.play_lessons import DEFAULT_DATABASE, SCHEMA, read_play_lessons


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", help="Exact historical run; omit to search across prior runs.")
    for name in ("screen", "enemy", "card", "action"):
        parser.add_argument("--" + name, help="Match a complete recorded name, case-insensitively.")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        result = read_play_lessons(**vars(args))
    except (ValueError, OSError) as error:
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "error": str(error),
                          "historical_only": True, "live": False,
                          "runtime_authorized": False, "controller_authorized": False}), flush=True)
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
