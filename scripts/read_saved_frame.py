#!/usr/bin/env python3
"""Read partial HUD/card/combat evidence from one saved PNG, without game control."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.native_ocr import NativeTextReader
from veda.saved_frame_reader import read_saved_frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read partial evidence from a saved PNG. Does not capture, play, or authorize a run.")
    parser.add_argument("image", type=Path, help="saved PNG; never a live screen source")
    parser.add_argument("--native-helper", type=Path, required=True,
                        help="explicitly built scripts/native_ocr.swift executable")
    parser.add_argument("--viewport", type=int, nargs=4, required=True,
                        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="game bounds in original image pixels; inspect the saved image first")
    parser.add_argument("--expected-sha256", help="optionally require this exact saved-image hash")
    parser.add_argument("--single-pass", action="store_true",
                        help="skip focused energy, combat, header, cost-symbol, and hand-title OCR; "
                             "combat pixels still use the whole-image text pass")
    parser.add_argument("--timeout-seconds", type=float, default=5,
                        help="deadline per native invocation, >0 and <=60 seconds (default: 5); "
                             "default mode may invoke it up to four times")
    args = parser.parse_args(argv)
    try:
        reader = NativeTextReader(args.native_helper, timeout_seconds=args.timeout_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    result = read_saved_frame(args.image, viewport=args.viewport, reader=reader,
                             expected_sha256=args.expected_sha256, refine_cards=not args.single_pass,
                             refine_energy=not args.single_pass, refine_combat=not args.single_pass)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
