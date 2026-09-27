"""Package an explicitly inspected capture; never inspect, arm or send input.

The review fields record the caller's declaration, not automatic recognition.
The persistent adapter rechecks freshness and source bytes before arming.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .execution import ARM_PHRASE
from .reviewed_play import MAX_AGE, MAX_BYTES
from .saved_frame_reader import _identity

SCREENS = ("title_continue", "combat", "map", "reward", "rest", "event", "shop", "selection")
MAX_RECEIPT_BYTES = 128 * 1024
_CAPTURE_NAME = re.compile(r"ps5_observation_(\d{8}T\d{6}(?:\.\d{1,6})?)Z(?:_[0-9a-f]{32})?\.png")


class PlayRequestError(ValueError):
    """A fixed error code, without arbitrary source contents or identifiers."""


def _require(condition, code):
    if not condition:
        raise PlayRequestError(code)


def _text(value, limit, code):
    _require(isinstance(value, str) and bool(value.strip())
             and len(value.encode("utf-8")) <= limit and "\0" not in value, code)


def _time(value, code):
    _require(isinstance(value, str) and len(value) <= 64, code)
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise PlayRequestError(code) from None
    _require(result.tzinfo is not None and result.utcoffset() is not None, code)
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "capture_metadata_duplicate_key")
        result[key] = value
    return result


def _receipt_bytes(path):
    try:
        _require(path.is_file(), "capture_metadata_unreadable")
        with path.open("rb") as stream:
            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "capture_metadata_not_regular")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise PlayRequestError("capture_metadata_unreadable") from None
    _require(len(raw) <= MAX_RECEIPT_BYTES, "capture_metadata_too_large")
    return raw


def _source_identity(path):
    try:
        _require(path.is_file(), "capture_not_regular")
        return _identity(path)
    except OSError:
        raise PlayRequestError("capture_unreadable") from None
    except ValueError as exc:
        if isinstance(exc, PlayRequestError):
            raise
        raise PlayRequestError("capture_image_invalid") from None


def write_arm_request(*, run_id: str, capture: Path, screen: str, reviewer: str,
                      evidence_note: str, phrase: str, reviewed: bool,
                      exclusive_client_confirmed: bool, output: Path,
                      now: datetime | None = None) -> dict:
    """Exclusively create one arm JSON file from a fresh, declared review.

    ``reviewed=True`` explicitly declares inspection of this exact image, its
    Slay the Spire identity, and the supplied screen. It does not declare a
    complete combat reading. No run lookup, capture or controller call occurs.
    ``now`` is a test clock; the CLI always uses the current wall clock.
    """
    _require(reviewed is True, "exact_image_review_required")
    _require(exclusive_client_confirmed is True, "exclusive_client_declaration_required")
    _require(phrase == ARM_PHRASE, "current_run_arming_phrase_required")
    _text(run_id, 128, "run_id_invalid")
    _text(reviewer, 128, "reviewer_invalid")
    _text(evidence_note, 4096, "evidence_note_invalid")
    _require(isinstance(screen, str) and screen in SCREENS, "screen_unknown")
    for value in (capture, output):
        _require(isinstance(value, (str, Path)), "path_invalid")
        _text(str(value), 4096, "path_invalid")
    try:
        image_path = Path(capture).expanduser().resolve()
        receipt_path = image_path.with_suffix(".capture.json")
        declared_output = Path(output).expanduser()
        # Resolve the directory, not the final entry: exclusive creation must
        # reject an existing symlink even when its target does not exist.
        output_path = declared_output.parent.resolve() / declared_output.name
        resolved_output = output_path.resolve()
    except (OSError, ValueError, RuntimeError):
        raise PlayRequestError("path_invalid") from None
    _require(resolved_output not in {image_path, receipt_path}, "output_is_capture_source")
    raw = _receipt_bytes(receipt_path)
    try:
        receipt = json.loads(raw, object_pairs_hook=_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(PlayRequestError("capture_metadata_invalid")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PlayRequestError):
            raise
        raise PlayRequestError("capture_metadata_invalid") from None
    _require(isinstance(receipt, dict) and receipt.get("schema") == "veda.game-window-capture.v1",
             "capture_schema_invalid")
    _require(receipt.get("image_path") == str(image_path), "capture_path_mismatch")
    digest, dimensions = _source_identity(image_path)
    _require(receipt.get("image_sha256") == digest, "capture_hash_mismatch")
    claimed = receipt.get("dimensions")
    _require(isinstance(claimed, list) and len(claimed) == 2
             and all(type(v) is int for v in claimed) and claimed == dimensions,
             "capture_dimensions_mismatch")
    requested = receipt.get("capture_requested_at")
    observed = _time(requested, "capture_requested_at_invalid")
    named = _CAPTURE_NAME.fullmatch(image_path.name)
    _require(named is not None, "capture_filename_invalid")
    try:
        fmt = "%Y%m%dT%H%M%S.%f" if "." in named[1] else "%Y%m%dT%H%M%S"
        named_time = datetime.strptime(named[1], fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        raise PlayRequestError("capture_filename_invalid") from None
    _require(named_time == observed, "capture_filename_time_mismatch")
    current = now if now is not None else datetime.now(timezone.utc)
    _require(isinstance(current, datetime) and current.tzinfo is not None
             and current.utcoffset() is not None, "clock_invalid")
    age = (current - observed).total_seconds()
    _require(age >= 0, "capture_future_dated")
    _require(age <= MAX_AGE, "capture_stale")
    completed = _time(receipt.get("capture_completed_at"), "capture_completed_at_invalid")
    _require(observed <= completed <= current, "capture_time_order_invalid")
    frame_id = "reviewed-" + hashlib.sha256((requested + "\0" + digest).encode()).hexdigest()
    request = {"operation": "arm", "phrase": phrase, "run_id": run_id,
               "source": {"path": str(image_path), "sha256": digest, "captured_at": requested,
                          "origin": "reviewer", "evidence_note": evidence_note},
               "review": {"complete": True, "reviewer": reviewer, "frame_id": frame_id,
                          "image_sha256": digest},
               "frame_id": frame_id, "game": "Slay the Spire", "screen": screen,
               "exclusive_client_confirmed": True}
    data = (json.dumps(request, sort_keys=True, allow_nan=False, indent=2) + "\n").encode()
    _require(len(data) <= MAX_BYTES, "request_too_large")
    _require(_source_identity(image_path) == (digest, dimensions)
             and _receipt_bytes(receipt_path) == raw, "capture_changed_during_packaging")
    if now is None:
        _require(0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= MAX_AGE, "capture_stale")
    created = False
    try:
        with output_path.open("xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise PlayRequestError("output_already_exists") from None
    except OSError:
        if created:
            output_path.unlink(missing_ok=True)
        raise PlayRequestError("output_write_failed") from None
    return {"request_file": str(output_path)}
