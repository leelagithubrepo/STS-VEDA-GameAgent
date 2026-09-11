#!/usr/bin/env python3
"""Run VEDA as a standalone, watch-only observation service.

This is safe to run outside VS Code.  It only saves screenshots and a latest
status document; it never opens a PS5 session or emits controller input.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.observation import DEFAULT_CAPTURE_DIR
from veda.standalone import capture_and_publish


def main() -> int:
    parser = argparse.ArgumentParser(description="VEDA standalone watch-only observer")
    parser.add_argument("--interval", type=float, default=20.0, help="seconds between captures (default: 20)")
    parser.add_argument("--once", action="store_true", help="capture exactly one frame and exit")
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument("--status-file", type=Path, default=None)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    while True:
        try:
            status = capture_and_publish(capture_dir=args.capture_dir, status_file=args.status_file)
            print(json.dumps(status), flush=True)
        except RuntimeError as exc:
            print(f"VEDA watch capture failed: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 2
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
