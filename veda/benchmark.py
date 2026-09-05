"""Repeatable, field-level evaluation for local screenshot interpreters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from .vision import StructuredGameState, VisionProvider


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    image_path: Path
    expected: dict[str, Any]


@dataclass(frozen=True)
class BenchmarkResult:
    case: str
    latency_seconds: float
    schema_valid: bool
    fields_scored: int
    fields_correct: int
    mismatches: tuple[str, ...]

    @property
    def accuracy(self) -> float:
        return self.fields_correct / self.fields_scored if self.fields_scored else 0.0


def score_state(actual: StructuredGameState, expected: dict[str, Any]) -> tuple[int, int, tuple[str, ...]]:
    """Score only expected fields, preserving unknown/untested fields as neutral."""
    value = asdict(actual)

    def normalized(item: Any) -> Any:
        if isinstance(item, tuple):
            return [normalized(value) for value in item]
        if isinstance(item, list):
            return [normalized(value) for value in item]
        if isinstance(item, dict):
            return {key: normalized(value) for key, value in item.items()}
        return item

    correct = 0
    mismatches: list[str] = []
    for field, wanted in expected.items():
        got = normalized(value[field])
        wanted = normalized(wanted)
        if got == wanted:
            correct += 1
        else:
            mismatches.append(f"{field}: expected {wanted!r}, got {got!r}")
    return len(expected), correct, tuple(mismatches)


def run_benchmark(provider: VisionProvider, cases: list[BenchmarkCase]) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for case in cases:
        started = perf_counter()
        state = provider.observe(case.image_path)
        latency = perf_counter() - started
        fields, correct, mismatches = score_state(state, case.expected)
        results.append(BenchmarkResult(case.name, latency, True, fields, correct, mismatches))
    return results


def summarize(results: list[BenchmarkResult]) -> dict[str, Any]:
    fields = sum(result.fields_scored for result in results)
    correct = sum(result.fields_correct for result in results)
    return {
        "cases": len(results),
        "field_accuracy": correct / fields if fields else 0.0,
        "mean_latency_seconds": sum(result.latency_seconds for result in results) / len(results) if results else 0.0,
        "results": [{**asdict(result), "accuracy": result.accuracy} for result in results],
    }
