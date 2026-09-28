"""Persistent play progress clocks, separate from controller authorization.

Create the tracker before preflight, arming or drafting so startup includes that
work. Begin a logical move before deciding/drafting it; navigation/focus is only
an intermediate verified input. Callers explicitly identify real user waits or
pauses. Unmarked inactivity, adapter restarts and model/tool delays count.

``pipeline_trace.TraceRecorder`` remains the fine-grained in-process span tool.
This module adds cross-process lifecycle budgets and reuses its percentile
calculation; it neither replaces span tracing nor estimates model thinking time.
Every target is a measured objective, never permission to omit a review, replay
an input, or change game state.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from .pipeline_trace import _percentile

SCHEMA = "veda.play-timing.v1"
TARGET_SECONDS = {"startup": 90, "move": 20, "noncombat": 90, "combat": 240, "elite": 360, "boss": 480}
STALE_RECAPTURE_LIMIT = 2
MAX_BYTES = 1_000_000
MAX_HISTORY = 128
PHASES = frozenset({"startup", "preflight", "capture", "inspection", "planning", "draft", "prepare",
                    "dispatch", "verification", "telemetry", "recovery", "idle"})
INTERMEDIATE_STEPS = frozenset({"focus", "target", "inspect", "navigation", "arm"})
_PROCESS_CLOCK_ID = str(uuid4())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, label, maximum=128):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
             and not any(ord(character) < 32 for character in value), f"invalid timing {label}")
    return value


def _timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             "timing timestamp requires a timezone")
    return value.astimezone(timezone.utc)


def _clock(monotonic_ns, clock_id):
    _require((monotonic_ns is None) == (clock_id is None), "monotonic timing needs both nanoseconds and a clock identity")
    if monotonic_ns is not None:
        _require(type(monotonic_ns) is int and monotonic_ns >= 0, "monotonic clock needs nonnegative integer nanoseconds")
        _text(clock_id, "clock_id")


def _scope(identifier, kind, at, **metadata):
    return {"id": identifier, "kind": kind, "started_at": at, "completed_at": None,
            "target_seconds": TARGET_SECONDS[kind], "active_seconds": 0.0, "excluded_seconds": 0.0,
            "phase_active_seconds": {}, "overrun_alerted": False, "stale_capture_ids": [],
            "stale_recapture_count": 0, "stale_budget_alerted": False, **metadata}


def begin_session(run_id, session_id=None, *, at=None, monotonic_ns=None, clock_id=None, startup_observed=True):
    """Create a new clock, only when no persisted session already exists."""
    _text(run_id, "run_id")
    session_id = _text(session_id or str(uuid4()), "session_id")
    _clock(monotonic_ns, clock_id)
    _require(type(startup_observed) is bool, "startup_observed must be explicit boolean")
    stamp = _timestamp(at or datetime.now(timezone.utc)).isoformat()
    return {"schema": SCHEMA, "run_id": run_id, "session_id": session_id, "started_at": stamp,
            "updated_at": stamp, "last_monotonic_ns": monotonic_ns, "clock_id": clock_id,
            "active_seconds": 0.0, "excluded_seconds": 0.0, "phase": "startup", "phase_active_seconds": {},
            "pause": None, "excluded_by_category": {"paused": 0.0, "user_wait": 0.0},
            "measurement_complete": True, "clock_issues": [], "clock_bridges": 0,
            "startup": _scope(session_id, "startup", stamp, observed_from_launch=startup_observed), "move": None, "floor": None,
            "completed_moves": [], "completed_floors": [], "completed_move_count": 0, "completed_floor_count": 0,
            "completed_move_overrun_count": 0, "completed_floor_overrun_count": 0,
            "verified_inputs": [], "verified_input_count": 0, "events": [], "poll_alerts": []}


def _copy_state(state):
    _require(isinstance(state, dict) and state.get("schema") == SCHEMA, "invalid play timing schema")
    _text(state.get("run_id"), "run_id")
    _text(state.get("session_id"), "session_id")
    raw = json.dumps(state, allow_nan=False)
    _require(len(raw.encode()) <= MAX_BYTES, "timing state exceeds byte bound")
    return json.loads(raw)


def _issue(state, code):
    state["measurement_complete"] = False
    if code not in state["clock_issues"]:
        state["clock_issues"].append(code)
    state["clock_issues"] = state["clock_issues"][-8:]


def _advance(state, at, monotonic_ns, clock_id):
    _clock(monotonic_ns, clock_id)
    now = _timestamp(at or datetime.now(timezone.utc))
    wall_delta = (now - _timestamp(state["updated_at"])).total_seconds()
    continuity = (clock_id is not None and clock_id == state["clock_id"]
                  and state["last_monotonic_ns"] is not None)
    if continuity and monotonic_ns >= state["last_monotonic_ns"]:
        elapsed = (monotonic_ns - state["last_monotonic_ns"]) / 1_000_000_000
        # Monotonic continuity measures this interval even when civil time jumps.
        if abs(elapsed - wall_delta) > 5:
            if "wall_clock_changed_monotonic_used" not in state["clock_issues"]:
                state["clock_issues"].append("wall_clock_changed_monotonic_used")
    elif continuity:
        elapsed = max(0, wall_delta)
        _issue(state, "monotonic_clock_moved_backwards_wall_interval_used")
    elif wall_delta < 0:
        elapsed = 0.0
        _issue(state, "wall_clock_rollback_interval_unmeasured")
    else:
        elapsed = wall_delta
        if clock_id != state["clock_id"]:
            state["clock_bridges"] += 1
    _require(math.isfinite(elapsed), "invalid elapsed timing interval")
    paused = state["pause"] is not None
    field = "excluded_seconds" if paused else "active_seconds"
    state[field] += elapsed
    if paused:
        state["excluded_by_category"][state["pause"]["category"]] += elapsed
    else:
        state["phase_active_seconds"][state["phase"]] = state["phase_active_seconds"].get(state["phase"], 0) + elapsed
    for name in ("startup", "move", "floor"):
        scope = state[name]
        if scope is not None and scope["completed_at"] is None:
            scope[field] += elapsed
            if not paused:
                scope["phase_active_seconds"][state["phase"]] = scope["phase_active_seconds"].get(state["phase"], 0) + elapsed
    state.update(updated_at=now.isoformat(), last_monotonic_ns=monotonic_ns, clock_id=clock_id)


def _complete_move(state, move_id, action_id):
    move = state["move"]
    _require(move is not None and move["id"] == move_id, "no matching pending logical move")
    proof = next((item for item in state["verified_inputs"] if item["action_id"] == action_id), None)
    _require(proof is not None and proof["move_id"] == move_id and proof["step_kind"] not in INTERMEDIATE_STEPS,
             "logical completion needs a matching verified non-navigation outcome")
    move.update(completed_at=state["updated_at"], completion_action_id=action_id)
    state["completed_moves"].append(move)
    state["completed_moves"] = state["completed_moves"][-MAX_HISTORY:]
    state["completed_move_count"] += 1
    state["completed_move_overrun_count"] += move["active_seconds"] > move["target_seconds"]
    state["move"] = None


def record_event(state, event, *, at=None, monotonic_ns=None, clock_id=None):
    """Return an updated copy; persist it atomically before accepting another event.

    Required event ``operation`` is one of begin_move, start_floor/begin_floor,
    phase, verified_input, complete_move, complete_floor, pause, resume,
    stale_capture, resume_session, or tick. verified_input may set move_complete
    only for a verified logical outcome, never a focus/target/inspection step.
    A tracker provides diagnostics; the caller still owns all game validation.
    """
    value = _copy_state(state)
    _require(isinstance(event, dict), "timing event must be an object")
    operation = event.get("operation")
    allowed = {
        "begin_move": {"move_id", "kind", "label"}, "start_floor": {"floor_id", "kind", "observed_from_entry"},
        "begin_floor": {"floor_id", "kind", "observed_from_entry"},
        "phase": {"name"}, "verified_input": {"action_id", "step_kind", "move_complete"},
        "complete_move": {"move_id", "action_id"}, "complete_floor": {"floor_id"},
        "pause": {"category", "reason"}, "resume": set(), "stale_capture": {"capture_id"},
        "failure": {"code", "reason"}, "overrun": {"reason"},
        "measurement_gap": {"reason"},
        "resume_session": set(), "tick": set()}
    _require(operation in allowed and not set(event) - allowed[operation] - {"operation"}, "unsupported timing event or fields")
    _advance(value, at, monotonic_ns, clock_id)
    if operation == "begin_move":
        move_id = _text(event.get("move_id"), "move_id")
        kind = _text(event.get("kind"), "move kind")
        label = _text(event.get("label", kind), "move label", 512)
        if value["move"] is not None:
            _require(value["move"]["id"] == move_id and value["move"]["move_kind"] == kind,
                     "pending logical move must not be reset or replaced")
        else:
            _require(not any(item["id"] == move_id for item in value["completed_moves"]), "logical move already completed")
            value["move"] = _scope(move_id, "move", value["updated_at"], move_kind=kind, label=label,
                                    floor_id=value["floor"]["id"] if value["floor"] else None)
    elif operation in {"start_floor", "begin_floor"}:
        floor_id, kind = _text(event.get("floor_id"), "floor_id"), event.get("kind")
        _require(kind in {"noncombat", "combat", "elite", "boss"}, "unknown floor timing kind")
        observed = event.get("observed_from_entry", False)
        _require(type(observed) is bool, "observed_from_entry must be explicit boolean")
        if value["floor"] is not None:
            _require(value["floor"]["id"] == floor_id and value["floor"]["kind"] == kind,
                     "pending floor must be completed before changing floor")
        else:
            _require(not any(item["id"] == floor_id for item in value["completed_floors"]), "floor already completed")
            value["floor"] = _scope(floor_id, kind, value["updated_at"], observed_from_entry=observed)
    elif operation == "phase":
        _require(event.get("name") in PHASES, "unknown timing phase")
        value["phase"] = event["name"]
    elif operation == "verified_input":
        action_id = _text(event.get("action_id"), "action_id")
        step_kind = _text(event.get("step_kind"), "step_kind")
        complete = event.get("move_complete", False)
        _require(type(complete) is bool, "move_complete must be an explicit boolean")
        _require(not complete or step_kind not in INTERMEDIATE_STEPS, "focus/target/inspection is not a completed logical move")
        existing = next((item for item in value["verified_inputs"] if item["action_id"] == action_id), None)
        if existing is not None:
            _require(existing["step_kind"] == step_kind and existing["move_complete"] == complete,
                     "verified timing action replay differs")
        else:
            _require(not complete or value["move"] is not None, "begin logical move before reporting its completion")
            move_id = value["move"]["id"] if value["move"] else None
            value["verified_inputs"].append({"action_id": action_id, "step_kind": step_kind, "move_complete": complete,
                                             "move_id": move_id, "verified_at": value["updated_at"]})
            value["verified_inputs"] = value["verified_inputs"][-MAX_HISTORY:]
            value["verified_input_count"] += 1
            if value["startup"]["completed_at"] is None:
                value["startup"].update(completed_at=value["updated_at"], first_verified_action_id=action_id)
            if complete:
                _complete_move(value, move_id, action_id)
    elif operation == "complete_move":
        _complete_move(value, event.get("move_id"), event.get("action_id"))
    elif operation == "complete_floor":
        floor = value["floor"]
        _require(floor is not None and floor["id"] == event.get("floor_id"), "no matching active floor")
        _require(value["move"] is None, "pending logical move must complete before floor timing closes")
        floor["completed_at"] = value["updated_at"]
        value["completed_floors"].append(floor)
        value["completed_floors"] = value["completed_floors"][-MAX_HISTORY:]
        value["completed_floor_count"] += 1
        value["completed_floor_overrun_count"] += floor["active_seconds"] > floor["target_seconds"]
        value["floor"] = None
    elif operation == "pause":
        category = event.get("category")
        _require(category in {"paused", "user_wait"}, "pause category must be paused or user_wait")
        reason = _text(event.get("reason"), "pause reason", 512)
        _require(value["pause"] is None, "already paused; do not exclude overlapping time twice")
        value["pause"] = {"category": category, "reason": reason, "started_at": value["updated_at"]}
    elif operation == "resume":
        _require(value["pause"] is not None, "resume requires an explicit pause/user wait")
        value["pause"] = None
    elif operation == "stale_capture":
        capture_id = _text(event.get("capture_id"), "capture_id", 256)
        scope = value["move"] or (value["startup"] if value["startup"]["completed_at"] is None else value["floor"])
        _require(scope is not None, "stale capture requires a current move, startup, or floor scope")
        if capture_id not in scope["stale_capture_ids"]:
            scope["stale_recapture_count"] += 1
            scope["stale_capture_ids"] = (scope["stale_capture_ids"] + [capture_id])[-MAX_HISTORY:]
    elif operation == "measurement_gap":
        _issue(value, _text(event.get("reason"), "measurement gap"))
    elif operation in {"failure", "overrun"}:
        if operation == "failure":
            _text(event.get("code"), "failure code")
        _text(event.get("reason"), "failure/overrun reason", 512)
    # resume_session and tick deliberately do not reset scopes or clear pauses.
    if operation != "tick":
        value["events"].append({"at": value["updated_at"], **deepcopy(event)})
        value["events"] = value["events"][-MAX_HISTORY:]
    return value


def _scope_summary(scope, complete_measurement):
    if scope is None:
        return None
    result = deepcopy(scope)
    result.pop("stale_capture_ids", None)
    active = scope["active_seconds"]
    complete_measurement = (complete_measurement and scope.get("observed_from_launch", True)
                            and scope.get("observed_from_entry", True))
    result.update(over_target=True if active > scope["target_seconds"] else False if complete_measurement else None,
                  remaining_seconds=max(0, scope["target_seconds"] - active) if complete_measurement else None,
                  measurement_basis="measured" if complete_measurement else "incomplete_elapsed_lower_bound",
                  stale_recapture_limit=STALE_RECAPTURE_LIMIT,
                  stale_budget_exhausted=scope["stale_recapture_count"] >= STALE_RECAPTURE_LIMIT)
    if scope["kind"] in {"noncombat", "combat", "elite", "boss"}:
        result["full_floor_target_verifiable"] = complete_measurement and scope.get("observed_from_entry", False)
    return result


def _diagnostics(state):
    result = []
    if state["startup"].get("observed_from_launch") is False:
        result.append({"code": "startup_measurement_started_late",
                       "recommendation": "Report startup duration as unavailable; create timing before the next launcher/preflight so setup and arming are measured."})
    for name in ("startup", "move", "floor"):
        scope = state[name]
        if scope is None:
            continue
        if scope["active_seconds"] > scope["target_seconds"]:
            dominant = max(scope["phase_active_seconds"], key=scope["phase_active_seconds"].get, default="unmeasured")
            result.append({"code": "target_exceeded", "scope": name, "scope_id": scope["id"],
                           "active_seconds": scope["active_seconds"], "target_seconds": scope["target_seconds"],
                           "dominant_phase": dominant,
                           "recommendation": "Use the prepared compact helper and existing context; finish source-free validation before the next fresh capture. Preserve every input and result check."})
        if scope["stale_recapture_count"] >= STALE_RECAPTURE_LIMIT:
            result.append({"code": "stale_recapture_budget_exhausted", "scope": name, "scope_id": scope["id"],
                           "count": scope["stale_recapture_count"], "limit": STALE_RECAPTURE_LIMIT,
                           "recommendation": "Do not repeat the same capture loop. Resolve the draft/helper delay, retain any pending input, then obtain and inspect fresh evidence. Never replay an uncertain input."})
    if not state["measurement_complete"]:
        result.append({"code": "elapsed_measurement_incomplete", "issues": state["clock_issues"],
                       "recommendation": "Retain elapsed totals and pending scope IDs; report incomplete timing until the clock discontinuity is reconciled. Do not reset a move to hide the gap."})
    return result


def summarize_timing(state, *, at=None, monotonic_ns=None, clock_id=None):
    """JSON-ready snapshot; no automatic action, no deadline-based authorization."""
    value = _copy_state(state)
    if at is not None or monotonic_ns is not None or clock_id is not None:
        _advance(value, at, monotonic_ns, clock_id)
    moves = [item["active_seconds"] for item in value["completed_moves"]]
    return {"schema": "veda.play-timing-summary.v1", "run_id": value["run_id"], "session_id": value["session_id"],
            "started_at": value["started_at"], "updated_at": value["updated_at"], "phase": value["phase"],
            "active_seconds": value["active_seconds"], "excluded_seconds": value["excluded_seconds"],
            "excluded_by_category": deepcopy(value["excluded_by_category"]), "pause": deepcopy(value["pause"]),
            "phase_active_seconds": deepcopy(value["phase_active_seconds"]), "targets_seconds": dict(TARGET_SECONDS),
            "measurement_complete": value["measurement_complete"], "clock_issues": value["clock_issues"],
            "clock_bridges": value["clock_bridges"],
            **{name: _scope_summary(value[name], value["measurement_complete"]) for name in ("startup", "move", "floor")},
            "completed_move_count": value["completed_move_count"], "completed_floor_count": value["completed_floor_count"],
            "completed_move_overrun_count": value["completed_move_overrun_count"],
            "completed_floor_overrun_count": value["completed_floor_overrun_count"],
            "verified_input_count": value["verified_input_count"],
            "recent_completed_move_seconds": {"samples": len(moves), "p50": _percentile(moves, .5), "p95": _percentile(moves, .95),
                                               "window": f"last {MAX_HISTORY} completed logical moves; excludes declared pauses/waits"},
            "diagnostics": _diagnostics(value), "controller_authorized": False, "runtime_authorized": False,
            "recent_failures": [deepcopy(event) for event in value["events"] if event["operation"] in {"failure", "overrun"}][-5:],
            "automatic_input": False, "targets_are_safety_overrides": False}


class PlayTiming:
    """Small flock/atomic-file adapter shared by launcher, helpers and play loop.

    Opening an existing file resumes it; it never begins a new session merely
    because a process restarted. A conflicting run/session identity raises.
    ``poll`` persists elapsed clocks and returns each scope's overrun/stale alert
    once, including when no action method is being called. Callers choose when
    and how to display those diagnostics; this object cannot control gameplay.
    """
    def __init__(self, path, *, run_id, session_id=None, clock=None, monotonic_clock=None, clock_id=None, startup_observed=True):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id, self.session_id = _text(run_id, "run_id"), session_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic_clock = monotonic_clock or time.monotonic_ns
        self.clock_id = clock_id or _PROCESS_CLOCK_ID
        with self._lock():
            if self.path.exists():
                value = self._read()
                self.session_id = value["session_id"]
            else:
                value = begin_session(run_id, session_id, startup_observed=startup_observed, **self._now())
                self.session_id = value["session_id"]
                self._write(value)

    def _now(self):
        return {"at": self.clock(), "monotonic_ns": self.monotonic_clock(), "clock_id": self.clock_id}

    def _lock(self):
        class Locked:
            def __init__(inner, path):
                inner.path = path
            def __enter__(inner):
                inner.stream = inner.path.open("a+")
                fcntl.flock(inner.stream.fileno(), fcntl.LOCK_EX)
                return inner
            def __exit__(inner, *args):
                fcntl.flock(inner.stream.fileno(), fcntl.LOCK_UN)
                inner.stream.close()
        return Locked(self.path.with_name(self.path.name + ".lock"))

    def _read(self):
        with self.path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        _require(len(raw) <= MAX_BYTES, "timing file exceeds byte bound")
        value = _copy_state(json.loads(raw))
        _require(value["run_id"] == self.run_id and (self.session_id is None or value["session_id"] == self.session_id),
                 "timing file belongs to another run/session")
        return value

    def _write(self, value):
        payload = json.dumps(_copy_state(value), sort_keys=True, allow_nan=False).encode()
        temporary = self.path.with_name(".timing-" + uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def snapshot(self):
        with self._lock():
            return self._read()

    def event(self, operation, **fields):
        return self.record({"operation": operation, **fields})

    def record(self, event):
        with self._lock():
            value = record_event(self._read(), event, **self._now())
            self._write(value)
            return summarize_timing(value)

    def summary(self):
        with self._lock():
            return summarize_timing(self._read(), **self._now())

    def poll(self):
        with self._lock():
            value = record_event(self._read(), {"operation": "tick"}, **self._now())
            alerts = []
            for diagnostic in _diagnostics(value):
                if diagnostic["code"] not in {"target_exceeded", "stale_recapture_budget_exhausted"}:
                    continue
                scope = value[diagnostic["scope"]]
                flag = "overrun_alerted" if diagnostic["code"] == "target_exceeded" else "stale_budget_alerted"
                if not scope[flag]:
                    alerts.append(diagnostic)
                    scope[flag] = True
            value["poll_alerts"] = alerts
            self._write(value)
            return {**summarize_timing(value), "new_alerts": alerts}
