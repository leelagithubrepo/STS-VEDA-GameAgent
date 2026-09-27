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
args = parser.parse_args()
output_dir = Path(tempfile.mkdtemp(prefix="veda-live-")) if args.ephemeral else None
print(capture_visible_ps5_feed(output_dir) if output_dir else capture_visible_ps5_feed())
