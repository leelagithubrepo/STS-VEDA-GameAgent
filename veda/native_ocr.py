"""Bounded native text/HUD reading of saved PNGs; no capture or action authority.

Build ``scripts/native_ocr.swift`` explicitly using its documented command,
then supply the executable path. This reader never compiles, installs, invokes
an external model, captures the screen, or imports a controller adapter.

``regions`` accepts an original-pixel viewport ``[left, top, right, bottom]``
and optional viewport-relative normalized ``hp``/``energy`` rectangles in the
same LTRB order. The default viewport is the image itself, appropriate for an
already-cropped game image. Desktop images require an inspected viewport.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import struct
import subprocess
import time
from typing import Any


SCHEMA = "veda.native-text.v1"
RAW_SCHEMA = "veda.native-text.raw.v1"
REGIONS_SCHEMA = "veda.native-text-regions.v1"
RAW_REGIONS_SCHEMA = "veda.native-text-regions.raw.v1"
DEFAULT_REGIONS = {"hp": [0.25, 0.0, 0.42, 0.09], "energy": [0.045, 0.75, 0.17, 0.93]}
_FRACTION = re.compile(r"([0-9]+)\s*/\s*([0-9]+)")
_MAX_IMAGE_BYTES = 64 * 1024 * 1024


class _ReaderFailure(Exception):
    pass


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _run_bounded(command: list[str], timeout: float, max_bytes: int) -> tuple[int, bytes]:
    """Drain both pipes within total time/output bounds; no shell or replay."""
    began = time.monotonic()
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except OSError as exc:
        raise _ReaderFailure("helper_start_failed") from exc
    output = bytearray()
    stderr_bytes = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = timeout - (time.monotonic() - began)
                if remaining <= 0:
                    raise _ReaderFailure("helper_timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is process.stdout:
                        if len(output) + len(chunk) > max_bytes:
                            raise _ReaderFailure("helper_output_limit")
                        output.extend(chunk)
                    else:
                        stderr_bytes += len(chunk)
                        if stderr_bytes > min(max_bytes, 8192):
                            raise _ReaderFailure("helper_stderr_limit")
            remaining = timeout - (time.monotonic() - began)
            if remaining <= 0:
                raise _ReaderFailure("helper_timeout")
            try:
                return process.wait(timeout=remaining), bytes(output)
            except subprocess.TimeoutExpired as exc:
                raise _ReaderFailure("helper_timeout") from exc
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close()
        process.stderr.close()


def _png_identity(path: Path) -> tuple[str, list[int]]:
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise _ReaderFailure("image_unreadable") from exc
    if len(data) > _MAX_IMAGE_BYTES:
        raise _ReaderFailure("image_too_large")
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise _ReaderFailure("image_not_png")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height or width * height > 100_000_000:
        raise _ReaderFailure("image_dimensions_invalid")
    return hashlib.sha256(data).hexdigest(), [width, height]


def _rect(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 4 and all(_finite(v) for v in value)
            and value[0] < value[2] and value[1] < value[3])


def _regions(dimensions: list[int], regions: dict[str, Any] | None) -> dict[str, Any]:
    if regions is None:
        regions = {}
    if not isinstance(regions, dict) or set(regions) - {"viewport", "hp", "energy"}:
        raise _ReaderFailure("invalid_regions")
    viewport = regions.get("viewport", [0, 0, *dimensions])
    if (not _rect(viewport) or viewport[0] < 0 or viewport[1] < 0
            or viewport[2] > dimensions[0] or viewport[3] > dimensions[1]):
        raise _ReaderFailure("invalid_viewport")
    width, height = viewport[2] - viewport[0], viewport[3] - viewport[1]
    result = {"viewport": list(viewport), "relative": {}, "pixels": {}}
    for field, default in DEFAULT_REGIONS.items():
        relative = regions.get(field, default)
        if not _rect(relative) or any(not 0 <= number <= 1 for number in relative):
            raise _ReaderFailure("invalid_regions")
        result["relative"][field] = list(relative)
        result["pixels"][field] = [viewport[0] + relative[0] * width, viewport[1] + relative[1] * height,
                                    viewport[0] + relative[2] * width, viewport[1] + relative[3] * height]
    return result


def _inside(box: list[float], region: list[float]) -> bool:
    x, y, width, height = box
    return region[0] <= x and region[1] <= y and x + width <= region[2] and y + height <= region[3]


def _fraction(observations: list[dict[str, Any]], region: list[float]) -> dict[str, Any]:
    found: set[tuple[int, int]] = set()
    selected = []
    for index, observation in enumerate(observations):
        if not _inside(observation["box_original_pixels_top_left"], region):
            continue
        evidence = {"observation_index": index, "candidates": []}
        for candidate in observation["candidates"]:
            match = _FRACTION.fullmatch(candidate["text"].strip())
            pair = tuple(map(int, match.groups())) if match else None
            if pair is not None:
                found.add(pair)
            evidence["candidates"].append({**candidate, "fraction": list(pair) if pair else None})
        selected.append(evidence)
    error = ("missing_region_text" if not selected else "missing_exact_fraction" if not found else
             "conflicting_candidates" if len(found) != 1 else None)
    pair = list(next(iter(found))) if error is None else None
    if pair is not None and pair[1] == 0:
        error, pair = "zero_denominator", None
    return {"fraction": pair, "error": error, "selected": selected,
            "distinct_candidates": [list(pair) for pair in sorted(found)]}


def extract_hud(observations: list[dict[str, Any]], *, source_dimensions: list[int],
                regions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse validated OCR text spatially, without expected values or history.

    A bounding box must be wholly within its predefined region. Every exact
    fraction candidate participates; confidence cannot overrule disagreement.
    Energy above its printed denominator is permitted (e.g. extra energy).
    """
    geometry = _regions(source_dimensions, regions)
    hp = _fraction(observations, geometry["pixels"]["hp"])
    energy = _fraction(observations, geometry["pixels"]["energy"])
    if hp["fraction"] and hp["fraction"][0] > hp["fraction"][1]:
        hp["fraction"], hp["error"] = None, "hp_exceeds_maximum"
    h, e = hp["fraction"], energy["fraction"]
    return {"hp": h[0] if h else None, "max_hp": h[1] if h else None,
            "energy": e[0] if e else None, "energy_max": e[1] if e else None,
            "errors": {"hp": hp["error"], "max_hp": hp["error"], "energy": energy["error"]},
            "regions": geometry, "evidence": {"hp": hp, "energy": energy}}


def _validate_raw(raw: Any, path: Path, digest: str, dimensions: list[int]) -> None:
    if not isinstance(raw, dict) or raw.get("schema") != RAW_SCHEMA or raw.get("ok") is not True:
        raise _ReaderFailure("invalid_helper_response")
    if raw.get("image_path") != str(path) or raw.get("image_sha256") != digest:
        raise _ReaderFailure("helper_image_mismatch")
    if raw.get("source_dimensions") != dimensions or raw.get("orientation") != "up":
        raise _ReaderFailure("helper_dimensions_or_orientation_mismatch")
    timing = raw.get("timing_ms")
    if not isinstance(timing, dict) or any(not _finite(timing.get(k)) or timing[k] < 0 for k in ("ocr", "load_and_ocr")):
        raise _ReaderFailure("invalid_helper_timing")
    _validate_observations(raw.get("observations"), dimensions)


def _validate_observations(observations: Any, dimensions: list[int], roi: list[int] | None = None) -> None:
    if not isinstance(observations, list) or len(observations) > 10000:
        raise _ReaderFailure("invalid_helper_observations")
    for observation in observations:
        if not isinstance(observation, dict):
            raise _ReaderFailure("invalid_helper_observation")
        box = observation.get("box_original_pixels_top_left")
        if (not isinstance(box, list) or len(box) != 4 or not all(_finite(n) for n in box)
                or box[2] <= 0 or box[3] <= 0 or not _inside(box, [-.01, -.01, dimensions[0]+.01, dimensions[1]+.01])):
            raise _ReaderFailure("invalid_helper_box")
        quad = observation.get("quadrilateral_original_pixels_top_left")
        if (not isinstance(quad, list) or len(quad) != 4 or any(not isinstance(p, list) or len(p) != 2
                or not all(_finite(n) for n in p) or not -.01 <= p[0] <= dimensions[0]+.01
                or not -.01 <= p[1] <= dimensions[1]+.01 for p in quad)):
            raise _ReaderFailure("invalid_helper_quadrilateral")
        candidates = observation.get("candidates")
        if (not isinstance(candidates, list) or not 1 <= len(candidates) <= 3 or any(
                not isinstance(c, dict) or not isinstance(c.get("text"), str) or len(c["text"]) > 8192
                or not _finite(c.get("confidence")) or not 0 <= c["confidence"] <= 1 for c in candidates)):
            raise _ReaderFailure("invalid_helper_candidates")
        if roi is not None:
            bounds = [roi[0] - .01, roi[1] - .01, roi[2] + .01, roi[3] + .01]
            if not _inside(box, bounds) or any(not bounds[0] <= p[0] <= bounds[2]
                    or not bounds[1] <= p[1] <= bounds[3] for p in quad):
                raise _ReaderFailure("helper_observation_outside_region")


def _batch_regions(regions: Any, dimensions: list[int]) -> list[dict[str, Any]]:
    """Validate all request geometry before a helper is allowed to start."""
    if not isinstance(regions, list) or len(regions) > 20:
        raise _ReaderFailure("invalid_regions")
    clean, ids, pixels = [], set(), 0
    for region in regions:
        if (not isinstance(region, dict) or not {"id", "box"} <= set(region)
                or set(region) - {"id", "box", "preprocessing"}):
            raise _ReaderFailure("invalid_regions")
        region_id = region["id"]
        if not isinstance(region_id, str) or not region_id.strip() or len(region_id) > 128 or region_id in ids:
            raise _ReaderFailure("invalid_region_id")
        ids.add(region_id)
        preprocessing = region.get("preprocessing", "original")
        if not isinstance(preprocessing, str) or preprocessing not in {"original", "green_text", "white_text"}:
            raise _ReaderFailure("invalid_region_preprocessing")
        box = region["box"]
        if (not _rect(box) or any(not float(n).is_integer() for n in box) or box[0] < 0 or box[1] < 0
                or box[2] > dimensions[0] or box[3] > dimensions[1]):
            raise _ReaderFailure("invalid_region_box")
        box = list(map(int, box))
        width, height = box[2] - box[0], box[3] - box[1]
        area = width * height * 9
        pixels += area
        if area > 12_000_000 or pixels > 48_000_000 or width * 3 > 8192 or height * 3 > 8192:
            raise _ReaderFailure("region_pixel_limit")
        clean.append({"id": region_id, "box": box, "preprocessing": preprocessing})
    if len(json.dumps(clean, separators=(",", ":"), ensure_ascii=False).encode()) > 16384:
        raise _ReaderFailure("region_argument_limit")
    return clean


def _validate_region_response(raw: Any, path: Path, digest: str, dimensions: list[int],
                              requested: list[dict[str, Any]]) -> None:
    if not isinstance(raw, dict) or raw.get("schema") != RAW_REGIONS_SCHEMA or raw.get("ok") is not True:
        raise _ReaderFailure("invalid_helper_response")
    if raw.get("image_path") != str(path) or raw.get("image_sha256") != digest:
        raise _ReaderFailure("helper_image_mismatch")
    if raw.get("source_dimensions") != dimensions or raw.get("orientation") != "up" or raw.get("scale") != 3:
        raise _ReaderFailure("helper_dimensions_or_orientation_mismatch")
    timing = raw.get("timing_ms")
    if not isinstance(timing, dict) or any(not _finite(timing.get(k)) or timing[k] < 0 for k in ("ocr", "load_and_ocr")):
        raise _ReaderFailure("invalid_helper_timing")
    results = raw.get("regions")
    if not isinstance(results, list) or len(results) != len(requested):
        raise _ReaderFailure("helper_region_mismatch")
    for expected, region in zip(requested, results):
        if (not isinstance(region, dict) or region.get("id") != expected["id"]
                or region.get("box_original_pixels_ltrb") != expected["box"] or region.get("scale") != 3
                or region.get("preprocessing") != expected["preprocessing"]):
            raise _ReaderFailure("helper_region_mismatch")
        if region.get("image_sha256") != digest or region.get("source_dimensions") != dimensions:
            raise _ReaderFailure("helper_region_image_mismatch")
        timing = region.get("timing_ms")
        if not isinstance(timing, dict) or any(not _finite(timing.get(k)) or timing[k] < 0 for k in ("ocr", "region_total")):
            raise _ReaderFailure("invalid_helper_timing")
        if type(region.get("ok")) is not bool:
            raise _ReaderFailure("invalid_helper_region_outcome")
        if region["ok"]:
            if region.get("error") is not None:
                raise _ReaderFailure("invalid_helper_region_outcome")
            _validate_observations(region.get("observations"), dimensions, expected["box"])
        elif (region.get("error") not in {"region_image_failed", "native_ocr_failed", "native_ocr_outside_region"}
              or region.get("observations") != []):
            raise _ReaderFailure("invalid_helper_region_outcome")
        elif region.get("error") == "native_ocr_outside_region":
            rejected = region.get("rejected_observation_count")
            if type(rejected) is not int or not 1 <= rejected <= 10000:
                raise _ReaderFailure("invalid_helper_region_outcome")


class NativeTextReader:
    """One-shot native OCR behind explicit executable, time, and byte limits."""

    def __init__(self, executable: Path, timeout_seconds: float = 5.0,
                 max_output_bytes: int = 1_048_576) -> None:
        if not _finite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be finite, positive, and at most 60")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 16_777_216:
            raise ValueError("max_output_bytes must be between 1024 and 16777216")
        self.executable = Path(executable).expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def observe_regions(self, image_path: Path, *, frame_id: str,
                        regions: list[dict[str, Any]]) -> dict[str, Any]:
        """Read <=20 original-pixel LTRB ROIs, at 3x scale in one bounded process.

        Top-level ``ok`` means the batch and source identity were verified;
        every region has its own outcome. Invalid request geometry rejects the
        whole batch before execution. Empty input returns an empty, valid batch
        without calling the helper. A global/source failure clears every ROI's
        observations. Per-region OCR failure never substitutes another region.
        Optional per-region ``preprocessing`` is ``original`` (default) or
        ``green_text``: before scaling, retain only pixels where green >=160,
        green >1.12*red and green >1.4*blue as black on white. This changes OCR
        input only; it does not certify title color or choose a card name.
        ``white_text`` keeps bright near-neutral original pixels (minimum RGB
        170, maximum minus minimum <=45) as black on white. It can separate
        pale digits from colored symbols but never establishes their meaning.
        """
        began = time.monotonic()
        path = Path(image_path).expanduser().resolve()
        requested: list[dict[str, Any]] = []
        result: dict[str, Any] = {"schema": REGIONS_SCHEMA, "ok": False, "error": None,
            "engine": "macos_vision_native_text", "frame_id": frame_id, "parent_frame_id": frame_id,
            "image_path": str(path), "image_sha256": None, "parent_image_sha256": None,
            "source_dimensions": None, "scale": 3, "regions": [], "has_region_errors": False,
            "timing_ms": {}, "runtime_authorization_eligible": False}
        try:
            if not isinstance(frame_id, str) or not frame_id.strip() or len(frame_id) > 256:
                raise _ReaderFailure("invalid_frame_id")
            digest, dimensions = _png_identity(path)
            result.update(image_sha256=digest, parent_image_sha256=digest, source_dimensions=dimensions)
            requested = _batch_regions(regions, dimensions)
            if not requested:
                result["ok"] = True
            else:
                if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                    raise _ReaderFailure("helper_not_prebuilt_or_executable")
                request_began = time.monotonic()
                code, payload = _run_bounded([str(self.executable), str(path), "--regions",
                    json.dumps(requested, separators=(",", ":"), ensure_ascii=False)],
                    self.timeout_seconds, self.max_output_bytes)
                result["timing_ms"]["request_round_trip"] = (time.monotonic() - request_began) * 1000
                if code != 0:
                    raise _ReaderFailure("helper_failed")
                try:
                    raw = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                except (ValueError, UnicodeError) as exc:
                    raise _ReaderFailure("invalid_helper_json") from exc
                _validate_region_response(raw, path, digest, dimensions, requested)
                if _png_identity(path) != (digest, dimensions):
                    raise _ReaderFailure("image_changed_during_ocr")
                result["regions"] = [{**region, "parent_frame_id": frame_id,
                                       "parent_image_sha256": digest} for region in raw["regions"]]
                result["has_region_errors"] = any(not region["ok"] for region in raw["regions"])
                result["timing_ms"].update(raw["timing_ms"])
                result["recognition"] = {key: raw.get(key) for key in ("recognition_level", "cpu_only",
                                                                    "language_correction", "custom_words")}
                result["ok"] = True
        except _ReaderFailure as exc:
            result["error"] = str(exc)
        except (OSError, ValueError, TypeError, OverflowError):
            result["error"] = "reader_failed"
        if not result["ok"]:
            result["has_region_errors"] = bool(requested)
            result["regions"] = [{"id": region["id"], "box_original_pixels_ltrb": region["box"],
                "ok": False, "error": result["error"], "observations": [], "scale": 3,
                "preprocessing": region["preprocessing"],
                "parent_frame_id": frame_id, "image_sha256": result["image_sha256"],
                "parent_image_sha256": result["parent_image_sha256"], "source_dimensions": result["source_dimensions"],
                "timing_ms": {}} for region in requested]
        result["timing_ms"]["total"] = (time.monotonic() - began) * 1000
        return result

    def observe(self, image_path: Path, *, frame_id: str,
                regions: dict[str, Any] | None = None) -> dict[str, Any]:
        began = time.monotonic()
        path = Path(image_path).expanduser().resolve()
        result: dict[str, Any] = {"schema": SCHEMA, "ok": False, "error": None, "engine": "macos_vision_native_text",
            "frame_id": frame_id, "parent_frame_id": frame_id, "image_path": str(path),
            "image_sha256": None, "parent_image_sha256": None, "source_dimensions": None,
            "observations": [], "timing_ms": {}, "runtime_authorization_eligible": False,
            "hud": {"hp": None, "max_hp": None, "energy": None, "energy_max": None,
                    "errors": {}, "evidence": {}}}
        try:
            if not isinstance(frame_id, str) or not frame_id.strip() or len(frame_id) > 256:
                raise _ReaderFailure("invalid_frame_id")
            digest, dimensions = _png_identity(path)
            result.update(image_sha256=digest, parent_image_sha256=digest, source_dimensions=dimensions)
            _regions(dimensions, regions)  # Validate geometry before starting any helper.
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise _ReaderFailure("helper_not_prebuilt_or_executable")
            request_began = time.monotonic()
            code, payload = _run_bounded([str(self.executable), str(path)], self.timeout_seconds,
                                         self.max_output_bytes)
            result["timing_ms"]["request_round_trip"] = (time.monotonic() - request_began) * 1000
            if code != 0:
                raise _ReaderFailure("helper_failed")
            try:
                raw = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            except (ValueError, UnicodeError) as exc:
                raise _ReaderFailure("invalid_helper_json") from exc
            _validate_raw(raw, path, digest, dimensions)
            if _png_identity(path) != (digest, dimensions):
                raise _ReaderFailure("image_changed_during_ocr")
            result.update(ok=True, observations=raw["observations"],
                          hud=extract_hud(raw["observations"], source_dimensions=dimensions, regions=regions))
            result["timing_ms"].update(raw["timing_ms"])
            result["recognition"] = {key: raw.get(key) for key in ("recognition_level", "recognition_revision",
                                                               "language_correction", "custom_words", "cpu_only")}
        except _ReaderFailure as exc:
            result["error"] = str(exc)
            result["hud"]["errors"] = {field: str(exc) for field in ("hp", "max_hp", "energy")}
        except (OSError, ValueError, TypeError, OverflowError):
            result["error"] = "reader_failed"
            result["hud"]["errors"] = {field: "reader_failed" for field in ("hp", "max_hp", "energy")}
        result["timing_ms"]["total"] = (time.monotonic() - began) * 1000
        return result
