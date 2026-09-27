"""One source-bound saved-image energy crop; no capture or action authority."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import re
import time

from .native_ocr import (
    REGIONS_SCHEMA, SCHEMA, _ReaderFailure, _inside, _png_identity, _regions,
    _validate_observations, extract_hud,
)


_FRACTION = re.compile(r"([0-9]+)\s*/\s*([0-9]+)\Z")
_OPERATIONAL_ERRORS = {
    "helper_start_failed", "helper_timeout", "helper_failed", "helper_output_limit",
    "helper_stderr_limit", "helper_not_prebuilt_or_executable",
}
_REGION_ERRORS = {"region_image_failed", "native_ocr_failed", "native_ocr_outside_region"}


def _parse_energy(observations, region):
    """Alternatives may veto a reading but may never repair its top text."""
    selected, exact_values, matching_rows, valid_tops = [], set(), [], []
    ambiguity = []
    for index, row in enumerate(observations):
        if not _inside(row["box_original_pixels_top_left"], region):
            continue
        candidates = []
        row_values = set()
        for candidate in row["candidates"]:
            match = _FRACTION.fullmatch(candidate["text"].strip())
            pair = None
            if match:
                # Bound conversion independently of Python's configurable
                # integer-string limit. This is not an assumed game maximum.
                if any(len(part) > 9 for part in match.groups()):
                    ambiguity.append("fraction_numeric_bound_exceeded")
                else:
                    pair = tuple(map(int, match.groups()))
                    row_values.add(pair)
                    exact_values.add(pair)
            candidates.append({**candidate, "fraction": list(pair) if pair is not None else None})
        if row_values:
            matching_rows.append(index)
        top = candidates[0]
        if top["fraction"] is not None and top["confidence"] >= .8:
            valid_tops.append(top["fraction"])
        selected.append({"observation_index": index,
                         "box_original_pixels_top_left": list(row["box_original_pixels_top_left"]),
                         "quadrilateral_original_pixels_top_left": deepcopy(row["quadrilateral_original_pixels_top_left"]),
                         "candidates": candidates})
    if len(exact_values) > 1:
        ambiguity.append("conflicting_exact_fractions")
    if len(matching_rows) > 1:
        ambiguity.append("multiple_matching_observations")
    if any(pair[1] == 0 for pair in exact_values):
        ambiguity.append("zero_denominator")
    ambiguity = sorted(set(ambiguity))
    fraction = valid_tops[0] if not ambiguity and len(valid_tops) == 1 else None
    if ambiguity:
        error = "ambiguous_energy_fraction"
    elif fraction is not None:
        error = None
    elif not selected:
        error = "missing_region_text"
    elif not exact_values:
        error = "missing_exact_fraction"
    else:
        error = "top_fraction_not_exact_or_confident"
    return {"fraction": fraction, "error": error, "selected": selected,
            "distinct_candidates": [list(pair) for pair in sorted(exact_values)],
            "matching_observation_indices": matching_rows,
            "ambiguous": bool(ambiguity), "ambiguity_reasons": ambiguity}


def _identity_matches(envelope, *, path, digest, dimensions, frame_id, schema):
    if (not isinstance(envelope, dict) or envelope.get("schema") != schema
            or envelope.get("image_sha256") != digest
            or envelope.get("source_dimensions") != dimensions
            or envelope.get("image_path") != str(path)
            or envelope.get("frame_id") != frame_id
            or envelope.get("parent_image_sha256", digest) != digest
            or envelope.get("parent_frame_id", frame_id) != frame_id):
        raise ValueError("energy_reader_source_mismatch")


def _validate_region(response, request, *, path, digest, dimensions, frame_id):
    if type(response.get("ok")) is not bool:
        raise ValueError("energy_reader_invalid_outcome")
    if response["ok"]:
        if response.get("error") is not None:
            raise ValueError("energy_reader_invalid_outcome")
    elif response.get("error") not in _OPERATIONAL_ERRORS:
        raise ValueError("energy_reader_integrity_failure:" + str(response.get("error")))
    regions = response.get("regions")
    if not isinstance(regions, list) or len(regions) != 1 or not isinstance(regions[0], dict):
        raise ValueError("energy_reader_region_count_mismatch")
    region = regions[0]
    if (region.get("id") != request["id"]
            or region.get("box_original_pixels_ltrb") != request["box"]
            or region.get("preprocessing") != "original"
            or region.get("image_sha256") != digest
            or region.get("parent_image_sha256") != digest
            or region.get("source_dimensions") != dimensions
            or region.get("parent_frame_id", frame_id) != frame_id
            or region.get("frame_id", frame_id) != frame_id
            or region.get("image_path", str(path)) != str(path)
            or region.get("scale") != 3
            or type(region.get("ok")) is not bool):
        raise ValueError("energy_reader_region_mismatch")
    if region["ok"]:
        if not response["ok"] or region.get("error") is not None:
            raise ValueError("energy_reader_invalid_region_outcome")
        try:
            _validate_observations(region.get("observations"), dimensions, request["box"])
        except _ReaderFailure as exc:
            raise ValueError("energy_reader_invalid_region:" + str(exc)) from exc
    else:
        allowed = _REGION_ERRORS if response["ok"] else {response["error"]}
        if region.get("error") not in allowed or region.get("observations") != []:
            raise ValueError("energy_reader_invalid_region_outcome")
        if region["error"] == "native_ocr_outside_region":
            count = region.get("rejected_observation_count")
            if type(count) is not int or not 1 <= count <= 10000:
                raise ValueError("energy_reader_invalid_rejection_metadata")
    return region


def refine_energy_reading(image_path, *, viewport, native, reader, frame_id):
    """Return copied HUD and both-pass evidence after exactly one energy ROI.

    ``native`` is the validated full-frame native envelope for this saved PNG.
    The crop uses outward-rounded DEFAULT_REGIONS geometry. Both passes require
    one literal top fraction with confidence >= .8; exact alternatives can only
    veto. Missing crop text may retain a valid original, never historical state.
    Source and protocol errors raise ValueError for the coordinator to clear all
    derived readings. Per-region/operational unavailability retains only the
    equally strictly reparsed original. No Pillow or expected values are used.
    """
    began = time.monotonic()
    path = Path(image_path).expanduser().resolve()
    if not isinstance(frame_id, str) or not frame_id.strip() or len(frame_id) > 256:
        raise ValueError("energy_reader_invalid_frame_id")
    try:
        digest, dimensions = _png_identity(path)
        geometry = _regions(dimensions, {"viewport": viewport})
    except _ReaderFailure as exc:
        raise ValueError("energy_reader_source_or_geometry:" + str(exc)) from exc
    _identity_matches(native, path=path, digest=digest, dimensions=dimensions,
                      frame_id=frame_id, schema=SCHEMA)
    if native.get("ok") is not True or not isinstance(native.get("hud"), dict):
        raise ValueError("energy_reader_invalid_original")
    try:
        _validate_observations(native.get("observations"), dimensions)
    except _ReaderFailure as exc:
        raise ValueError("energy_reader_invalid_original:" + str(exc)) from exc
    # Same bytes alone do not bind precomputed HUD fields to this viewport or
    # to these observations. Validate the original before retaining HP/max HP.
    expected_hud = extract_hud(native["observations"], source_dimensions=dimensions,
                               regions={"viewport": viewport})
    if (native["hud"] != expected_hud or any(type(native["hud"].get(field)) is not type(expected_hud[field])
            for field in ("hp", "max_hp", "energy", "energy_max"))):
        raise ValueError("energy_reader_original_hud_mismatch")
    bounds = geometry["pixels"]["energy"]
    roi = [math.floor(bounds[0]), math.floor(bounds[1]), math.ceil(bounds[2]), math.ceil(bounds[3])]
    request = {"id": "energy", "box": roi, "preprocessing": "original"}
    original = _parse_energy(native["observations"], roi)
    response = reader.observe_regions(path, frame_id=frame_id, regions=[request])
    _identity_matches(response, path=path, digest=digest, dimensions=dimensions,
                      frame_id=frame_id, schema=REGIONS_SCHEMA)
    if response.get("scale") != 3:
        raise ValueError("energy_reader_scale_mismatch")
    region = _validate_region(response, request, path=path, digest=digest, dimensions=dimensions, frame_id=frame_id)
    focused = _parse_energy(region["observations"], roi)
    focused_status = ("processed" if region["ok"] else "region_failed" if response["ok"] else "reader_failed")
    if not region["ok"]:
        focused["error"] = region["error"]
    exact = {tuple(pair) for reading in (original, focused) for pair in reading["distinct_candidates"]}
    reasons = sorted(set(original["ambiguity_reasons"] + focused["ambiguity_reasons"]))
    if len(exact) > 1:
        reasons.append("conflicting_exact_fractions_across_passes")
    value = None
    if reasons:
        status, error = "conflict", "ambiguous_energy_fraction"
    elif focused["fraction"] is not None:
        value = focused["fraction"]
        status, error = ("agreed" if original["fraction"] is not None else "refined"), None
    elif original["fraction"] is not None:
        value = original["fraction"]
        status, error = "original_retained", None
    else:
        status, error = "unknown", focused["error"] or original["error"]
    try:
        if _png_identity(path) != (digest, dimensions):
            raise ValueError("energy_reader_image_changed")
    except _ReaderFailure as exc:
        raise ValueError("energy_reader_image_changed:" + str(exc)) from exc
    hud = deepcopy(native["hud"])
    hud["energy"], hud["energy_max"] = (value if value is not None else (None, None))
    # Existing `regions` describes nominal unrounded HUD geometry. Name the
    # outward-rounded extraction rectangle separately instead of rewriting it.
    hud["energy_extraction_region_px"] = list(roi)
    hud.setdefault("errors", {})["energy"] = error
    previous_evidence = deepcopy(hud.get("evidence", {}).get("energy"))
    hud.setdefault("evidence", {})["energy"] = {
        "fraction": value, "error": error, "distinct_candidates": [list(pair) for pair in sorted(exact)],
        "original": original, "focused": focused, "ambiguity_reasons": reasons,
        "previous_full_pass_evidence": previous_evidence,
    }
    return {"hud": hud, "refinement": {
        "schema": "veda.energy-refinement.v1", "status": status, "focused_status": focused_status,
        "image_path": str(path), "image_sha256": digest, "source_dimensions": dimensions,
        "frame_id": frame_id, "viewport": list(viewport), "requested_region": request,
        "geometry": geometry, "region_count": 1, "regions": deepcopy(response["regions"]),
        "original": original, "focused": focused, "ambiguity_reasons": reasons,
        "reader_timing_ms": deepcopy(response.get("timing_ms", {})),
        "timing_ms": (time.monotonic() - began) * 1000,
        "runtime_authorized": False, "controller_authorized": False,
    }}
