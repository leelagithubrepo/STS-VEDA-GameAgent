#!/usr/bin/env python3
"""Add a confirmed relic and its property to VEDA's durable run inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.floor_telemetry import load_floor_log
from veda.handoff_dashboard import render_handoff_dashboard
from veda.relic_inventory import inventory_summary, load_relic_inventory, record_relic


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Record an evidence-backed relic property")
    parser.add_argument("--name", required=True)
    parser.add_argument("--property", required=True, dest="property_text")
    parser.add_argument("--source", required=True, help="e.g. relic tooltip, reward screen, or manual reference")
    parser.add_argument("--confidence", type=float, default=1.0)
    parser.add_argument("--act", type=int)
    parser.add_argument("--floor", type=int)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    entry = record_relic(
        inventory_path=ROOT / "data" / "relic_inventory.json", name=args.name, property_text=args.property_text,
        source=args.source, confidence=args.confidence, act=args.act, floor=args.floor,
        screenshot=args.screenshot, asset_dir=ROOT / "docs" / "assets" / "relics",
    )
    handoffs = ROOT / "data" / "handoff_runs.json"
    if handoffs.exists():
        render_handoff_dashboard(
            json.loads(handoffs.read_text(encoding="utf-8")), ROOT / "docs" / "report-card.html",
            load_floor_log(ROOT / "data" / "floor_runs.json")["runs"],
            inventory_summary(load_relic_inventory(ROOT / "data" / "relic_inventory.json")),
        )
    print(f"Recorded {entry['name']}: {entry['property']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
