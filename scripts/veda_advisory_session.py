#!/usr/bin/env python3
"""Keep compact checked-advice context without operating the game."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.advisory_session import AdvisorySession, MAX_REQUEST_BYTES, _read


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--context", type=Path, help="initial run/floor/combat/turn IDs")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--replay", action="store_true", help="historical checks; never live evidence")
    parser.add_argument("--request", type=Path, help="one request; otherwise bounded JSONL on stdin")
    args = parser.parse_args(argv)
    try:
        context = json.loads(_read(args.context, 65536)) if args.context else None
        with AdvisorySession(args.directory, context=context, resume=args.resume,
                             mode="replay" if args.replay else "review") as session:
            if args.request:
                print(json.dumps(session.handle(json.loads(_read(args.request, MAX_REQUEST_BYTES))), allow_nan=False))
            else:
                print(json.dumps({"status": "ready", "summary": session.handle({"operation": "summary"})}), flush=True)
                while True:
                    line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES+1)
                    if not line:
                        break
                    if len(line) > MAX_REQUEST_BYTES:
                        raise ValueError("request line exceeds byte limit")
                    try:
                        result = session.handle(json.loads(line))
                    except (ValueError, KeyError, TypeError, OSError) as error:
                        result = {"status": "rejected", "error": str(error), "controller_authorized": False}
                    print(json.dumps(result, allow_nan=False), flush=True)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
