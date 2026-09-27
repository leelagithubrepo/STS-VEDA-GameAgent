"""Bind saved-frame recognition evidence to an explicit live reader identity.

This is configuration/evidence binding, not executable attestation or permission
to play. Identity declarations are never invented from accuracy scores. Importing
the module neither reads files nor launches a reader/model/controller.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

from .calibration import CalibrationReport


IDENTITY_SCHEMA = "veda.recognizer-identity.v1"
REPORT_SCHEMA = "veda.saved-frame-validation.v1"
LIVE_SOURCE = "live_capture"


class RuntimeAuthorizationError(ValueError):
    pass


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise RuntimeAuthorizationError("recognizer identity must be finite JSON") from error


def _digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def recognizer_fingerprint(identity: dict[str, Any]) -> str:
    """Hash explicitly supplied command/code/model/prompt/preprocessing identity.

    code_files must include the executable and reader/configuration files. Model,
    prompt and preprocessing digests are declarations of the evaluated immutable
    configuration; this function does not claim to inspect model internals.
    """
    if not isinstance(identity, dict) or identity.get("schema") != IDENTITY_SCHEMA:
        raise RuntimeAuthorizationError("explicit recognizer identity schema is required")
    command = identity.get("command")
    if (not isinstance(command, list) or not command
            or any(not isinstance(v, str) or not v for v in command)
            or not Path(command[0]).is_absolute()):
        raise RuntimeAuthorizationError("identity command needs an absolute executable and nonempty string arguments")
    files = identity.get("code_files")
    if not isinstance(files, list) or not files:
        raise RuntimeAuthorizationError("reader code-file digests are required")
    paths = []
    for item in files:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or not Path(item["path"]).is_absolute() or not _digest(item.get("sha256"))):
            raise RuntimeAuthorizationError("each code file needs an absolute path and SHA-256")
        paths.append(item["path"])
    if len(paths) != len(set(paths)):
        raise RuntimeAuthorizationError("duplicate reader code-file paths")
    model = identity.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"].strip() or not _digest(model.get("sha256")):
        raise RuntimeAuthorizationError("explicit model identifier and immutable digest are required")
    for field in ("prompt", "preprocessing"):
        component = identity.get(field)
        if not isinstance(component, dict) or not _digest(component.get("sha256")):
            raise RuntimeAuthorizationError(f"explicit {field} identity is required")
    if identity.get("output_contract") != "veda.runtime-reading.v1":
        raise RuntimeAuthorizationError("reader must declare the full runtime Reading contract")
    return hashlib.sha256(_canonical(identity).encode()).hexdigest()


def validation_provenance_errors(provenance: Any) -> list[str]:
    """Check retained declarations without reinterpreting them as verified truth."""
    if not isinstance(provenance, dict):
        return ["runtime reader validation provenance is absent"]
    reasons = []
    if provenance.get("evidence_kind") != "independent_saved_frame_validation":
        reasons.append("independent saved-frame validation provenance is required")
    if provenance.get("source_kind") != LIVE_SOURCE:
        reasons.append("validation images lack declared live-capture provenance")
    try:
        fingerprint = recognizer_fingerprint(provenance.get("reader_identity"))
        if provenance.get("recognizer_fingerprint") != fingerprint:
            reasons.append("validation recognizer fingerprint is absent or inconsistent")
    except RuntimeAuthorizationError as error:
        reasons.append(str(error))
    return reasons


def _verify_code_files(identity: dict[str, Any]) -> None:
    paths = {}
    try:
        for item in identity["code_files"]:
            path = Path(item["path"]).resolve(strict=True)
            if path in paths:
                raise RuntimeAuthorizationError("multiple identity entries resolve to one code file")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise RuntimeAuthorizationError("reader code or configuration changed since validation")
            paths[path] = actual
        executable = Path(identity["command"][0]).resolve(strict=True)
        if executable not in paths:
            raise RuntimeAuthorizationError("reader executable is not covered by code-file identity")
        for argument in identity["command"][1:]:
            # Existing path arguments can contain executable code or configuration.
            # Require their bytes to be covered rather than hashing only argv text.
            path = Path(argument)
            if not argument.startswith("-") and path.is_file() and path.resolve() not in paths:
                raise RuntimeAuthorizationError("reader file argument is not covered by code-file identity")
    except OSError as error:
        raise RuntimeAuthorizationError("reader code-file identity cannot be verified") from error


def _fields(required_fields: Iterable[str]) -> frozenset[str]:
    # Lazy import avoids a cycle when ExecutionLoop imports this gate. A caller
    # cannot manufacture a weaker authorization by requesting only a few fields.
    from .execution import RUNTIME_FIELDS

    fields = frozenset(required_fields)
    if not RUNTIME_FIELDS <= fields:
        raise RuntimeAuthorizationError("authorization must cover every current runtime field")
    return fields


@dataclass(frozen=True)
class RuntimeAuthorization:
    calibration: CalibrationReport
    recognizer_fingerprint: str
    source_kind: str
    _identity_json: str
    _required_fields: frozenset[str]

    def assert_runtime(self, *, reader_command, reader_identity, source_kind) -> None:
        _fields(self._required_fields)
        if source_kind != LIVE_SOURCE or source_kind != self.source_kind:
            raise RuntimeAuthorizationError("live runtime requires an identified live capture source")
        fingerprint = recognizer_fingerprint(reader_identity)
        if fingerprint != self.recognizer_fingerprint or _canonical(reader_identity) != self._identity_json:
            raise RuntimeAuthorizationError("runtime recognizer differs from the validated identity")
        if list(reader_command or []) != reader_identity["command"]:
            raise RuntimeAuthorizationError("runtime reader command differs from validation")
        _verify_code_files(reader_identity)

    def assert_frame_source(self, source_kind: str) -> None:
        if source_kind != LIVE_SOURCE or source_kind != self.source_kind:
            raise RuntimeAuthorizationError("live runtime received a saved, replayed, or unproven frame")


def authorize_runtime(report: dict[str, Any], *, reader_command, reader_identity,
                      source_kind: str, required_fields: Iterable[str]) -> RuntimeAuthorization:
    """Consume the actual saved-frame report schema; never trust its boolean alone."""
    fields = _fields(required_fields)
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        raise RuntimeAuthorizationError("a saved-frame validation report is required")
    provenance = report.get("validation_provenance")
    reasons = validation_provenance_errors(provenance)
    if reasons:
        raise RuntimeAuthorizationError("; ".join(reasons))
    if report.get("runtime_authorized") is not True or report.get("authorization_blockers") != []:
        raise RuntimeAuthorizationError("reader has not earned runtime authorization")
    labels = report.get("label_provenance", {})
    if (not isinstance(labels, dict) or labels.get("independent_labels_declared") is not True
            or not isinstance(labels.get("source"), str) or not labels["source"].strip()):
        raise RuntimeAuthorizationError("independent label provenance is required")
    errors = report.get("unflagged_critical_errors")
    if type(errors) is not int or errors != 0:
        raise RuntimeAuthorizationError("unflagged critical recognition errors block runtime authorization")
    metrics = report.get("fields")
    if not isinstance(metrics, dict):
        raise RuntimeAuthorizationError("per-field saved-frame metrics are missing")
    distinct_images = report.get("distinct_image_hashes")
    if type(distinct_images) is not int or distinct_images < 12:
        raise RuntimeAuthorizationError("at least 12 distinct saved image hashes are required")
    accuracy, counts = {}, []
    for field in fields:
        metric = metrics.get(field, {})
        if not isinstance(metric, dict):
            raise RuntimeAuthorizationError(f"{field}: invalid validation metric")
        count, value = metric.get("runtime_labeled_unique_images"), metric.get("runtime_accuracy")
        if (type(count) is not int or not 12 <= count <= distinct_images or type(value) not in (int, float)
                or not math.isfinite(value) or not .95 <= value <= 1
                or metric.get("runtime_field_validated") is not True):
            raise RuntimeAuthorizationError(f"{field}: >=12 unique runtime images and >=0.95 accuracy required")
        accuracy[field] = value
        counts.append(count)
    fingerprint = recognizer_fingerprint(reader_identity)
    if fingerprint != provenance["recognizer_fingerprint"]:
        raise RuntimeAuthorizationError("runtime reader fingerprint differs from the evaluated recognizer")
    result = RuntimeAuthorization(CalibrationReport(accuracy, min(counts)), fingerprint,
        provenance["source_kind"], _canonical(provenance["reader_identity"]), fields)
    result.assert_runtime(reader_command=reader_command, reader_identity=reader_identity, source_kind=source_kind)
    return result
