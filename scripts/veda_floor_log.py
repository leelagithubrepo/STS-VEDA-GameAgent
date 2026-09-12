#!/usr/bin/env python3
"""Log a completed floor and refresh the local VEDA telemetry page."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.floor_telemetry import load_floor_log, record_floor, render_floor_dashboard


ROOT = Path(__file__).resolve().parents[1]


def _metric(value: str) -> tuple[str, str]:
    key, separator, metric_value = value.partition("=")
    if not separator or not key.strip() or not metric_value.strip():
        raise argparse.ArgumentTypeError("metrics must use key=value")
    return key.strip(), metric_value.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Log a floor and regenerate VEDA telemetry HTML")
    parser.add_argument("--act", type=int, required=True)
    parser.add_argument("--floor", type=int, required=True)
    parser.add_argument("--outcome", required=True, choices=("victory", "defeat", "event", "shop", "rest", "treasure", "unknown"))
    parser.add_argument("--screenshot", type=Path, required=True)
    parser.add_argument("--hp", type=int)
    parser.add_argument("--max-hp", type=int)
    parser.add_argument("--gold", type=int)
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--metric", action="append", type=_metric, default=[])
    args = parser.parse_args()
    entry = record_floor(
        log_path=ROOT / "data" / "floor_runs.json", screenshot=args.screenshot,
        screenshot_dir=ROOT / "docs" / "assets" / "floor-runs", act=args.act, floor=args.floor,
        outcome=args.outcome, hp=args.hp, max_hp=args.max_hp, gold=args.gold,
        notes=tuple(args.note), telemetry=dict(args.metric),
    )
    dashboard = ROOT / "docs" / "floor-telemetry.html"
    render_floor_dashboard(load_floor_log(ROOT / "data" / "floor_runs.json"), dashboard)
    print(f"logged {entry['id']}\n{dashboard}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
