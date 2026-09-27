"""Count standalone helper failures against an existing play clock only.

No clock/session is created here. Failure accounting cannot grant input authority,
change a pending move, pause a clock, or extend a source freshness deadline.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

from .play_timing import MAX_BYTES, SCHEMA, record_event, summarize_timing

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_HELPER_CLOCK_ID = "helper-" + str(uuid4())


class _AccountingError(ValueError):
    """Only fixed diagnostic codes leave failure-accounting internals."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _AccountingError("helper_timing_duplicate_json_key")
        result[key] = value
    return result


def _read_json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise _AccountingError("helper_timing_file_too_large")
    return json.loads(raw, object_pairs_hook=_pairs)


def _run_id(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 128
            and not any(ord(character) < 32 for character in value))


def _session_run(session_path):
    path = _path(session_path)
    value = _read_json(path / "state.json" if path.is_dir() else path)
    if not isinstance(value, dict) or value.get("schema") != "veda.reviewed-play.v1" or not _run_id(value.get("run_id")):
        raise _AccountingError("reviewed_session_identity_unavailable")
    return value["run_id"]


def _path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip() or len(str(value)) > 4096:
        raise ValueError("helper timing path is invalid")
    return Path(value).expanduser().resolve()


def _candidate(run_id, session_path, output_path):
    candidates = []
    if session_path is not None:
        session = _path(session_path)
        candidates.append((session if session.is_dir() else session.parent) / "timing.json")
    if output_path is not None:
        candidates.append(_path(output_path).parent / "timing.json")
    # Only a parsed, canonical UUID can become part of a project-relative path.
    # Explicit session/output evidence still supports a matching synthetic ID.
    try:
        canonical = str(UUID(run_id))
    except (ValueError, AttributeError, TypeError):
        canonical = None
    if canonical is not None:
        candidates.append(PROJECT_ROOT / "artifacts" / "reviewed-play" / canonical / "timing.json")
    for path in dict.fromkeys(candidates):
        if path.is_file():
            return path
    return None


def _read_existing(path, run_id):
    value = _read_json(path)
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("run_id") != run_id:
        raise _AccountingError("existing_play_clock_run_mismatch")
    return value


def _write_existing(path, state):
    payload = json.dumps(state, sort_keys=True, allow_nan=False).encode()
    if len(payload) > MAX_BYTES:
        raise ValueError("helper timing result exceeds byte bound")
    # Caller holds the existing clock lock and has validated this exact run.
    temporary = path.with_name(".helper-timing-" + uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _compact(state, *, stale_accounting=None):
    summary = summarize_timing(state)
    scopes = {name: {key: summary[name][key] for key in
                    ("id", "active_seconds", "target_seconds", "over_target", "stale_recapture_count", "stale_budget_exhausted")}
              for name in ("startup", "move", "floor") if summary[name] is not None}
    value = {"schema": "veda.helper-timing.v1", "recorded": True, "run_id": summary["run_id"],
             "session_id": summary["session_id"], "phase": summary["phase"],
             "measurement_complete": summary["measurement_complete"], "scopes": scopes,
             "diagnostics": [{key: row[key] for key in ("code", "scope", "scope_id") if key in row}
                             for row in summary["diagnostics"]],
             "controller_input_sent": False, "controller_authorized": False}
    if stale_accounting is not None:
        value["stale_capture_accounting"] = stale_accounting
    if any(scope["stale_budget_exhausted"] for scope in scopes.values()):
        value["next_step"] = "Resolve the source-free draft/helper delay before another capture; preserve pending inputs and all freshness checks."
    return value


def record_helper_failure(error, run_id, session_path=None, output_path=None, capture=None):
    """Record a CLI failure and deduplicated stale capture on an existing clock.

    Search order is the explicit session/state.json directory, output directory,
    then this project's reviewed-play UUID directory. A present conflicting or
    malformed timer is reported, never skipped to a different timer. Existing
    timing.json.lock is required; unknown runs produce no files or directories.
    The return value is a compact ``timing`` field for the original CLI error
    response. Accounting errors are returned as ``measurement_error``; callers
    must retain the original helper failure and its ordinary nonzero status.
    """
    empty = {"schema": "veda.helper-timing.v1", "recorded": False,
             "controller_input_sent": False, "controller_authorized": False}
    try:
        if run_id is None and session_path is not None:
            run_id = _session_run(session_path)
        if not _run_id(run_id):
            return {**empty, "reason": "run_identity_unavailable"}
        path = _candidate(run_id, session_path, output_path)
        if path is None:
            return {**empty, "reason": "existing_play_clock_not_found"}
        # The normal tracker creates this lock together with its timing file.
        # Opening r+ deliberately does not create even a lock for an unknown run.
        with path.with_name(path.name + ".lock").open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                state = _read_existing(path, run_id)
                reason = " ".join(str(error).split())[:500] or type(error).__name__
                code = type(error).__name__ if isinstance(error, BaseException) else "helper_failure"
                now = {"monotonic_ns": time.monotonic_ns(), "clock_id": _HELPER_CLOCK_ID}
                state = record_event(state, {"operation": "failure", "code": code, "reason": reason}, **now)
                state = record_event(state, {"operation": "phase", "name": "recovery"}, **now)
                lower = reason.casefold()
                stale = any(marker in lower for marker in ("capture_stale", "stale capture", "capture is stale", "capture stale"))
                accounting = None
                if stale:
                    if capture is None:
                        accounting = "unavailable_without_exact_capture_path"
                    elif state["move"] is None and state["startup"]["completed_at"] is not None and state["floor"] is None:
                        accounting = "unavailable_without_active_scope"
                    else:
                        identity = "capture-" + hashlib.sha256(str(_path(capture)).encode()).hexdigest()
                        state = record_event(state, {"operation": "stale_capture", "capture_id": identity}, **now)
                        accounting = "recorded_or_deduplicated_by_exact_capture_path"
                _write_existing(path, state)
                return _compact(state, stale_accounting=accounting)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except _AccountingError as failure:
        return {**empty, "measurement_error": str(failure)}
    except OSError:
        return {**empty, "measurement_error": "helper_timing_io_error"}
    except (ValueError, KeyError, TypeError, RuntimeError):
        return {**empty, "measurement_error": "helper_timing_data_invalid"}
