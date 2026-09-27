"""Local pipeline timing; no capture, model, controller, or database side effects.

Times are monotonic nanoseconds within one recorder, not calendar timestamps.
Use ``model_request_round_trip`` for provider requests: this measures their whole
round trip and does not infer internal model thinking time. A report is a snapshot;
call ``close()`` to freeze its endpoint and close the optional JSONL journal.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import math
from pathlib import Path
from threading import RLock
from time import monotonic_ns
from typing import Callable, Iterator
from uuid import uuid4


@dataclass
class _Span:
    span_id: str
    parent_id: str | None
    stage: str
    start_ns: int
    ids: dict
    end_ns: int | None = None
    error: str | None = None


class TraceSpan:
    """A span handle; attach an identity obtained during a measured operation."""

    def __init__(self, recorder: TraceRecorder, span_id: str):
        self._recorder = recorder
        self.span_id = span_id

    def annotate(self, **metadata: object) -> None:
        self._recorder._annotate(self.span_id, metadata)


def _union(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _length(intervals: list[tuple[int, int]]) -> int:
    return sum(end - start for start, end in intervals)


def _percentile(samples: list[int], quantile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


class TraceRecorder:
    """Record correlated spans, interruptions, retries, and decision coverage.

    ``clock`` must return monotonic integer nanoseconds. Correlation IDs such as
    run_id, floor_id, turn_id, action_id, frame_id, and request_id may be supplied
    as constructor defaults or span/event keywords. Nested spans inherit IDs.
    JSONL uses one append per boundary/event and retains one buffered file handle.
    The journal is append-only; each recorder has a separate trace_id even when
    several runs share a path. Report data belongs only to this recorder.

    Pause/resume and maintenance_start/maintenance_end are independent pairs;
    overlapping intervals are counted once when computing active time. Retry
    events are counts, not invented durations; measure recovery with a span.
    """

    def __init__(self, path: str | Path | None = None, *,
                 clock: Callable[[], int] = monotonic_ns, **default_ids: object):
        json.dumps(default_ids, allow_nan=False)
        self.trace_id = str(uuid4())
        self._clock = clock
        self._lock = RLock()
        self._last_ns: int | None = None
        self._start_ns = self._now()
        self._end_ns: int | None = None
        self._defaults = dict(default_ids)
        self._spans: dict[str, _Span] = {}
        self._stack: ContextVar[tuple[str, ...]] = ContextVar(
            f"pipeline_trace_{self.trace_id}", default=())
        self._events: list[dict] = []
        self._decisions: dict[str, dict] = {}
        self._interruptions: dict[str, list[tuple[int, int]]] = {
            "pause": [], "maintenance": []}
        self._open_interruptions: dict[str, int] = {}
        self._journal = None
        if path is not None:
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._journal = destination.open("a", encoding="utf-8", buffering=1)
        self._emit("trace_start", self._start_ns, ids=self._defaults)

    def _now(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("trace clock must return nonnegative integer nanoseconds")
        if self._last_ns is not None and value < self._last_ns:
            raise ValueError("trace clock moved backwards")
        self._last_ns = value
        return value

    def _require_open(self) -> None:
        if self._end_ns is not None:
            raise RuntimeError("trace recorder is closed")

    def _emit(self, kind: str, now: int, **data: object) -> None:
        line = json.dumps({"schema": "veda.pipeline-trace.v1",
                           "trace_id": self.trace_id, "kind": kind,
                           "at_ns": now, **data}, separators=(",", ":"),
                          allow_nan=False)
        if self._journal is not None:
            self._journal.write(line + "\n")

    def _ids(self, supplied: dict) -> dict:
        inherited = dict(self._defaults)
        for span_id in self._stack.get():
            inherited.update(self._spans[span_id].ids)
        inherited.update(supplied)
        json.dumps(inherited, allow_nan=False)
        return inherited

    @contextmanager
    def span(self, stage: str, **ids: object) -> Iterator[TraceSpan]:
        """Measure one operation, preserving exceptions and recording its outcome."""
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError("span stage must be a nonempty string")
        with self._lock:
            self._require_open()
            correlation = self._ids(ids)
            stack = self._stack.get()
            span = _Span(str(uuid4()), stack[-1] if stack else None,
                         stage, self._now(), correlation)
            self._emit("span_start", span.start_ns, span_id=span.span_id,
                       parent_id=span.parent_id, stage=stage, ids=correlation)
            self._spans[span.span_id] = span
            token = self._stack.set((*stack, span.span_id))
        try:
            yield TraceSpan(self, span.span_id)
        except BaseException as error:
            span.error = type(error).__name__
            raise
        finally:
            try:
                with self._lock:
                    span.end_ns = self._now()
                    self._emit("span_end", span.end_ns, span_id=span.span_id,
                               stage=stage, ids=span.ids, error=span.error)
            finally:
                self._stack.reset(token)

    def _annotate(self, span_id: str, metadata: dict) -> None:
        json.dumps(metadata, allow_nan=False)
        with self._lock:
            self._require_open()
            span = self._spans[span_id]
            if span.end_ns is not None:
                raise RuntimeError("cannot annotate a completed span")
            self._emit("span_annotation", self._now(), span_id=span_id,
                       metadata=metadata)
            span.ids.update(metadata)

    def event(self, kind: str, **metadata: object) -> None:
        """Record an event; explicit interruption pairs affect active-time totals."""
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("event kind must be a nonempty string")
        transitions = {"pause": ("pause", True), "resume": ("pause", False),
                       "maintenance_start": ("maintenance", True),
                       "maintenance_end": ("maintenance", False)}
        with self._lock:
            self._require_open()
            correlation = self._ids(metadata)
            transition = transitions.get(kind)
            if transition is not None:
                category, opening = transition
                if opening == (category in self._open_interruptions):
                    raise ValueError(f"unmatched {kind} event")
            now = self._now()
            self._emit("event", now, event=kind, metadata=correlation)
            self._events.append({"kind": kind, "at_ns": now,
                                 "metadata": correlation})
            if transition is not None:
                category, opening = transition
                if opening:
                    self._open_interruptions[category] = now
                else:
                    start = self._open_interruptions.pop(category)
                    self._interruptions[category].append((start, now))

    def record_decision(self, decision_id: str, *, handled_locally: bool,
                        escalation_reason: str | None = None,
                        error: str | None = None, **ids: object) -> None:
        """Record one real opportunity; retries must not inflate the denominator.

        Local coverage reports routing, not correctness. Local decisions with an
        error remain in that routing count and are excluded from successful local
        coverage. Nonlocal opportunities require an explicit escalation reason.
        """
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("decision_id must be a nonempty string")
        if not isinstance(handled_locally, bool):
            raise ValueError("handled_locally must be a boolean")
        if escalation_reason is not None and (
                not isinstance(escalation_reason, str) or not escalation_reason.strip()):
            raise ValueError("escalation_reason must be a nonempty string")
        if not handled_locally and not escalation_reason:
            raise ValueError("nonlocal decisions require an escalation reason")
        if handled_locally and escalation_reason is not None:
            raise ValueError("local decisions cannot have an escalation reason")
        if error is not None and (not isinstance(error, str) or not error.strip()):
            raise ValueError("error must be a nonempty error code or description")
        with self._lock:
            self._require_open()
            if decision_id in self._decisions:
                raise ValueError("decision_id already recorded")
            decision = {"decision_id": decision_id,
                        "handled_locally": handled_locally,
                        "escalation_reason": escalation_reason, "error": error,
                        "ids": self._ids(ids)}
            self._emit("decision", self._now(), **decision)
            self._decisions[decision_id] = decision

    def record_decision_error(self, decision_id: str, error: str) -> None:
        """Mark a later execution/verification failure on the original opportunity."""
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be a nonempty error code or description")
        with self._lock:
            self._require_open()
            if decision_id not in self._decisions:
                raise ValueError("decision_id has not been recorded")
            decision = self._decisions[decision_id]
            self._emit("decision_error", self._now(), decision_id=decision_id,
                       error=error, ids=decision["ids"])
            decision["error"] = error

    def close(self) -> None:
        """Freeze the report endpoint. Active spans must exit before closing."""
        with self._lock:
            if self._end_ns is not None:
                return
            if any(span.end_ns is None for span in self._spans.values()):
                raise RuntimeError("cannot close trace with active spans")
            self._end_ns = self._now()
            try:
                self._emit("trace_end", self._end_ns)
            finally:
                if self._journal is not None:
                    self._journal.close()
                    self._journal = None

    def report(self) -> dict:
        """Snapshot wall time, measured stage time, gaps, and decision coverage.

        Per-stage union includes parents and can overlap other stages. Exclusive
        time excludes children. Concurrent leaves of different stages go into
        overlapping_stages_ns, so exclusive totals never double count wall time.
        Percentiles use completed-span wall durations, with linear interpolation;
        they are neither screenshot cadence nor provider-internal thinking time.
        Open spans contribute elapsed union time, but never a percentile sample.
        """
        with self._lock:
            end = self._end_ns if self._end_ns is not None else self._now()
            interruptions = {}
            for category, closed in self._interruptions.items():
                intervals = list(closed)
                if category in self._open_interruptions:
                    intervals.append((self._open_interruptions[category], end))
                interruptions[category] = _union(intervals)
            inactive = _union(interruptions["pause"] + interruptions["maintenance"])
            stages, totals = self._stage_times(end, inactive)
            wall = end - self._start_ns
            inactive_ns = _length(inactive)
            active = wall - inactive_ns
            decisions = list(self._decisions.values())
            local = sum(item["handled_locally"] for item in decisions)
            local_success = sum(item["handled_locally"] and item["error"] is None
                                for item in decisions)
            errors = Counter(item["error"] for item in decisions if item["error"])
            escalations = Counter(item["escalation_reason"] for item in decisions
                                  if not item["handled_locally"])
            event_counts = Counter(item["kind"] for item in self._events)
            retries = Counter(str(item["metadata"].get("reason", "unspecified"))
                              for item in self._events if item["kind"] == "retry")
            return {
                "schema": "veda.pipeline-trace-report.v1", "trace_id": self.trace_id,
                "unit": "nanoseconds", "clock": "monotonic", "ids": dict(self._defaults),
                "start_ns": self._start_ns, "end_ns": end,
                "closed": self._end_ns is not None, "total_wall_ns": wall,
                "active_ns": active, "pause_ns": _length(interruptions["pause"]),
                "maintenance_ns": _length(interruptions["maintenance"]),
                "pause_or_maintenance_ns": inactive_ns,
                "open_interruptions": sorted(self._open_interruptions),
                **totals,
                "unaccounted_active_ns": active - totals["active_stage_union_ns"],
                "stages": stages,
                "percentile_method": "linear interpolation of completed-span wall durations",
                "coverage": {"opportunities": len(decisions), "handled_locally": local,
                             "escalated": len(decisions) - local,
                             "fast_path_coverage": local / len(decisions) if decisions else None,
                             "successful_fast_path_coverage": (
                                 local_success / len(decisions) if decisions else None),
                             "decision_errors": sum(errors.values()),
                             "errors_by_reason": dict(errors),
                             "escalations_by_reason": dict(escalations)},
                "events": dict(event_counts), "retries_by_reason": dict(retries),
                "span_errors": dict(Counter(span.error for span in self._spans.values()
                                            if span.error is not None)),
            }

    def _stage_times(self, end: int, inactive: list[tuple[int, int]]) -> tuple[dict, dict]:
        starts: dict[int, list[str]] = defaultdict(list)
        finishes: dict[int, list[str]] = defaultdict(list)
        inactive_changes: Counter = Counter()
        stages = {}
        for span in self._spans.values():
            stages.setdefault(span.stage, {"union_ns": 0, "active_union_ns": 0,
                                          "exclusive_ns": 0, "exclusive_active_ns": 0})
            stop = span.end_ns if span.end_ns is not None else end
            if stop > span.start_ns:
                starts[span.start_ns].append(span.span_id)
                finishes[stop].append(span.span_id)
        for start, stop in inactive:
            inactive_changes[start] += 1
            inactive_changes[stop] -= 1
        points = sorted(set(starts) | set(finishes) | set(inactive_changes))
        active_spans: set[str] = set()
        inactive_count = 0
        totals = {"stage_union_ns": 0, "active_stage_union_ns": 0,
                  "overlapping_stages_ns": 0, "overlapping_active_stages_ns": 0}
        for left, right in zip(points, points[1:]):
            active_spans.difference_update(finishes.get(left, ()))
            active_spans.update(starts.get(left, ()))
            inactive_count += inactive_changes[left]
            if not active_spans:
                continue
            width = right - left
            is_active = inactive_count == 0
            totals["stage_union_ns"] += width
            if is_active:
                totals["active_stage_union_ns"] += width
            parents = set()
            for span_id in active_spans:
                parent = self._spans[span_id].parent_id
                while parent is not None:
                    parents.add(parent)
                    parent = self._spans[parent].parent_id
            for stage in {self._spans[span_id].stage for span_id in active_spans}:
                stages[stage]["union_ns"] += width
                if is_active:
                    stages[stage]["active_union_ns"] += width
            leaves = {self._spans[span_id].stage for span_id in active_spans - parents}
            if len(leaves) == 1:
                stage = stages[next(iter(leaves))]
                stage["exclusive_ns"] += width
                if is_active:
                    stage["exclusive_active_ns"] += width
            else:
                totals["overlapping_stages_ns"] += width
                if is_active:
                    totals["overlapping_active_stages_ns"] += width
        for name, stage in stages.items():
            relevant = [span for span in self._spans.values() if span.stage == name]
            samples = [span.end_ns - span.start_ns for span in relevant
                       if span.end_ns is not None]
            stage.update({"completed_samples": len(samples),
                          "in_flight_spans": sum(span.end_ns is None for span in relevant),
                          "sample_wall_sum_ns": sum(samples),
                          "p50_ns": _percentile(samples, .5),
                          "p95_ns": _percentile(samples, .95),
                          "max_ns": max(samples) if samples else None})
        return stages, totals
