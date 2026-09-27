"""Score independently labeled, saved images without capture or controller access.

Importing this module does no I/O. Labels and predictions are separate inputs;
missing predictions are never filled from labels. Accuracy uses distinct image
hashes, not repeated rows, and unknown predictions remain in its denominator.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .execution import RUNTIME_FIELDS
from .runtime_authorization import validation_provenance_errors


SCHEMA = "veda.saved-frame-validation.v1"
_ABSENCE_FIELDS = frozenset({"focused_card_id", "selected_card_id", "focused_target_id"})
_MISSING = object()


@dataclass(frozen=True)
class SavedFrame:
    frame_id: str
    image: Path
    sha256: str
    expected: dict[str, Any]


@dataclass(frozen=True)
class ValidationManifest:
    frames: tuple[SavedFrame, ...]
    label_source: str
    independent_labels: bool
    validation_provenance: dict[str, Any] | None = None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_manifest(path: str | Path) -> ValidationManifest:
    """Read and hash-check every saved image before any provider may be called.

    Manifest: {label_source, independent_labels, frames: [{frame_id, image,
    sha256, expected: {field: value}}]}. Image paths are relative to the manifest.
    Null numeric labels mean unreadable, not a known zero. An explicit null UI
    focus/selection label means a verified absence. Structured facts match
    exactly, so partial enemy/context objects cannot certify the whole field.
    Enemy/map confidence metadata is excluded from factual equality.
    """
    path = Path(path).resolve()
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or not isinstance(document.get("frames"), list):
        raise ValueError("manifest needs a frames array")
    label_source = document.get("label_source", "")
    if not isinstance(label_source, str):
        raise ValueError("label_source must be text")
    independent = document.get("independent_labels", False)
    if type(independent) is not bool:
        raise ValueError("independent_labels must be a boolean declaration")
    provenance = document.get("validation_provenance")
    if provenance is not None and not isinstance(provenance, dict):
        raise ValueError("validation_provenance must be an explicitly supplied object")
    _json(provenance)
    frames, ids, labels = [], set(), {}
    for row in document["frames"]:
        if not isinstance(row, dict):
            raise ValueError("each manifest frame must be an object")
        frame_id = row.get("frame_id")
        if not isinstance(frame_id, str) or not frame_id or frame_id in ids:
            raise ValueError("each frame needs a unique nonempty frame_id")
        ids.add(frame_id)
        image_name, digest, expected = row.get("image"), row.get("sha256"), row.get("expected")
        if not isinstance(image_name, str) or not image_name:
            raise ValueError(f"{frame_id}: image path is required")
        image = (path.parent / image_name).resolve()
        actual_digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest != actual_digest:
            raise ValueError(f"{frame_id}: image SHA-256 does not match manifest")
        if not isinstance(expected, dict) or any(not isinstance(k, str) or not k for k in expected):
            raise ValueError(f"{frame_id}: expected must map named fields to labels")
        _json(expected)
        for field, value in expected.items():
            key = (digest, field)
            if key in labels and labels[key] != _json(value):
                raise ValueError(f"{frame_id}: conflicting labels for the same image and field")
            labels[key] = _json(value)
        frames.append(SavedFrame(frame_id, image, digest, expected))
    return ValidationManifest(tuple(frames), label_source, independent, provenance)


def load_predictions(path: str | Path) -> list[dict[str, Any]]:
    document = json.loads(Path(path).read_text())
    rows = document.get("predictions") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("predictions file needs a predictions array")
    return rows


def _unknown(field: str, value: Any) -> bool:
    if value is _MISSING:
        return True
    if value is None:
        return field not in _ABSENCE_FIELDS
    if isinstance(value, str) and value.strip().casefold() in {"", "unknown", "unknown enemy"}:
        return True
    if isinstance(value, dict):
        return any(_unknown(key, child) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return any(_unknown(field, child) for child in value)
    return False


def _complete_label(field: str, value: Any) -> bool:
    """Prevent a sparse structured label from validating a complete runtime field."""
    if _unknown(field, value):
        return False
    if field in {"hp", "max_hp", "energy", "block", "player_weak", "player_vulnerable", "player_frail", "end_turn_damage"}:
        return type(value) is int and value >= 0
    if field == "player_strength":
        return type(value) is int
    if field == "hand_complete":
        return type(value) is bool
    if field in {"hand", "hand_order", "target_order"}:
        return isinstance(value, list) and all(isinstance(v, str) and bool(v) for v in value)
    if field in _ABSENCE_FIELDS:
        return value is None or isinstance(value, str) and bool(value)
    if field == "ui_phase":
        return isinstance(value, str) and value in {"hand", "card_selected", "targeting", "tooltip", "animating"}
    if field == "enemies":
        keys = {"name", "hp", "max_hp", "intent", "block", "intent_hits", "intent_total_damage"}
        return isinstance(value, list) and all(
            isinstance(v, dict) and keys <= v.keys()
            and all(isinstance(v[k], str) and bool(v[k]) for k in ("name", "intent"))
            and all(type(v[k]) is int and v[k] >= 0 for k in ("hp", "max_hp", "block", "intent_total_damage"))
            and isinstance(v["intent_hits"], list)
            and all(type(hit) is int and hit >= 0 for hit in v["intent_hits"])
            and sum(v["intent_hits"]) == v["intent_total_damage"]
            and v["hp"] <= v["max_hp"]
            for v in value)
    if field == "hand_details":
        keys = {"name", "title_color", "upgraded", "current_cost"}
        return isinstance(value, list) and all(
            isinstance(v, dict) and keys <= v.keys()
            and isinstance(v["name"], str) and bool(v["name"])
            and isinstance(v["title_color"], str) and v["title_color"] in {"green", "teal", "white"}
            and type(v["upgraded"]) is bool
            and type(v["current_cost"]) is int and v["current_cost"] >= 0
            for v in value)
    if field == "advisory_context":
        # Full context is an independent adapter's responsibility; the current
        # StructuredGameState provider cannot produce or validate this object.
        if not isinstance(value, dict) or not {"state", "inventory", "fresh", "unknowns", "encounter_type"} <= value.keys():
            return False
        state = value["state"]
        inventory = value["inventory"]
        return isinstance(state, dict) and {
            "hp", "max_hp", "energy", "block", "hand", "enemies", "powers", "piles",
            "strength", "dexterity", "weak", "vulnerable", "frail", "no_block", "counters",
            "hand_complete", "powers_complete", "end_turn_damage", "unmodeled_effects",
        } <= state.keys() and isinstance(inventory, dict) and {"coverage", "current"} <= inventory.keys()
    return True


def _facts(field: str, value: Any) -> Any:
    """Confidence is a provider signal, not an independently visible game fact."""
    omitted = {"enemies": {"intent_damage_confidence"}, "map_nodes": {"confidence"}}.get(field)
    if omitted and isinstance(value, (list, tuple)):
        return [{key: item for key, item in entry.items() if key not in omitted}
                if isinstance(entry, dict) else entry for entry in value]
    return value


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def evaluate_predictions(manifest: ValidationManifest, predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure scoring; runtime evidence requires >=12 distinct COMBAT images/field.

    Each prediction row binds frame_id and sha256 to a prediction object. Optional
    unknown_fields / flagged_fields are the provider's declared uncertainty; an
    error makes the whole row unknown. model_request_round_trip_ns is optional,
    genuinely measured request latency, never reconstructed from screenshot gaps.
    Duplicate image outcomes are conservatively reduced (error > unknown > match).
    """
    frames = {frame.frame_id: frame for frame in manifest.frames}
    by_id, timing, prediction_errors = {}, [], Counter()
    for row in predictions:
        if not isinstance(row, dict):
            raise ValueError("each prediction must be an object")
        frame_id = row.get("frame_id")
        if frame_id not in frames or frame_id in by_id:
            raise ValueError("prediction frame_id is absent from manifest or duplicated")
        if row.get("sha256") != frames[frame_id].sha256:
            raise ValueError(f"{frame_id}: prediction is bound to a different image hash")
        if not isinstance(row.get("prediction"), dict):
            raise ValueError(f"{frame_id}: prediction must be an object")
        _json(row)
        for key in ("unknown_fields", "flagged_fields"):
            value = row.get(key, [])
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ValueError(f"{frame_id}: {key} must be a list of field names")
        elapsed = row.get("model_request_round_trip_ns")
        if elapsed is not None:
            if type(elapsed) is not int or elapsed < 0:
                raise ValueError("request round-trip timing must be nonnegative integer nanoseconds")
            timing.append(elapsed)
        if row.get("error"):
            if not isinstance(row["error"], str):
                raise ValueError("prediction error must be a string")
            prediction_errors[row["error"]] += 1
        by_id[frame_id] = row

    fields = set(RUNTIME_FIELDS) | {name for f in manifest.frames for name in f.expected}
    outcomes = {name: {} for name in fields}
    runtime_outcomes = {name: {} for name in fields}
    unknown_labels = Counter()
    incomplete_labels = Counter()
    details, unflagged_errors = [], set()
    rank = {"correct": 0, "unknown": 1, "error": 2}
    for frame in manifest.frames:
        row = by_id.get(frame.frame_id, {})
        predicted = row.get("prediction", {})
        statuses = {}
        for field, expected in frame.expected.items():
            if _unknown(field, expected):
                unknown_labels[field] += 1
                statuses[field] = "label_unknown"
                continue
            actual = predicted.get(field, _MISSING)
            if row.get("error") or field in row.get("unknown_fields", []) or _unknown(field, actual):
                outcome = "unknown"
            else:
                outcome = "correct" if _json(_facts(field, expected)) == _json(_facts(field, actual)) else "error"
            flagged = field in row.get("flagged_fields", [])
            if outcome == "error" and field in RUNTIME_FIELDS and not flagged:
                unflagged_errors.add((frame.sha256, field))
            statuses[field] = outcome
            prior = outcomes[field].get(frame.sha256)
            if prior is None or rank[outcome] > rank[prior]:
                outcomes[field][frame.sha256] = outcome
            if not _complete_label(field, expected):
                incomplete_labels[field] += 1
                continue
            if frame.expected.get("screen_type") == "COMBAT":
                prior = runtime_outcomes[field].get(frame.sha256)
                if prior is None or rank[outcome] > rank[prior]:
                    runtime_outcomes[field][frame.sha256] = outcome
        details.append({"frame_id": frame.frame_id, "sha256": frame.sha256,
                        "prediction_present": bool(row), "fields": statuses,
                        "flagged_fields": row.get("flagged_fields", []), "error": row.get("error")})

    metrics, blockers = {}, []
    for field in sorted(fields):
        # A second prediction for the same image cannot hide an error/unknown by
        # omitting its screen_type label from that duplicate manifest row.
        runtime_outcomes[field] = {digest: outcomes[field][digest] for digest in runtime_outcomes[field]}
        counts, runtime = Counter(outcomes[field].values()), Counter(runtime_outcomes[field].values())
        denominator, runtime_denominator = sum(counts.values()), sum(runtime.values())
        accuracy = counts["correct"] / denominator if denominator else None
        runtime_accuracy = runtime["correct"] / runtime_denominator if runtime_denominator else None
        metrics[field] = {
            "labeled_unique_images": denominator, "correct": counts["correct"],
            "errors": counts["error"], "unknown_predictions": counts["unknown"],
            "accuracy": accuracy,
            "prediction_coverage": (denominator - counts["unknown"]) / denominator if denominator else None,
            "unknown_label_rows": unknown_labels[field], "incomplete_label_rows": incomplete_labels[field],
            "runtime_labeled_unique_images": runtime_denominator,
            "runtime_accuracy": runtime_accuracy,
            "runtime_field_validated": runtime_denominator >= 12 and runtime_accuracy is not None and runtime_accuracy >= .95,
        }
        if field in RUNTIME_FIELDS and not metrics[field]["runtime_field_validated"]:
            blockers.append(f"{field}: needs >=12 distinct fully labeled COMBAT images and >=0.95 accuracy")
    if not manifest.independent_labels or not manifest.label_source.strip():
        blockers.append("independent label provenance has not been declared")
    if unflagged_errors:
        blockers.append(f"{len(unflagged_errors)} unflagged critical image/field errors")
    recognition_fields_validated = not blockers
    provenance = manifest.validation_provenance
    binding_errors = validation_provenance_errors(provenance)
    if not binding_errors:
        fingerprint = provenance["recognizer_fingerprint"]
        if not predictions or any(row.get("recognizer_fingerprint") != fingerprint for row in predictions):
            binding_errors.append("predictions lack a matching runtime reader fingerprint")
    blockers.extend(binding_errors)
    return {
        "schema": SCHEMA, "runtime_authorized": not blockers, "authorization_blockers": blockers,
        "recognition_fields_validated": recognition_fields_validated,
        "validation_provenance": json.loads(_json(provenance)),
        "recognizer_binding_errors": binding_errors,
        "authorization_scope": "Recognition evidence only; this report never arms a run or sends input.",
        "required_runtime_fields": sorted(RUNTIME_FIELDS), "minimum_unique_images_per_field": 12,
        "minimum_field_accuracy": .95,
        "label_provenance": {"source": manifest.label_source, "independent_labels_declared": manifest.independent_labels,
                             "note": "Human-provided provenance declaration; not independently provable by this evaluator."},
        "manifest_rows": len(manifest.frames), "distinct_image_hashes": len({f.sha256 for f in manifest.frames}),
        "prediction_rows": len(predictions), "missing_prediction_rows": len(frames.keys() - by_id.keys()),
        "fields": metrics, "unflagged_critical_errors": len(unflagged_errors),
        "prediction_errors": dict(prediction_errors), "frames": details,
        "latency": {"label": "model_request_round_trip", "unit": "nanoseconds", "samples": len(timing),
                    "missing_samples": len(predictions) - len(timing), "total_ns": sum(timing),
                    "p50_ns": _percentile(timing, .50), "p95_ns": _percentile(timing, .95),
                    "max_ns": max(timing) if timing else None,
                    "scope": "Request round trip only; includes service/transport overhead and is not model thinking time.",
                    "percentile_method": "linear interpolation of supplied measured request durations"},
    }


def observe_saved_frames(manifest: ValidationManifest, provider: Any, trace: Any) -> list[dict[str, Any]]:
    """Explicit saved-image calls only. The caller supplies a bounded provider.

    Timing comes from its model_request_round_trip span, never last_latency_ms
    (which also includes preparation/parsing). No labels enter the provider.
    """
    predictions = []
    for frame in manifest.frames:
        if hashlib.sha256(frame.image.read_bytes()).hexdigest() != frame.sha256:
            raise ValueError(f"{frame.frame_id}: saved image changed after manifest validation")
        before = trace.report()["stages"].get("model_request_round_trip", {})
        row = {"frame_id": frame.frame_id, "sha256": frame.sha256, "prediction": {}}
        try:
            state = provider.observe(frame.image, frame_id=frame.frame_id)
            row["prediction"] = asdict(state)
            if provider.last_error:
                row["error"] = provider.last_error
            else:
                flagged = set()
                if state.confidence < .85 or state.screen_type == "UNKNOWN":
                    flagged.update(RUNTIME_FIELDS)
                if state.end_turn_damage_confidence < .90:
                    flagged.add("end_turn_damage")
                if any(enemy.intent_damage_confidence < .90 for enemy in state.enemies):
                    flagged.add("enemies")
                row["flagged_fields"] = sorted(flagged)
        except Exception as error:
            # A saved-frame evaluator records provider failures; it cannot resume
            # gameplay or turn an exception into a fabricated observation.
            row["error"] = type(error).__name__
        after = trace.report()["stages"].get("model_request_round_trip", {})
        if after.get("completed_samples", 0) > before.get("completed_samples", 0):
            row["model_request_round_trip_ns"] = after["sample_wall_sum_ns"] - before.get("sample_wall_sum_ns", 0)
        predictions.append(row)
    return predictions
