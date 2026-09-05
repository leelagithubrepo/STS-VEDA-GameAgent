#!/usr/bin/env python3
"""Interpret a PS5 screenshot locally; this script never sends game input."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.vision import LocalOllamaVisionProvider

if len(sys.argv) != 2:
    raise SystemExit("usage: python3 scripts/interpret_observation.py <screenshot.png>")

state = LocalOllamaVisionProvider().observe(Path(sys.argv[1]))
print(json.dumps(asdict(state), indent=2))
