"""Read a displayed card cost from saved pixels and source-bound native OCR.

This module has no card catalog, title matching, standard-cost fallback, capture,
or controller dependency. It cannot establish a complete hand or authorize play.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import math
from pathlib import Path
import re


_NUMERAL = re.compile(r"[0-9]{1,3}\Z")
_MAX_IMAGE_BYTES = 64_000_000
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_REGION_PIXELS = 160_000
_MIN_ORB_SEARCH_FRACTION = .25
# Match native_ocr._validate_observations and Swift observationsFitCrop. This
# tolerance covers coordinate arithmetic only, never search/orb/source bounds.
_OBSERVATION_BOUND_TOLERANCE = .01


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError("card_cost_invalid_box")
    left, top, right, bottom = value
    if right <= left or bottom <= top:
        raise ValueError("card_cost_invalid_box")
    return tuple(value)


def _inside(box, outer):
    return box[0] >= outer[0] and box[1] >= outer[1] and box[2] <= outer[2] and box[3] <= outer[3]


def _pixels(box):
    return [math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])]


def _read_bytes(path):
    with path.open("rb") as source:
        raw = source.read(_MAX_IMAGE_BYTES + 1)
    if len(raw) > _MAX_IMAGE_BYTES:
        raise ValueError("card_cost_image_byte_bound")
    return raw


def _orbs(image, search):
    """Find bounded compact warm components, independently of title font size.

    Closing one-pixel antialiasing gaps does not reconstruct or recognize text.
    Components clipped at the search edge remain possible, never confirmed.
    The automatic search is a padded symbol-sized region. A complete plausible
    symbol must span at least a quarter of its shorter side; tiny disconnected
    gold flecks, including pieces inside an orb, are not additional symbols.
    This geometric floor does not depend on OCR values or title recognition.
    """
    from PIL import Image, ImageFilter

    left, top, right, bottom = _pixels(search)
    cropped = image.crop((left, top, right, bottom))
    minimum_side = max(6, min(cropped.size) * _MIN_ORB_SEARCH_FRACTION)
    mask = Image.new("L", cropped.size)
    source, target = cropped.load(), mask.load()
    for y in range(cropped.height):
        for x in range(cropped.width):
            r, g, b = source[x, y]
            if r >= 95 and g >= 65 and .8 * g <= r <= 2.1 * g and b < .75 * g:
                target[x, y] = 255
    pixels = mask.filter(ImageFilter.MaxFilter(3)).load()
    seen = bytearray(cropped.width * cropped.height)
    components = []
    for y in range(cropped.height):
        for x in range(cropped.width):
            if seen[y * cropped.width + x] or not pixels[x, y]:
                continue
            seen[y * cropped.width + x] = 1
            pending = [(x, y)]
            minx = maxx = x
            miny = maxy = y
            count = 0
            while pending:
                cx, cy = pending.pop()
                count += 1
                minx, maxx = min(minx, cx), max(maxx, cx)
                miny, maxy = min(miny, cy), max(maxy, cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if (0 <= nx < cropped.width and 0 <= ny < cropped.height
                            and not seen[ny * cropped.width + nx] and pixels[nx, ny]):
                        seen[ny * cropped.width + nx] = 1
                        pending.append((nx, ny))
            width, height = maxx - minx + 1, maxy - miny + 1
            if (min(width, height) >= minimum_side and .5 <= width / height <= 1.8
                    and .15 <= count / (width * height) <= .9):
                components.append({
                    "box_px": [left + minx, top + miny, left + maxx + 1, top + maxy + 1],
                    "warm_component_pixels": count,
                    "clipped": minx == 0 or miny == 0 or maxx == cropped.width - 1 or maxy == cropped.height - 1,
                })
                if len(components) > 20:
                    return components, True
    return components, False


def _association(image, numeral, orb):
    """Require a numeral near an orb center and warm pixels around it.

    Size limits refer to this orb's pixels, not the unrelated title's font.
    Wide OCR boxes from rotated single digits therefore remain eligible.
    """
    left, top, right, bottom = numeral
    a, b, c, d = orb["box_px"]
    width, height = c - a, d - b
    cx, cy = (left + right) / 2, (top + bottom) / 2
    if (not _inside(numeral, (a - 2, b - 2, c + 2, d + 2))
            or abs(cx - (a + c) / 2) > width * .3
            or abs(cy - (b + d) / 2) > height * .3
            or not .12 * height <= bottom - top <= 1.05 * height
            or right - left > 1.05 * width):
        return None
    # The pixel surround belongs to the orb, so repeated/ambiguous OCR rows
    # must not cause repeated full pixel scans of the same component.
    if "surround" in orb:
        return orb["surround"]
    warm = total = 0
    sectors = [0, 0, 0, 0]
    sector_total = [0, 0, 0, 0]
    pixels = image.load()
    for y in range(b, d):
        for x in range(a, c):
            dx, dy = (x + .5 - (a + c) / 2) / (width / 2), (y + .5 - (b + d) / 2) / (height / 2)
            if .35 <= dx * dx + dy * dy <= .9:
                r, g, blue = pixels[x, y]
                is_warm = r >= 95 and g >= 65 and .8 * g <= r <= 2.1 * g and blue < .75 * g
                total += 1
                warm += int(is_warm)
                sector = (2 if dy >= 0 else 0) + (1 if dx >= 0 else 0)
                sector_total[sector] += 1
                sectors[sector] += int(is_warm)
    ratios = [count / size if size else 0 for count, size in zip(sectors, sector_total)]
    # All four quarters need visible surround. A one-sided fragment is not a
    # complete cost symbol, even when OCR proposes a plausible digit.
    if not total or warm / total < .35 or min(ratios) < .15:
        orb["surround"] = None
        return None
    orb["surround"] = {"orb_box_px": orb["box_px"], "orb_clipped": orb["clipped"],
                       "warm_ring_pixels": warm, "ring_pixels": total,
                       "warm_ring_quadrant_fractions": ratios}
    return orb["surround"]


def read_card_cost(image_path, *, viewport, candidate, observations):
    """Return a partial displayed cost from one native cost-region envelope.

    ``candidate`` needs only ``cost_search_box_px``; its title/name is never read.
    ``observations`` is the source-bound per-region result of
    ``NativeTextReader.observe_regions`` using ``preprocessing='original'``.
    Its bounds must equal the automatic cost search box. Bare OCR lists are not
    accepted because they cannot establish source identity.

    Source, geometry, and protocol violations raise ``ValueError`` so a caller
    can discard all derived state. Ordinary unreadable or ambiguous evidence
    returns ``current_cost=None`` plus issues. No OCR alternative repairs a
    nonnumeric top reading, and confidence cannot settle numeric disagreement.
    ``ambiguous`` and stable ``ambiguity_reasons`` distinguish contradictions
    from plain missing/low-confidence evidence for conservative pass fusion.
    """
    from PIL import Image

    path = Path(image_path)
    raw = _read_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    with Image.open(BytesIO(raw)) as opened:
        if opened.width * opened.height > _MAX_IMAGE_PIXELS:
            raise ValueError("card_cost_image_pixel_bound")
        image = opened.convert("RGB")
    dimensions = [image.width, image.height]
    viewport = _box(viewport)
    if not _inside(viewport, (0, 0, *dimensions)):
        raise ValueError("card_cost_viewport_outside_image")
    if not isinstance(candidate, dict):
        raise ValueError("card_cost_invalid_candidate")
    search = _box(candidate.get("cost_search_box_px"))
    if not _inside(search, viewport) or any(type(v) is not int for v in search):
        raise ValueError("card_cost_search_outside_viewport_or_noninteger")
    if (search[2] - search[0]) * (search[3] - search[1]) > _MAX_REGION_PIXELS:
        raise ValueError("card_cost_region_pixel_bound")
    if not isinstance(observations, dict):
        raise ValueError("card_cost_source_envelope_required")
    if (observations.get("image_sha256") != digest
            or observations.get("parent_image_sha256") != digest
            or observations.get("source_dimensions") != dimensions):
        raise ValueError("card_cost_source_mismatch")
    for key, expected in (("image_sha256", digest), ("parent_image_sha256", digest),
                          ("source_dimensions", dimensions)):
        if key in candidate and candidate[key] != expected:
            raise ValueError("card_cost_candidate_source_mismatch")
    if (observations.get("box_original_pixels_ltrb") != list(search)
            or observations.get("preprocessing") != "original"):
        raise ValueError("card_cost_region_mismatch")
    if "image_path" in observations and observations["image_path"] != str(path.resolve()):
        raise ValueError("card_cost_source_path_mismatch")
    result = {"schema": "veda.card-cost.v1", "image_sha256": digest,
              "parent_image_sha256": digest, "source_dimensions": dimensions,
              "current_cost": None, "issues": [], "ambiguous": False,
              "ambiguity_reasons": [], "runtime_authorized": False,
              "evidence": {"method": "literal_native_numeral_and_original_warm_orb_pixels",
                           "region_id": observations.get("id"), "search_box_px": list(search),
                           "minimum_orb_side_px": max(6, min(search[2]-search[0], search[3]-search[1])
                                                      * _MIN_ORB_SEARCH_FRACTION),
                           "box_px": None, "possible_cost_boxes": [], "orb_boxes": [],
                           "numeral_candidates": []}}
    evidence, issues = result["evidence"], result["issues"]
    if observations.get("ok") is not True:
        issues.append("native cost region failed; no displayed cost established")
    else:
        rows = observations.get("observations")
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError("card_cost_invalid_observations")
        orbs, exceeded = _orbs(image, search)
        evidence["orb_boxes"] = [orb["box_px"] for orb in orbs]
        evidence["orb_candidates"] = orbs
        if exceeded:
            issues.append("cost-symbol component bound exceeded")
            result["ambiguity_reasons"].append("orb_component_bound_exceeded")
        if len(orbs) != 1:
            issues.append("no unique original-pixel cost symbol")
            if len(orbs) > 1:
                result["ambiguity_reasons"].append("multiple_possible_orbs")
        elif orbs[0]["clipped"]:
            issues.append("cost symbol is clipped or occluded at region boundary")
        for index, row in enumerate(rows):
            try:
                x, y, w, h = row["box_original_pixels_top_left"]
                box = _box((x, y, x + w, y + h))
                alternatives = row["candidates"]
                if not isinstance(alternatives, list) or not 1 <= len(alternatives) <= 3:
                    raise ValueError("invalid alternatives")
                for entry in alternatives:
                    confidence = entry["confidence"]
                    if (not isinstance(entry["text"], str) or len(entry["text"]) > 256
                            or type(confidence) not in (int, float) or not math.isfinite(confidence)
                            or not 0 <= confidence <= 1):
                        raise ValueError("invalid OCR candidate")
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"card_cost_malformed_observation:{index}") from error
            tolerance = _OBSERVATION_BOUND_TOLERANCE
            observation_bounds = (search[0]-tolerance, search[1]-tolerance,
                                  search[2]+tolerance, search[3]+tolerance)
            if not _inside(box, observation_bounds):
                raise ValueError(f"card_cost_observation_outside_region:{index}")
            numeric = sorted({entry["text"] for entry in alternatives if _NUMERAL.fullmatch(entry["text"])})
            if not numeric:
                continue
            associations = [proof for orb in orbs if (proof := _association(image, box, orb)) is not None]
            entry = {"observation_index": index, "box_px": _pixels(box),
                     "raw_text": alternatives[0]["text"], "confidence": alternatives[0]["confidence"],
                     "numeric_alternatives": numeric, "orb_associations": associations}
            evidence["numeral_candidates"].append(entry)
            if associations:
                evidence["possible_cost_boxes"].append(entry["box_px"])
        associated = [entry for entry in evidence["numeral_candidates"] if entry["orb_associations"]]
        numeric = {text for entry in associated for text in entry["numeric_alternatives"]}
        if len(numeric) > 1:
            issues.append("conflicting numeric OCR alternatives or observations")
            result["ambiguity_reasons"].append("conflicting_numeric_readings")
        if len(associated) != 1:
            issues.append("no unique observed numeral with cost-symbol context")
            if len(associated) > 1:
                result["ambiguity_reasons"].append("multiple_numeric_observations")
        elif not _NUMERAL.fullmatch(associated[0]["raw_text"]):
            issues.append("top OCR reading is not a literal numeral; alternatives cannot repair it")
        elif associated[0]["confidence"] < .8:
            issues.append("top literal numeral confidence is insufficient")
        elif len(associated[0]["orb_associations"]) != 1:
            issues.append("numeral has multiple possible cost-symbol associations")
            result["ambiguity_reasons"].append("multiple_orb_associations")
        if not issues:
            selected = associated[0]
            result["current_cost"] = int(selected["raw_text"])
            evidence.update(box_px=selected["box_px"], raw_text=selected["raw_text"],
                            numeric_alternatives=selected["numeric_alternatives"],
                            observation_index=selected["observation_index"],
                            orb=selected["orb_associations"][0])
    result["ambiguous"] = bool(result["ambiguity_reasons"])
    if hashlib.sha256(_read_bytes(path)).hexdigest() != digest:
        raise ValueError("card_cost_source_changed_during_analysis")
    return result
