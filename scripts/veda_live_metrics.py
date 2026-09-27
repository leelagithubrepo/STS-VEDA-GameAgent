#!/usr/bin/env python3
"""Report observation cadence for a live VEDA play block.

This is read-only. It uses timestamped observation filenames and never touches
the controller bridge or the SQLite ledger.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from statistics import median

PATTERN = re.compile(r"ps5_observation_(\d{8}T\d{6}(?:\.\d{6})?Z)(?:_[0-9a-f]{32})?\.png$")


def observation_times(directory: Path) -> list[datetime]:
    values = []
    for path in directory.glob("ps5_observation_*.png"):
        match = PATTERN.search(path.name)
        if match:
            stamp = match.group(1)
            values.append(datetime.strptime(stamp, "%Y%m%dT%H%M%S.%fZ" if "." in stamp else "%Y%m%dT%H%M%SZ"))
    return sorted(values)


def summarize(times: list[datetime]) -> dict[str, float | int | None]:
    if not times:
        return {"observations": 0, "elapsed_minutes": 0.0, "median_interval_seconds": None,
                "p95_interval_seconds": None}
    intervals = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]))
    p95 = intervals[min(len(intervals) - 1, int(len(intervals) * 0.95))] if intervals else None
    return {
        "observations": len(times),
        "elapsed_minutes": round((times[-1] - times[0]).total_seconds() / 60, 2),
        "median_interval_seconds": round(median(intervals), 2) if intervals else None,
        "p95_interval_seconds": round(p95, 2) if p95 is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize VEDA observation cadence")
    parser.add_argument("directory", type=Path, nargs="?", default=Path("artifacts/observations"))
    args = parser.parse_args()
    print(json.dumps(summarize(observation_times(args.directory)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
