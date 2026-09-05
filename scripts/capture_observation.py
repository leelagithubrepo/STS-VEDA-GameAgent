#!/usr/bin/env python3
"""Capture exactly one passive observation of the visible PS5 feed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.observation import capture_visible_ps5_feed

print(capture_visible_ps5_feed())
