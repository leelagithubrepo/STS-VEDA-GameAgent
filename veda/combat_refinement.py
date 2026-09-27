"""One bounded, source-bound crop batch for partial saved combat evidence."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import time

from .combat_evidence import _overlap, extract_combat_evidence
from .energy_refinement import _identity_matches as _energy_identity_matches, _OPERATIONAL_ERRORS, _REGION_ERRORS
from .native_ocr import (
    REGIONS_SCHEMA, SCHEMA, _ReaderFailure, _png_identity, _regions,
    _validate_observations, extract_hud,
)


_CROPS = {
    "combat-block": (.14, .62, .24, .78),
    "combat-enemy-health": (.4, .50, .94, .79),
    "combat-intent": (.4, .25, .94, .58),
}


def _identity_matches(envelope, *, path, digest, dimensions, frame_id, schema):
    try:
        _energy_identity_matches(envelope, path=path, digest=digest, dimensions=dimensions,
                                 frame_id=frame_id, schema=schema)
    except ValueError as exc:
        raise ValueError("combat_reader_source_mismatch") from exc
    if envelope.get("parent_image_sha256") != digest or envelope.get("parent_frame_id") != frame_id:
        raise ValueError("combat_reader_parent_source_mismatch")


def combat_region_requests(viewport):
    """Fixed geometry only; no labels, expected numbers, or per-image tuning."""
    if (not isinstance(viewport, (tuple, list)) or len(viewport) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in viewport)
            or not 0 <= viewport[0] < viewport[2] or not 0 <= viewport[1] < viewport[3]):
        raise ValueError("combat_reader_invalid_viewport")
    x, y, right, bottom = viewport
    width, height = right-x, bottom-y
    return [{"id": name, "box": [math.floor(x+l*width), math.floor(y+t*height),
                                  math.ceil(x+r*width), math.ceil(y+b*height)],
             "preprocessing": "white_text" if name == "combat-intent" else "original"}
            for name, (l, t, r, b) in _CROPS.items()]


def _validate_crop(response, request, *, path, digest, dimensions, frame_id):
    """Same strict source protocol as the energy pass, with declared ROI modes."""
    region = response
    if (not isinstance(region, dict) or region.get("id") != request["id"]
            or region.get("box_original_pixels_ltrb") != request["box"]
            or region.get("preprocessing") != request["preprocessing"]
            or region.get("image_sha256") != digest
            or region.get("parent_image_sha256") != digest
            or region.get("source_dimensions") != dimensions
            or region.get("parent_frame_id") != frame_id
            or region.get("frame_id", frame_id) != frame_id
            or region.get("image_path", str(path)) != str(path)
            or type(region.get("scale")) is not int or region["scale"] != 3
            or type(region.get("ok")) is not bool):
        raise ValueError("combat_reader_region_mismatch")
    if region["ok"]:
        if region.get("error") is not None:
            raise ValueError("combat_reader_invalid_region_outcome")
        try:
            _validate_observations(region.get("observations"), dimensions, request["box"])
        except _ReaderFailure as exc:
            raise ValueError("combat_reader_invalid_region:" + str(exc)) from exc
    else:
        if region.get("error") not in _REGION_ERRORS | _OPERATIONAL_ERRORS or region.get("observations") != []:
            raise ValueError("combat_reader_invalid_region_outcome")
        if region["error"] == "native_ocr_outside_region":
            count = region.get("rejected_observation_count")
            if type(count) is not int or not 1 <= count <= 10000:
                raise ValueError("combat_reader_invalid_rejection_metadata")
    return region


def _intent_requests(path, *, viewport, digest, dimensions):
    from .intent_evidence import intent_crop_requests

    proposal = intent_crop_requests(path, viewport=viewport)
    if (not isinstance(proposal, dict) or proposal.get("schema") != "veda.intent-crop-requests.v1"
            or proposal.get("image_path") != str(path)
            or proposal.get("image_sha256") != digest or proposal.get("parent_image_sha256") != digest
            or proposal.get("source_dimensions") != dimensions or proposal.get("viewport") != list(viewport)
            or proposal.get("runtime_authorized") is not False
            or proposal.get("controller_authorized") is not False):
        raise ValueError("combat_reader_intent_proposal_source_mismatch")
    requests = proposal.get("regions")
    # Six primary icons plus at most three geometric alternate rows. Both
    # preprocessing modes of every row remain in the same native batch (20
    # regions including Block and HP); an alternative never replaces a veto.
    if not isinstance(requests, list) or len(requests) > 9:
        raise ValueError("combat_reader_intent_proposal_bound")
    ids = set()
    for request in requests:
        if (not isinstance(request, dict) or not isinstance(request.get("id"), str)
                or not request["id"].startswith("intent-") or len(request["id"]) > 64
                or request["id"] in ids or request.get("preprocessing") != "white_text"):
            raise ValueError("combat_reader_invalid_intent_proposal")
        ids.add(request["id"])
        box = request.get("box")
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                or not viewport[0] <= box[0] < box[2] <= viewport[2]
                or not viewport[1] <= box[1] < box[3] <= viewport[3]):
            raise ValueError("combat_reader_invalid_intent_proposal")
    return deepcopy(requests), proposal


def _merge_field(original, focused, kind):
    """Cluster the same visible pixels, retaining every rejected contradiction.

    Agreement between passes yields one depiction with both proofs. Repetition
    inside one pass is never silently deduplicated. Distinct enemy anchors stay
    distinct; their order is visual location, not a verified target order.
    """
    key = "enemy_hp_candidates" if kind == "enemy_health" else "player_block_candidates"
    entries = []
    for source, reading in (("original", original), ("focused", focused)):
        for accepted, candidates in ((True, reading[key]), (False, reading["rejected_numeric_candidates"])):
            for candidate in candidates:
                if not accepted and candidate["kind"] != kind:
                    continue
                item = deepcopy(candidate)
                item["proof"]["source_pass"] = source
                entries.append((accepted, source, item))
    if len(entries) > 256:
        raise ValueError("combat_reader_merge_bound")
    remaining = set(range(len(entries)))
    accepted_out, rejected_out, conflicts = [], [], []
    while remaining:
        group = {min(remaining)}
        remaining -= group
        pending = list(group)
        while pending:
            current = pending.pop()
            neighbors = {i for i in remaining if _overlap(entries[current][2]["box"], entries[i][2]["box"])}
            remaining -= neighbors
            group |= neighbors
            pending.extend(neighbors)
        members = [entries[i] for i in sorted(group)]
        valid = [item for ok, _, item in members if ok]
        rejected = [item for ok, _, item in members if not ok]
        rejected_out.extend(rejected)
        if not valid:
            continue
        values = {(item["hp"], item["max_hp"]) if kind == "enemy_health" else item["value"] for item in valid}
        passes = [source for ok, source, _ in members if ok]
        # A rejected same-pixel reading still vetoes an asserted reading. This
        # includes uncertain/conflicting alternatives, not just accepted tops.
        ambiguous = bool(rejected) or len(values) != 1 or len(passes) != len(set(passes))
        if ambiguous:
            reason = "overlapping_numeric_evidence_across_passes"
            conflicts.append({"kind": kind, "boxes": [item["box"] for _, _, item in members], "reason": reason})
            for item in valid:
                rejected_out.append({"kind": kind, **item, "issues": [*item["issues"], reason]})
            continue
        chosen = deepcopy(valid[-1])
        chosen["proof"]["corroborating_readings"] = [
            {"source_pass": source, "box": deepcopy(item["box"]), "proof": deepcopy(item["proof"])}
            for ok, source, item in members if ok]
        accepted_out.append(chosen)
    accepted_out.sort(key=lambda item: (item["box"][0], item["box"][1]))
    return accepted_out, rejected_out, conflicts


def refine_combat_reading(image_path, *, viewport, native, reader, frame_id):
    """Return partial evidence and a validated intent ROI after one crop call.

    Source/protocol mismatch raises ValueError so the coordinator can clear all
    derived readings. Operational or explicit empty region failures retain only
    valid original evidence. Intent text is returned for a separate icon-aware
    reader; it never establishes an attack or incoming-damage value here.
    """
    began = time.monotonic()
    path = Path(image_path).expanduser().resolve()
    if not isinstance(frame_id, str) or not frame_id.strip() or len(frame_id) > 256:
        raise ValueError("combat_reader_invalid_frame_id")
    try:
        digest, dimensions = _png_identity(path)
        geometry = _regions(dimensions, {"viewport": viewport})
        _identity_matches(native, path=path, digest=digest, dimensions=dimensions,
                          frame_id=frame_id, schema=SCHEMA)
        if native.get("ok") is not True or native.get("error") is not None or not isinstance(native.get("hud"), dict):
            raise ValueError("combat_reader_invalid_original")
        _validate_observations(native.get("observations"), dimensions)
    except _ReaderFailure as exc:
        raise ValueError("combat_reader_invalid_original:" + str(exc)) from exc
    expected_hud = extract_hud(native["observations"], source_dimensions=dimensions, regions={"viewport": viewport})
    if (native["hud"] != expected_hud or any(type(native["hud"].get(field)) is not type(expected_hud[field])
            for field in ("hp", "max_hp", "energy", "energy_max"))):
        raise ValueError("combat_reader_original_hud_mismatch")
    original = extract_combat_evidence(path, viewport=viewport, observations=native["observations"])
    requests = combat_region_requests(viewport)
    intent_requests, intent_proposal = _intent_requests(path, viewport=viewport, digest=digest, dimensions=dimensions)
    # Neither preprocessing mode can certify an attack alone. Request both
    # views of every proposed icon (or the fallback strip) in this same batch.
    intent_requests = intent_requests or requests[2:]
    paired_intents = [{**deepcopy(request), "id": request["id"]+suffix, "preprocessing": mode}
                      for request in intent_requests
                      for suffix, mode in (("-original", "original"), ("-white", "white_text"))]
    requests = requests[:2] + paired_intents
    if original["image_sha256"] != digest or original["source_dimensions"] != dimensions:
        raise ValueError("combat_reader_image_changed")
    response = reader.observe_regions(path, frame_id=frame_id, regions=deepcopy(requests))
    _identity_matches(response, path=path, digest=digest, dimensions=dimensions,
                      frame_id=frame_id, schema=REGIONS_SCHEMA)
    if response.get("scale") != 3 or type(response.get("scale")) is not int:
        raise ValueError("combat_reader_scale_mismatch")
    if type(response.get("ok")) is not bool:
        raise ValueError("combat_reader_invalid_outcome")
    if response["ok"]:
        if response.get("error") is not None:
            raise ValueError("combat_reader_invalid_outcome")
    elif response.get("error") not in _OPERATIONAL_ERRORS:
        raise ValueError("combat_reader_integrity_failure:" + str(response.get("error")))
    regions = response.get("regions")
    if not isinstance(regions, list) or len(regions) != len(requests):
        raise ValueError("combat_reader_region_count_mismatch")
    # A malformed sibling invalidates the whole batch, including intent evidence.
    validated = []
    for request, region in zip(requests, regions):
        checked = _validate_crop(region, request, path=path, digest=digest, dimensions=dimensions, frame_id=frame_id)
        if ((not response["ok"] and (checked["ok"] or checked["error"] != response["error"]))
                or (response["ok"] and not checked["ok"] and checked["error"] not in _REGION_ERRORS)):
            raise ValueError("combat_reader_invalid_region_outcome")
        validated.append(checked)
    focused_rows = deepcopy(validated[0]["observations"] + validated[1]["observations"])
    focused = extract_combat_evidence(path, viewport=viewport, observations=focused_rows)
    if focused["image_sha256"] != digest or focused["source_dimensions"] != dimensions:
        raise ValueError("combat_reader_image_changed")
    enemies, rejected_enemies, enemy_conflicts = _merge_field(original, focused, "enemy_health")
    blocks, rejected_blocks, block_conflicts = _merge_field(original, focused, "player_block")
    block = blocks[0]["value"] if len(blocks) == 1 else None
    conflicts = enemy_conflicts + block_conflicts
    if len(blocks) > 1:
        conflicts.append({"kind": "player_block", "reason": "multiple_distinct_shield_backed_numerals",
                          "boxes": [item["box"] for item in blocks]})
    merged = deepcopy(original)
    merged.update(player_block=block, player_block_candidates=blocks, enemy_hp_candidates=enemies,
                  rejected_numeric_candidates=rejected_enemies + rejected_blocks)
    merged["issues"] = original["issues"][:2]
    if block is None:
        merged["issues"].append("No unique agreed shield-backed block numeral; absence does not imply zero.")
    if conflicts:
        merged["issues"].append("Conflicting or repeated same-pixel numeric evidence is withheld.")
    merged["provenance"].update(method="source_bound_full_and_focused_saved_pixel_evidence",
                                sampled_pixels=original["provenance"]["sampled_pixels"] + focused["provenance"]["sampled_pixels"],
                                numeric_candidates_examined=original["provenance"]["numeric_candidates_examined"] + focused["provenance"]["numeric_candidates_examined"])
    try:
        if _png_identity(path) != (digest, dimensions):
            raise ValueError("combat_reader_image_changed")
    except _ReaderFailure as exc:
        raise ValueError("combat_reader_image_changed:" + str(exc)) from exc
    return {"combat_evidence": merged, "refinement": {
        "schema": "veda.combat-refinement.v1", "status": "conflict" if conflicts else "processed",
        "image_path": str(path), "image_sha256": digest, "source_dimensions": dimensions,
        "frame_id": frame_id, "viewport": list(viewport), "geometry": geometry,
        "requested_regions": requests, "region_count": len(requests), "regions": deepcopy(validated),
        "intent_region": deepcopy(validated[2]), "intent_regions": deepcopy(validated[2:]),
        "intent_crop_proposal": deepcopy(intent_proposal), "original": original, "focused": focused,
        "conflicts": conflicts, "reader_timing_ms": deepcopy(response.get("timing_ms", {})),
        "timing_ms": (time.monotonic()-began)*1000,
        "runtime_authorized": False, "controller_authorized": False,
    }}
