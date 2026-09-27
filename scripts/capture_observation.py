#!/usr/bin/env python3
"""Capture exactly one passive observation of the visible PS5 feed."""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.observation import capture_visible_ps5_feed

parser = argparse.ArgumentParser(description="Capture one passive PS5 observation")
parser.add_argument(
    "--ephemeral",
    action="store_true",
    help="write the frame to a temporary directory for immediate inspection instead of the archive",
)
parser.add_argument("--game-window", action="store_true", help="capture the unique QuickTime Movie Recording window, even when another app covers it")
parser.add_argument("--window-id", type=int, help="explicit inspected QuickTime capture window; requires --game-window")
args = parser.parse_args()
if args.window_id is not None and not args.game_window:
    parser.error("--window-id requires --game-window")
output_dir = Path(tempfile.mkdtemp(prefix="veda-live-")) if args.ephemeral else None
if args.game_window:
    from veda.game_capture import capture_game_window
    from veda.observation import DEFAULT_CAPTURE_DIR
    print(capture_game_window(output_dir or DEFAULT_CAPTURE_DIR, window_id=args.window_id))
else:
    print(capture_visible_ps5_feed(output_dir) if output_dir else capture_visible_ps5_feed())
