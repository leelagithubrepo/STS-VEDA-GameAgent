#!/usr/bin/env python3
"""Benchmark local vision models against labeled VEDA screenshots."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.benchmark import BenchmarkCase, run_benchmark, summarize
from veda.vision import LocalOllamaVisionProvider

if len(sys.argv) < 2:
    raise SystemExit("usage: python3 scripts/benchmark_vision.py <model> [<model> ...]")

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "data" / "vision_benchmark.json").read_text())
cases = [BenchmarkCase(row["name"], root / row["image"], row["expected"]) for row in fixture]
for model in sys.argv[1:]:
    report = summarize(run_benchmark(LocalOllamaVisionProvider(model=model, timeout_seconds=60), cases))
    report["model"] = model
    print(json.dumps(report, indent=2))
