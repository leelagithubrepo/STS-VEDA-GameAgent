"""Persistent observe/check/input/verify runtime. Importing never starts gameplay.

Replay and a future armed run share this loop. Recognition is an explicit
adapter, not invented state. No plan crosses a draw/effect/turn boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from .advisory import check_plan
from .calibration import CalibrationReport, COMBAT_CRITICAL_FIELDS
from .controller_state_machine import ControllerStateMachine
from .combat_input import CombatInputAdapter, RuntimeStop, _state_key
from .pipeline_trace import TraceRecorder
from .routine_combat import plan_routine_combat, routine_observation_reasons
from .runtime_frames import RuntimeFrame, snapshot_frame, retain_snapshot
from .vision import StructuredGameState

RUNTIME_FIELDS = COMBAT_CRITICAL_FIELDS | frozenset({
    "hand_complete", "hand_details", "player_strength", "player_weak", "player_frail",
    "advisory_context", "ui_phase", "focused_card_id", "selected_card_id", "focused_target_id",
    "hand_order", "target_order",
})
ARM_PHRASE = "ARM ORCHESTRATOR FOR THIS RUN"


@dataclass(frozen=True)
class Reading:
    frame_id: str
    image_sha256: str
    state: StructuredGameState
    context: dict
    ui: dict
    encounter_name: str | None
    run_id: str
    floor_id: str
    turn_id: str

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "state": StructuredGameState.from_dict(data["state"])})


class ActionJournal:
    """Small durable journal before/after input; never generates public reports."""
    TERMINAL = {"verified", "not_sent", "reconciled"}

    def __init__(self, path: Path | None = None):
        self.path, self.rows = Path(path) if path is not None else None, []
        self._directory_synced = False
        self._directory_sync_paths = None
        if self.path and self.path.exists():
            try:
                self.rows = [json.loads(line) for line in self.path.read_text().splitlines() if line]
            except (ValueError, OSError) as error:
                raise RuntimeStop("action journal is unreadable; reconciliation required") from error

    def unresolved(self):
        states = {}
        for row in self.rows:
            if row.get("action_id"):
                states[row["action_id"]] = row["status"]
        return [key for key, status in states.items() if status not in self.TERMINAL]

    def append(self, *, status, action_id, **data):
        row = {"status": status, "action_id": action_id,
               "recorded_at": datetime.now(timezone.utc).isoformat(), **data}
        if self.path:
            # Record missing parents before creation so their own directory
            # entries are also made durable before the first possible input.
            if self._directory_sync_paths is None:
                created_parents = []
                parent = self.path.parent
                while not parent.exists():
                    created_parents.append(parent)
                    parent = parent.parent
                self._directory_sync_paths = list(dict.fromkeys(
                    [self.path.parent] + [p.parent for p in created_parents]))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if not self._directory_synced:
                for directory in self._directory_sync_paths:
                    descriptor = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                self._directory_synced = True
        self.rows.append(row)




class ExecutionLoop(CombatInputAdapter):
    def __init__(self, source, interpreter, controller, *, calibration: CalibrationReport,
                 run_id: str, mode="replay", arm=None, trace=None, journal=None,
                 max_inputs=100, max_seconds=120, max_frame_age_seconds=5,
                 clock=time.monotonic_ns, should_stop=lambda: False, planner=plan_routine_combat,
                 evidence_directory: Path | None = None, authorization=None):
        if mode not in {"replay", "shadow", "live"}:
            raise ValueError("invalid runtime mode")
        if (not run_id or isinstance(max_inputs, bool) or not isinstance(max_inputs, int)
                or max_inputs < 1 or not math.isfinite(max_seconds) or max_seconds <= 0
                or not math.isfinite(max_frame_age_seconds) or max_frame_age_seconds <= 0):
            raise ValueError("bounded runtime limits and run identity required")
        if mode == "live" and arm != ARM_PHRASE:
            raise RuntimeStop("separate per-run arming is required before live operation")
        if mode == "live":
            from .runtime_authorization import RuntimeAuthorization
            if not isinstance(authorization, RuntimeAuthorization):
                raise RuntimeStop("live operation needs independent reader-bound authorization")
            authorization.assert_runtime(reader_command=getattr(interpreter, "command", None),
                reader_identity=getattr(interpreter, "recognizer_identity", None),
                source_kind=getattr(source, "provenance", None))
            if evidence_directory is None:
                raise RuntimeStop("live operation requires a durable evidence directory")
            if not isinstance(journal, ActionJournal) or journal.path is None:
                raise RuntimeStop("live operation requires a persistent action journal")
            calibration = authorization.calibration
        if mode != "live" and not getattr(controller, "offline", False):
            raise RuntimeStop("offline modes require an offline controller")
        self.source, self.interpreter, self.controller = source, interpreter, controller
        self.calibration, self.run_id, self.mode = calibration, run_id, mode
        self.trace = trace or TraceRecorder(clock=clock, run_id=run_id)
        self.journal = journal or ActionJournal()
        self.clock, self.should_stop, self.planner = clock, should_stop, planner
        self.max_inputs, self.max_seconds = max_inputs, max_seconds
        self.max_frame_age_ns = int(max_frame_age_seconds * 1e9)
        self.seen_frames = set()
        self.machine = ControllerStateMachine()
        self.inputs = 0
        self.decisions = 0
        self.previous = None
        self.last_frame = None
        self.current_decision_id = None
        self.decision_was_local = False
        self.evidence_directory = evidence_directory
        self.deadline_ns = None
        self.authorization = authorization
        self.evidence_errors = []

    def _read(self, *, purpose, after_ns=None):
        if self.mode == "live":
            self.authorization.assert_runtime(reader_command=getattr(self.interpreter, "command", None),
                reader_identity=getattr(self.interpreter, "recognizer_identity", None),
                source_kind=getattr(self.source, "provenance", None))
        for attempt in range(2):
            try:
                with self.trace.span("capture", purpose=purpose) as capture_span:
                    frame = self.source.next_frame()
                    capture_span.annotate(frame_id=frame.frame_id, sha256=frame.sha256)
                break
            except (OSError, TimeoutError) as error:
                self.trace.event("retry", stage="capture", attempt=attempt + 1,
                                 error=type(error).__name__)
                if attempt:
                    raise RuntimeStop("capture failed after one retry") from error
        if self.mode == "live":
            self.authorization.assert_frame_source(frame.source)
        if (frame.frame_id in self.seen_frames or frame.acquired_ns > self.clock()
                or self.clock() - frame.acquired_ns > self.max_frame_age_ns
                or after_ns is not None and frame.acquired_ns <= after_ns):
            raise RuntimeStop("stale, reused, or out-of-order frame")
        with self.trace.span("image_preparation", frame_id=frame.frame_id):
            if hashlib.sha256(frame.image_path.read_bytes()).hexdigest() != frame.sha256:
                raise RuntimeStop("frame bytes do not match their evidence identity")
        self.seen_frames.add(frame.frame_id)
        self.last_frame = frame
        self.trace.event("frame", **frame.identity(), purpose=purpose)
        with self.trace.span("state_extraction", frame_id=frame.frame_id) as extraction_span:
            reading = self.interpreter.read(frame, purpose=purpose, previous=self.previous)
            with self.trace.span("parsing", frame_id=frame.frame_id):
                if not isinstance(reading, Reading):
                    reading = Reading.from_dict(reading)
            extraction_span.annotate(run_id=reading.run_id, floor_id=reading.floor_id,
                                     turn_id=reading.turn_id)
        if reading.frame_id != frame.frame_id or reading.image_sha256 != frame.sha256:
            raise RuntimeStop("recognition result references a different frame")
        if reading.run_id != self.run_id:
            raise RuntimeStop("run identity changed")
        if reading.state.screen_type == "COMBAT" and reading.ui.get("phase") != "animating":
            with self.trace.span("state_consistency", frame_id=frame.frame_id):
                reasons = routine_observation_reasons(reading.state, reading.context)
                if reasons:
                    raise RuntimeStop("settled combat reading is inconsistent: " + "; ".join(reasons))
        self.previous = reading
        return reading

    def _choose(self, reading):
        decision_id = uuid4().hex
        self.current_decision_id = decision_id
        self.decision_was_local = False
        self.decisions += 1
        with self.trace.span("rule_validation", frame_id=reading.frame_id,
                             decision_id=decision_id, floor_id=reading.floor_id,
                             turn_id=reading.turn_id):
            if not self.calibration.authorized_for(RUNTIME_FIELDS):
                reason = "recognition has not earned runtime field authorization"
                self.trace.record_decision(decision_id, handled_locally=False, escalation_reason=reason)
                raise RuntimeStop(reason)
        with self.trace.span("planning", decision_id=decision_id, frame_id=reading.frame_id):
            plan = self.planner(reading.state, self.calibration,
                                encounter_name=reading.encounter_name, context=reading.context)
        action = getattr(plan, "next_action", None)
        if not plan.ready or action is None:
            reasons = list(plan.reasons) or ["no supported safe local decision"]
            self.trace.record_decision(decision_id, handled_locally=False,
                                       escalation_reason="; ".join(reasons))
            raise RuntimeStop("; ".join(reasons))
        checked = check_plan(reading.context, {"steps": [action]})
        if not checked.get("allowed"):
            reason = "; ".join(checked.get("reasons", ["shared validation rejected action"]))
            self.trace.record_decision(decision_id, handled_locally=False, escalation_reason=reason)
            raise RuntimeStop(reason)
        self.trace.record_decision(decision_id, handled_locally=True)
        self.decision_was_local = True
        return action, checked

    def _retain(self, snapshot):
        with self.trace.span("action_evidence", frame_id=snapshot.frame.frame_id):
            return retain_snapshot(snapshot, self.evidence_directory)

    def _execute(self, reading, action, checked, expected, command, ids):
        """Retain consequential evidence, then measure input and verification.

        A single bounded byte snapshot protects the before-frame during cache
        eviction. Successful focus-only navigation does not archive its frames;
        mismatched or uncertain inputs retain both available sides. Evidence
        records never change an action's verified/unknown/not-sent status.
        """
        action_id = command["request_id"]
        before_frame = self.last_frame
        before_snapshot = None
        before_identity = {**before_frame.identity(), "durable": False}
        consequential = expected["kind"] in {"advance", "end_turn"}
        attempted = False
        try:
            with self.trace.span("input_verification", **ids):
                if self.evidence_directory is not None:
                    before_snapshot = snapshot_frame(before_frame)
                    if consequential:
                        # Do not send an input whose durable before-evidence failed.
                        before_identity = self._retain(before_snapshot)
                with self.trace.span("telemetry", **ids):
                    self.journal.append(status="attempted", action_id=action_id, command=command,
                                        frame=before_identity, decision=action,
                                        reading=asdict(reading))
                    attempted = True
                # Logging/retention can take time; check again at the send boundary.
                if (self.should_stop() or self.clock() - before_frame.acquired_ns > self.max_frame_age_ns
                        or self.deadline_ns is not None and self.clock() >= self.deadline_ns):
                    self.journal.append(status="not_sent", action_id=action_id,
                                        reason="stop, stale frame, or wall-clock limit before send")
                    raise RuntimeStop("stop, stale frame, or wall-clock limit before send")
                with self.trace.span("controller_round_trip", **ids):
                    result = self.controller.call(command)
                completed_ns = self.clock()
                self.inputs += 1
                if result.get("status") != "ok" or result.get("request_id") != action_id:
                    status = ("not_sent" if result.get("status") == "not_sent"
                              and result.get("request_id") == action_id else "unknown_outcome")
                    self.journal.append(status=status, action_id=action_id, result=result)
                    if status != "not_sent":
                        with self.trace.span("recovery", **ids):
                            self._read(purpose="uncertain_input_reconciliation", after_ns=completed_ns)
                    raise RuntimeStop("controller result is uncertain or rejected; no automatic replay")
                with self.trace.span("animation_verification", **ids):
                    for verification_attempt in range(3):
                        after = self._read(purpose="ui_verification", after_ns=completed_ns)
                        if after.ui.get("phase") != "animating":
                            break
                        self.trace.event("retry", reason="animation_pending", attempt=verification_attempt + 1,
                                         action_id=action_id, frame_id=after.frame_id)
                    else:
                        raise RuntimeStop("animation did not settle within verification budget")
                    resolved = self._verify(reading, after, action, expected, checked)
                after_identity = {**self.last_frame.identity(), "durable": False}
                if consequential and self.evidence_directory is not None:
                    # A sent action is not marked verified if its after-evidence fails.
                    after_identity = self._retain(snapshot_frame(self.last_frame))
                with self.trace.span("telemetry", **ids):
                    self.journal.append(status="verified", action_id=action_id,
                                        frame=after_identity, resolved=resolved,
                                        reading=asdict(after))
            return after, resolved
        except Exception:
            if attempted and before_snapshot is not None:
                # Diagnostic evidence is correlated without becoming an action
                # state: it must not reopen a confirmed not-sent action.
                retained = {}
                for side, frame in (("before", before_frame), ("after", self.last_frame)):
                    if side == "after" and frame.frame_id == before_frame.frame_id:
                        continue
                    try:
                        snapshot = before_snapshot if side == "before" else snapshot_frame(frame)
                        retained[side] = self._retain(snapshot)
                    except Exception as error:
                        self.evidence_errors.append({"action_id": action_id, "side": side,
                                                     "error": type(error).__name__})
                if retained:
                    try:
                        self.journal.append(status="action_evidence", action_id=None,
                                            related_action_id=action_id, frames=retained)
                    except Exception as error:
                        self.evidence_errors.append({"action_id": action_id, "side": "journal",
                                                     "error": type(error).__name__})
            raise

    def run(self):
        began = self.clock()
        self.deadline_ns = began + int(self.max_seconds * 1e9)
        reason, outcome = "input budget reached", "stopped"
        action = checked = None
        pending_id = None
        cleanup_errors, retained_evidence = [], None
        try:
            if self.should_stop():
                raise RuntimeStop("user stop requested")
            reading = self._read(purpose="reconcile" if self.journal.unresolved() else "decision")
            if self.journal.unresolved():
                raise RuntimeStop("unresolved prior input; fresh evidence retained for explicit reconciliation")
            while self.inputs < self.max_inputs:
                if self.should_stop():
                    raise RuntimeStop("user stop requested")
                if (self.clock() - began) / 1e9 >= self.max_seconds:
                    raise RuntimeStop("runtime wall-clock budget reached")
                if self.clock() - self.last_frame.acquired_ns > self.max_frame_age_ns:
                    raise RuntimeStop("frame became stale before input")
                if action is None:
                    action, checked = self._choose(reading)
                with self.trace.span("local_ui_planning", frame_id=reading.frame_id):
                    command, expected = self._input(reading, action)
                if len(command["buttons"]) != 1:
                    raise RuntimeStop("only one atomic input is permitted")
                if self.mode == "shadow":
                    reason, outcome = "shadow proposal recorded; no input sent", "shadow"
                    self.trace.event("shadow_proposal", action=action, command=command)
                    break
                pending_id = str(uuid4())
                command = {"action": "tap", "buttons": command["buttons"], "request_id": pending_id}
                ids = dict(action_id=pending_id, request_id=pending_id, frame_id=reading.frame_id,
                           run_id=reading.run_id, floor_id=reading.floor_id, turn_id=reading.turn_id,
                           decision_id=self.current_decision_id)
                after, resolved = self._execute(reading, action, checked, expected, command, ids)
                pending_id = None
                reading = after
                if resolved:
                    action = checked = None
                    self.current_decision_id = None
                    self.decision_was_local = False
                    self.machine.reset()
            else:
                reason = "input budget reached"
        except (RuntimeStop, StopIteration, ValueError, KeyError, TypeError, OSError, TimeoutError) as error:
            reason = str(error) or "saved frame stream exhausted"
            self.trace.event("stop", reason=reason, error=type(error).__name__)
            if self.current_decision_id and self.decision_was_local:
                self.trace.record_decision_error(self.current_decision_id, reason)
            elif self.current_decision_id is None and self.last_frame is not None:
                # A received but unusable decision frame is an escalation, not
                # a missing opportunity that would inflate local coverage.
                self.decisions += 1
                self.trace.record_decision(uuid4().hex, handled_locally=False,
                    escalation_reason=reason, frame_id=self.last_frame.frame_id)
            if pending_id and pending_id in self.journal.unresolved():
                try:
                    self.journal.append(status="unknown_outcome", action_id=pending_id, reason=reason)
                except Exception as journal_error:
                    # The durable attempted row is already unresolved. A full
                    # disk must not prevent cleanup or the structured report.
                    self.evidence_errors.append({"action_id": pending_id, "side": "journal",
                                                 "error": type(journal_error).__name__})
        finally:
            if self.last_frame and self.evidence_directory and hasattr(self.source, "retain"):
                try:
                    with self.trace.span("boundary_evidence", frame_id=self.last_frame.frame_id):
                        retained_evidence = str(self.source.retain(self.last_frame, self.evidence_directory))
                except Exception as error:
                    cleanup_errors.append("evidence retention: " + type(error).__name__)
            for adapter in (self.controller, self.interpreter, self.source):
                try:
                    adapter.close()
                except Exception as error:
                    self.trace.event("error", stage="cleanup", error=type(error).__name__)
                    cleanup_errors.append(type(adapter).__name__ + ": " + type(error).__name__)
                    outcome = "stopped"
        self.trace.close()
        return {"mode": self.mode, "outcome": outcome, "reason": reason,
                "inputs": self.inputs, "decisions": self.decisions,
                "unresolved_actions": self.journal.unresolved(), "metrics": self.trace.report(),
                "cleanup_errors": cleanup_errors, "retained_evidence": retained_evidence,
                "evidence_errors": self.evidence_errors,
                "measurement_basis": "offline adapter execution" if self.mode != "live" else "live runtime",
                "live_performance_validated": False}
