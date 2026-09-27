"""Literal attack numerals plus a verified red weapon in immutable saved pixels.

This does not identify an enemy roster, classify companion effects, infer a
missing hit count, or authorize an action. No OCR, capture or model is invoked.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import time

from .native_ocr import _ReaderFailure, _validate_observations


_MAX_BYTES = 64_000_000
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_OBSERVATIONS = 2000
_MAX_NUMERIC_CANDIDATES = 64
_MAX_SAMPLE_PIXELS = 2_000_000
_MAX_MERGE_SOURCES = 20
_MAX_TEMPLATES = 6
_GRID = 24
_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "data" / "intent_templates.json"
_ATTACK = re.compile(r"([0-9]{1,4})(?:\s*[xX×]\s*([0-9]{1,3}))?\Z")


def _read(path, limit=_MAX_BYTES):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("intent_evidence_byte_bound")
    return raw


def _viewport(value, dimensions):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not 0 <= value[0] < value[2] <= dimensions[0]
            or not 0 <= value[1] < value[3] <= dimensions[1]):
        raise ValueError("intent_evidence_invalid_viewport")
    return tuple(value)


def _inside(box, region):
    return region[0] <= box[0] and region[1] <= box[1] and box[2] <= region[2] and box[3] <= region[3]


def _red(rgb):
    r, g, b = rgb
    return r >= 120 and r > 1.35*g and r > 1.18*b and b >= .6*g


def _green(rgb):
    r, g, b = rgb
    return g >= 110 and g > 1.08*r and g > 1.25*b


def _white(rgb):
    return min(rgb) >= 165 and max(rgb)-min(rgb) <= 50


def _templates():
    raw = _read(_TEMPLATE_PATH, 32_000)
    try:
        data = json.loads(raw)
        entries = data["templates"]
        if (data["schema"] != "veda.intent-icon-templates.v1" or data["grid_size"] != _GRID
                or not isinstance(entries, list) or not 1 <= len(entries) <= _MAX_TEMPLATES):
            raise ValueError
        ids = set()
        for entry in entries:
            rows = entry["mask_rows"]
            if (not isinstance(entry["id"], str) or not entry["id"] or entry["id"] in ids
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["source_sha256"])
                    or len(rows) != _GRID or any(not isinstance(row, str)
                        or len(row) != _GRID or set(row)-{"0", "1"} for row in rows)
                    or not 50 <= sum(row.count("1") for row in rows) <= 400):
                raise ValueError
            ids.add(entry["id"])
            box = entry["source_box_px"]
            if (len(box) != 4 or any(type(v) is not int or v < 0 for v in box)
                    or box[0] >= box[2] or box[1] >= box[3]):
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("intent_evidence_invalid_templates") from exc
    return entries, hashlib.sha256(raw).hexdigest()


class _Pixels:
    def __init__(self, image, viewport):
        self.image, self.viewport, self.sampled = image, viewport, 0

    def crop(self, box, *, limit=100_000):
        v = self.viewport
        # Inward rounding never samples desktop pixels outside the viewport.
        a, b = math.ceil(max(box[0], v[0])), math.ceil(max(box[1], v[1]))
        c, d = math.floor(min(box[2], v[2])), math.floor(min(box[3], v[3]))
        area = max(0, c-a)*max(0, d-b)
        if not area or area > limit or self.sampled+area > _MAX_SAMPLE_PIXELS:
            raise ValueError("intent_evidence_pixel_sample_bound")
        self.sampled += area
        image = self.image.crop((a, b, c, d))
        values = list(image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata())
        return [a, b, c, d], values


def _components(mask, width, height, *, minimum=8):
    seen, found = bytearray(len(mask)), []
    for start, on in enumerate(mask):
        if not on or seen[start]:
            continue
        pending, points = [start], []
        seen[start] = 1
        while pending:
            point = pending.pop()
            points.append(point)
            x, y = point % width, point // width
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if 0 <= nx < width and 0 <= ny < height:
                    target = ny*width+nx
                    if mask[target] and not seen[target]:
                        seen[target] = 1
                        pending.append(target)
        if len(points) >= minimum:
            found.append(points)
        if len(found) > 256:
            raise ValueError("intent_evidence_component_bound")
    return found


def _number_pixels(pixels, box):
    bounds, rgb = pixels.crop(box)
    white = sum(_white(value) for value in rgb)
    green = sum(_green(value) for value in rgb)
    # Green curls can be read as C, 2 or another digit. They may not become a
    # literal damage value just because OCR emitted an integer with confidence.
    clear = white >= max(6, len(rgb)*.035) and green <= max(3, white*.05)
    return {"confirmed": clear, "sample_box_px": bounds, "white_pixels": white,
            "green_pixels": green, "sample_pixels": len(rgb),
            "method": "neutral_white_text_without_green_curl_contamination"}


def _sword_components(pixels, search, templates, view_height, *, limit=100_000):
    from PIL import Image

    bounds, rgb = pixels.crop(search, limit=limit)
    a, b, c, d = bounds
    width, height = c-a, d-b
    mask = bytearray(_red(value) for value in rgb)
    matches = []
    for points in _components(mask, width, height):
        xs, ys = [p % width for p in points], [p // width for p in points]
        left, top, r, bot = min(xs), min(ys), max(xs)+1, max(ys)+1
        w, sh = r-left, bot-top
        if (not .7 <= w/sh <= 1.4 or not .018*view_height <= sh <= .07*view_height
                or not .15 <= len(points)/(w*sh) <= .55):
            continue
        component = Image.new("L", (w, sh))
        for point in points:
            component.putpixel((point % width-left, point // width-top), 255)
        small = component.resize((_GRID, _GRID), Image.Resampling.NEAREST)
        values = list(small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata())
        present = {i for i, value in enumerate(values) if value}
        best = None
        for template in templates:
            expected = {i for i, bit in enumerate("".join(template["mask_rows"])) if bit == "1"}
            score = len(present & expected)/len(present | expected)
            if best is None or score > best[0]:
                best = score, template
        if best[0] >= .68:
            template = best[1]
            matches.append({"box_px": [a+left, b+top, a+r, b+bot], "template_iou": best[0],
                            "template_id": template["id"], "red_pixels": len(points),
                            "template_source_sha256": template["source_sha256"],
                            "template_source_box_px": template["source_box_px"]})
    return bounds, matches


def _sword_proof(pixels, box, templates, view_height):
    x, y, right, bottom = box
    h = bottom-y
    # Sample every pixel permitted by the unchanged center/height association
    # below. A narrower OCR box must not truncate an otherwise valid blade.
    # Maximum half-height/width are 1.75h and 1.4*1.75h respectively.
    search = [x-2.65*h, y-4.25*h, right+4.05*h, y+2.15*h]
    v = pixels.viewport
    sample_width = max(0, math.floor(min(search[2], v[2]))-math.ceil(max(search[0], v[0])))
    sample_height = max(0, math.floor(min(search[3], v[3]))-math.ceil(max(search[1], v[1])))
    if sample_width*sample_height > 100_000:
        # Wide numeric-containing overlay text can exceed a local proof budget.
        # Withhold this candidate while retaining its raw row for conflicts;
        # do not spend the pixels or turn it into a whole-frame source failure.
        return {"confirmed": False, "matches": [], "sample_box_px": None,
                "requested_search_box_px": search, "sampled_pixels": 0,
                "issues": ["sword_search_exceeds_per_candidate_pixel_bound"],
                "method": "original_red_weapon_component_and_normalized_mask",
                "companion_icons_classified": False}
    bounds, all_matches = _sword_components(pixels, search, templates, view_height)
    matches = []
    for entry in all_matches:
        a, b, c, d = entry["box_px"]
        if (.7*h <= d-b <= 3.5*h and x-.2*h <= (a+c)/2 <= right+1.6*h
                and y-2.5*h <= (b+d)/2 <= y+.4*h):
            matches.append(entry)
    return {"confirmed": len(matches) == 1, "matches": matches, "sample_box_px": bounds,
            "method": "original_red_weapon_component_and_normalized_mask",
            "companion_icons_classified": False}


def _numeral_crop(pixels, icon):
    """Select a whole white row, or retain the original wide icon crop.

    This selects pixels before OCR. It neither recognizes a digit nor discards
    unresolved small fragments that might belong to a multiplier.
    """
    a, b, c, d = icon["box_px"]
    sh = d-b
    vx, vy, vr, vb = pixels.viewport
    wide = [max(math.ceil(vx), math.floor(a-1.4*sh)),
            max(math.ceil(vy), math.floor(b+.4*sh)),
            min(math.floor(vr), math.ceil(c+.8*sh)),
            min(math.floor(vb), math.ceil(d+.85*sh))]
    row_region = [wide[0], max(math.ceil(vy), math.floor(b+.6*sh)),
                  wide[2], min(math.floor(vb), math.ceil(d+.5*sh))]
    # Audit the complete previous crop, including above/below the expected
    # baseline. An outlying or detached xN fragment must not disappear merely
    # because it lies outside the region used to select a likely text row.
    bounds, rgb = pixels.crop(wide)
    left, top, right, bottom = bounds
    result = {"box_px": wide, "selection": "original_wide_fallback", "search_box_px": bounds,
              "row_region_px": row_region,
              "white_components": [], "issues": []}
    try:
        components = _components(bytearray(_white(value) for value in rgb), right-left,
                                 bottom-top, minimum=1)
    except ValueError as exc:
        if str(exc) != "intent_evidence_component_bound":
            raise
        result["issues"].append("white_component_bound")
        return result
    for points in components:
        xs, ys = [p % (right-left) for p in points], [p // (right-left) for p in points]
        result["white_components"].append({"box_px": [left+min(xs), top+min(ys),
                                                       left+max(xs)+1, top+max(ys)+1],
                                            "pixels": len(points)})
    boxes = [entry["box_px"] for entry in result["white_components"]]
    glyphs = sorted(box for box in boxes if .25*sh <= box[3]-box[1] <= .75*sh and _inside(box, row_region))
    reason = None
    if any(box[0] <= left or box[1] <= top or box[2] >= right or box[3] >= bottom for box in boxes):
        reason = "white_component_touches_search_boundary"
    elif not 1 <= len(glyphs) <= 8:
        reason = "no_unique_bounded_white_row"
    elif max(box[3] for box in glyphs)-min(box[3] for box in glyphs) > .25*max(box[3]-box[1] for box in glyphs):
        reason = "white_row_baselines_disagree"
    elif any(glyphs[i+1][0]-glyphs[i][2] > .65*sh for i in range(len(glyphs)-1)):
        reason = "white_row_gap_is_ambiguous"
    else:
        row = [min(box[0] for box in glyphs), min(box[1] for box in glyphs),
               max(box[2] for box in glyphs), max(box[3] for box in glyphs)]
        pad = math.ceil(.2*(row[3]-row[1]))
        crop = [row[0]-pad, row[1]-pad, row[2]+pad, row[3]+pad]
        if not _inside(crop, wide):
            reason = "padded_white_row_outside_wide_crop"
        elif any(not _inside(box, crop) for box in boxes):
            # Even a one-pixel fragment can be part of a split xN. Retain the
            # wide source crop rather than silently trimming such evidence.
            reason = "unresolved_white_component_outside_row"
        else:
            result.update(box_px=crop, selection="neutral_white_row")
    if reason:
        result["issues"].append(reason)
    return result


def _alternative_crops(icon, primary, crop):
    """Offer context-preserving alternatives; never replace the primary proof.

    A tight glyph crop can make both OCR modes misread the same damaged glyph.
    Only the padded row and fixed row are offered. The original wider crop is
    retained so fragments and contradictory readings are still examined.
    """
    if crop["selection"] != "original_wide_fallback":
        return []
    a, b, c, d = icon["box_px"]
    sh = d-b
    wide = primary["box"]
    glyphs = sorted(entry["box_px"] for entry in crop["white_components"]
                    if .25*sh <= entry["box_px"][3]-entry["box_px"][1] <= .75*sh
                    and _inside(entry["box_px"], crop["row_region_px"]))
    candidates = []
    if (1 <= len(glyphs) <= 8
            and max(box[3] for box in glyphs)-min(box[3] for box in glyphs)
                <= .25*max(box[3]-box[1] for box in glyphs)
            and all(glyphs[i+1][0]-glyphs[i][2] <= .65*sh for i in range(len(glyphs)-1))):
        row = [min(box[0] for box in glyphs), min(box[1] for box in glyphs),
               max(box[2] for box in glyphs), max(box[3] for box in glyphs)]
        pad = math.ceil(.5*(row[3]-row[1]))
        candidates.append(("padded", [row[0]-pad, row[1]-pad, row[2]+pad, row[3]+pad]))
    candidates.append(("fixed", [math.floor(a-.65*sh), math.floor(b+.6*sh),
                                  math.ceil(c+.25*sh), math.ceil(d+.5*sh)]))
    output, seen = [], {tuple(wide)}
    for selection, box in candidates:
        if tuple(box) in seen or not _inside(box, wide):
            continue
        seen.add(tuple(box))
        output.append({"id": primary["id"].replace("intent-", "intent-alt-", 1)+"-"+selection,
                       "primary_region_id": primary["id"], "box_px": box,
                       "selection": selection+"_row_with_primary_retained",
                       "issues": ["Alternative geometry does not establish complete intent or an absent multiplier."]})
    return output


def intent_crop_requests(image_path, *, viewport):
    """Propose six primary weapon crops and bounded alternatives, nine total.

    Empty ``regions`` permits the caller's fixed-strip fallback. No card or
    damage values, OCR text, enemy identities, or expected labels are inputs.
    """
    from PIL import Image

    path = Path(image_path).expanduser().resolve()
    raw = _read(path)
    digest = hashlib.sha256(raw).hexdigest()
    with Image.open(BytesIO(raw)) as opened:
        if opened.format != "PNG" or opened.width*opened.height > _MAX_IMAGE_PIXELS:
            raise ValueError("intent_evidence_image_format_or_pixel_bound")
        image = opened.convert("RGB")
    dimensions = [image.width, image.height]
    view = _viewport(viewport, dimensions)
    vx, vy, vr, vb = view
    width, height = vr-vx, vb-vy
    search = [vx+.38*width, vy+.18*height, vx+.96*width, vy+.62*height]
    pixels = _Pixels(image, view)
    templates, template_sha = _templates()
    issues = []
    if (search[2]-search[0])*(search[3]-search[1]) > 750_000:
        matches = []
        issues.append("intent_icon_search_pixel_bound; use bounded caller fallback")
    else:
        _, matches = _sword_components(pixels, search, templates, height, limit=750_000)
    matches.sort(key=lambda item: (item["box_px"][0], item["box_px"][1]))
    regions, crop_evidence = [], []
    for index, icon in enumerate(matches[:6]):
        crop = _numeral_crop(pixels, icon)
        regions.append({"id": f"intent-{index}", "box": crop["box_px"], "preprocessing": "white_text"})
        crop_evidence.append({"id": f"intent-{index}", **crop})
    primary_count = len(regions)
    alternatives = []
    for icon, primary, crop in zip(matches[:6], regions[:], crop_evidence):
        alternatives.extend(_alternative_crops(icon, primary, crop))
    # Primary spatial order, then padded/fixed order, never OCR or expected
    # values. At most nine proposals become eighteen paired native crops.
    requested_alternatives = max(0, min(len(alternatives), 9-primary_count))
    for index, alternative in enumerate(alternatives):
        requested = index < requested_alternatives
        alternative.update(requested=requested, omitted_reason=None if requested else "nine_region_budget")
        if requested:
            regions.append({"id": alternative["id"], "box": alternative["box_px"],
                            "preprocessing": "white_text"})
    omitted_alternatives = len(alternatives)-requested_alternatives
    if omitted_alternatives:
        issues.append("intent_alternative_proposals_omitted_at_nine_region_limit")
    if len(matches) > 6:
        issues.append("intent_icon_proposals_omitted_at_six_region_limit")
    if not matches and not issues:
        issues.append("no_verified_sword_components; caller may use fixed-strip fallback")
    if hashlib.sha256(_read(path)).hexdigest() != digest:
        raise ValueError("intent_evidence_source_changed")
    searched = not any("search_pixel_bound" in issue for issue in issues)
    return {"schema": "veda.intent-crop-requests.v1", "image_path": str(path),
            "image_sha256": digest, "parent_image_sha256": digest,
            "source_dimensions": dimensions, "viewport": list(viewport),
            "regions": regions, "icon_evidence": matches[:6], "crop_evidence": crop_evidence,
            "primary_region_count": primary_count, "alternative_region_count": requested_alternatives,
            "alternative_proposal_count": len(alternatives), "omitted_alternative_count": omitted_alternatives,
            "alternative_evidence": alternatives,
            "search_complete": searched,
            "omitted_icon_count": max(0, len(matches)-6) if searched else None,
            "issues": issues, "runtime_authorized": False, "controller_authorized": False,
            "provenance": {"template_manifest_sha256": template_sha,
                           "search_region_px": search, "sampled_pixels": pixels.sampled,
                           "selection": "spatial weapon components only; no OCR or expected values"}}


def _literal(row):
    parsed = []
    for candidate in row["candidates"]:
        match = _ATTACK.fullmatch(candidate["text"].strip())
        parsed.append((int(match[1]), int(match[2]) if match[2] is not None else None) if match else None)
    issues = []
    if parsed[0] is None:
        issues.append("top_text_is_not_an_exact_attack_literal")
    if row["candidates"][0]["confidence"] < .8:
        issues.append("top_confidence_below_0.8")
    if any(value is None or value != parsed[0] for value in parsed[1:]):
        issues.append("alternative_attack_readings_disagree")
    if parsed[0] is not None and parsed[0][1] == 0:
        issues.append("nonpositive_explicit_hit_count")
    return parsed[0] if not issues else None, parsed, issues


def _overlap(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return area > .25*smaller


def extract_intent_evidence(image_path, *, viewport, observations, ocr_preprocessing="original"):
    """Parse one source/pass; the caller merges independent OCR passes later."""
    from PIL import Image

    began = time.monotonic()
    if ocr_preprocessing not in ("original", "white_text"):
        raise ValueError("intent_evidence_invalid_preprocessing")
    if not isinstance(observations, list) or len(observations) > _MAX_OBSERVATIONS:
        raise ValueError("intent_evidence_observation_bound")
    path = Path(image_path).expanduser().resolve()
    raw = _read(path)
    digest = hashlib.sha256(raw).hexdigest()
    with Image.open(BytesIO(raw)) as opened:
        if opened.format != "PNG" or opened.width*opened.height > _MAX_IMAGE_PIXELS:
            raise ValueError("intent_evidence_image_format_or_pixel_bound")
        image = opened.convert("RGB")
    dimensions = [image.width, image.height]
    view = _viewport(viewport, dimensions)
    try:
        _validate_observations(observations, dimensions)
    except _ReaderFailure as exc:
        raise ValueError("intent_evidence_invalid_observation:" + str(exc)) from exc
    templates, template_sha = _templates()
    vx, vy, vr, vb = view
    width, height = vr-vx, vb-vy
    intent_region = [vx+.4*width, vy+.23*height, vx+.94*width, vy+.62*height]
    pixels = _Pixels(image, view)
    proposed, rejected, numeric_count = [], [], 0
    for index, row in enumerate(observations):
        x, y, w, h = row["box_original_pixels_top_left"]
        box = [x, y, x+w, y+h]
        if (not _inside(box, intent_region) or not .008*height <= h <= .04*height
                or not any(re.search(r"[0-9]", c["text"]) for c in row["candidates"])):
            continue
        numeric_count += 1
        if numeric_count > _MAX_NUMERIC_CANDIDATES:
            raise ValueError("intent_evidence_numeric_candidate_bound")
        value, parsed, issues = _literal(row)
        icon = _sword_proof(pixels, box, templates, height)
        text_pixels = _number_pixels(pixels, box)
        if not icon["confirmed"]:
            issues.append("no_unique_verified_sword_component")
            issues.extend(icon.get("issues", []))
        if not text_pixels["confirmed"]:
            issues.append("numeric_pixels_unconfirmed_or_green_contaminated")
        entry = {"box": box, "icon_evidence": icon,
                 "proof": {"observation_index": index, "raw_observation": deepcopy(row),
                           "ocr_preprocessing": ocr_preprocessing,
                           "parsed_literals": parsed, "number_pixels": text_pixels,
                           "location_region_px": intent_region}, "issues": issues}
        if issues:
            rejected.append(entry)
        else:
            damage, hits = value
            proposed.append({**entry, "damage_per_hit": damage, "hits": hits,
                             "multiplier_visible": hits is not None,
                             "verification_modes": [ocr_preprocessing], "cross_mode_confirmed": False,
                             "attack_total": damage*hits if hits is not None else None})
    ambiguous = set()
    for index, candidate in enumerate(proposed):
        icon_box = candidate["icon_evidence"]["matches"][0]["box_px"]
        for other in [*rejected, *proposed[:index], *proposed[index+1:]]:
            shares_icon = any(_overlap(icon_box, match["box_px"]) for match in other["icon_evidence"]["matches"])
            if _overlap(candidate["box"], other["box"]) or shares_icon:
                ambiguous.add(index)
                break
    accepted = []
    for index, candidate in enumerate(proposed):
        if index in ambiguous:
            rejected.append({**candidate, "issues": ["overlapping_or_shared_sword_readings"]})
        else:
            accepted.append(candidate)
    accepted.sort(key=lambda entry: (entry["box"][0], entry["box"][1]))
    if hashlib.sha256(_read(path)).hexdigest() != digest:
        raise ValueError("intent_evidence_source_changed")
    return {"schema": "veda.intent-evidence.v1", "image_path": str(path),
            "image_sha256": digest, "parent_image_sha256": digest,
            "source_dimensions": dimensions, "viewport": list(viewport),
            "ocr_preprocessing": ocr_preprocessing,
            "attack_candidates": [], "unconfirmed_attack_candidates": accepted,
            "rejected_numeric_candidates": rejected,
            "supported_attack_subtotal": None, "subtotal_candidate_count": 0, "incoming_damage": None,
            "enemy_count": None, "enemy_roster_complete": None,
            "combat_ready": False, "runtime_ready": False,
            "runtime_authorized": False, "controller_authorized": False,
            "issues": ["Candidate attacks do not establish enemy identity, target order or roster completeness.",
                       "Missing multipliers do not establish one hit; incoming damage remains unknown.",
                       "Single-mode numeric candidates require matching original and white-text OCR before publication.",
                       "Weapon evidence does not classify companion shield or debuff effects."],
            "provenance": {"method": "literal_ocr_plus_original_weapon_mask",
                           "template_manifest_sha256": template_sha,
                           "numeric_candidates_examined": numeric_count, "sampled_pixels": pixels.sampled,
                           "viewport_source": "caller supplied", "subset_is_total_incoming_damage": False},
            "timing_ms": {"total": (time.monotonic()-began)*1000}}


def _same_reading_area(first, second):
    return _overlap(first["box"], second["box"]) or any(
        _overlap(a["box_px"], b["box_px"])
        for a in first["icon_evidence"]["matches"] for b in second["icon_evidence"]["matches"])


def _bounded_evidence(value):
    """Reject excessive or recursive caller data before any retained deepcopy."""
    pending, nodes, characters = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 50_000:
            raise ValueError("intent_merge_evidence_size_or_depth_bound")
        if isinstance(item, dict):
            if len(item) > 256 or any(not isinstance(key, str) for key in item):
                raise ValueError("intent_merge_evidence_size_or_depth_bound")
            pending.extend((child, depth+1) for child in item.values())
            characters += sum(len(key) for key in item)
        elif isinstance(item, (list, tuple)):
            if len(item) > 2000:
                raise ValueError("intent_merge_evidence_size_or_depth_bound")
            pending.extend((child, depth+1) for child in item)
        elif isinstance(item, str):
            characters += len(item)
            if len(item) > 32_000:
                raise ValueError("intent_merge_evidence_size_or_depth_bound")
        elif item is not None and type(item) not in (bool, int, float):
            raise ValueError("intent_merge_invalid_evidence")
        elif type(item) in (int, float) and (not math.isfinite(item) or abs(item) > 10**15):
            raise ValueError("intent_merge_invalid_evidence")
        if characters > 2_000_000:
            raise ValueError("intent_merge_evidence_size_or_depth_bound")


def _validate_result(result):
    _bounded_evidence(result)
    try:
        if (not isinstance(result, dict) or result.get("schema") != "veda.intent-evidence.v1"
                or not isinstance(result.get("image_path"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", result.get("image_sha256", ""))
                or result.get("parent_image_sha256") != result["image_sha256"]
                or result.get("incoming_damage") is not None
                or any(result.get(key) is not False for key in
                       ("combat_ready", "runtime_ready", "runtime_authorized", "controller_authorized"))):
            raise ValueError
        dimensions = result["source_dimensions"]
        if len(dimensions) != 2 or any(type(v) is not int or v <= 0 for v in dimensions):
            raise ValueError
        view = _viewport(result["viewport"], dimensions)
        if result.get("ocr_preprocessing") not in ("original", "white_text", "cross_mode"):
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{64}", result["provenance"]["template_manifest_sha256"]):
            raise ValueError
        if result["ocr_preprocessing"] == "cross_mode":
            sources = result["merge"]["sources"]
            if (not isinstance(sources, list) or not 2 <= len(sources) <= _MAX_MERGE_SOURCES
                    or type(result["merge"]["source_count"]) is not int
                    or result["merge"]["source_count"] != len(sources)
                    or any(not isinstance(source, dict) or "merge" in source
                           or source.get("ocr_preprocessing") not in ("original", "white_text") for source in sources)):
                raise ValueError
            for source in sources:
                _validate_result(source)
                if any(source[key] != result[key] for key in
                       ("image_path", "image_sha256", "parent_image_sha256", "source_dimensions", "viewport")):
                    raise ValueError
                if source["provenance"]["template_manifest_sha256"] != result["provenance"]["template_manifest_sha256"]:
                    raise ValueError
        elif "merge" in result:
            raise ValueError
        for field, maximum in (("attack_candidates", 64), ("unconfirmed_attack_candidates", 64),
                               ("rejected_numeric_candidates", 256)):
            entries = result[field]
            if not isinstance(entries, list) or len(entries) > maximum:
                raise ValueError
            for entry in entries:
                box = entry["box"]
                if (len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                        or box[0] >= box[2] or box[1] >= box[3] or not _inside(box, view)):
                    raise ValueError
                matches = entry["icon_evidence"]["matches"]
                if not isinstance(matches, list) or len(matches) > 256:
                    raise ValueError
                for match in matches:
                    icon_box = match["box_px"]
                    if (len(icon_box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v)
                                                for v in icon_box)
                            or icon_box[0] >= icon_box[2] or icon_box[1] >= icon_box[3]
                            or not _inside(icon_box, view)):
                        raise ValueError
                _validate_observations([entry["proof"]["raw_observation"]], dimensions)
                x, y, w, h = entry["proof"]["raw_observation"]["box_original_pixels_top_left"]
                if box != [x, y, x+w, y+h]:
                    raise ValueError
                mode = entry["proof"]["ocr_preprocessing"]
                if mode not in ("original", "white_text") or (result["ocr_preprocessing"] != "cross_mode"
                                                             and mode != result["ocr_preprocessing"]):
                    raise ValueError
                if field in ("attack_candidates", "unconfirmed_attack_candidates"):
                    damage, hits = entry["damage_per_hit"], entry["hits"]
                    if (type(damage) is not int or not 0 <= damage <= 9999
                            or hits is not None and (type(hits) is not int or not 1 <= hits <= 999)
                            or entry["multiplier_visible"] is not (hits is not None)
                            or entry["attack_total"] != (damage*hits if hits is not None else None)
                            or entry["attack_total"] is not None and type(entry["attack_total"]) is not int
                            or entry["icon_evidence"].get("confirmed") is not True or len(matches) != 1
                            or entry["proof"]["number_pixels"].get("confirmed") is not True
                            or _literal(entry["proof"]["raw_observation"])[0] != (damage, hits)):
                        raise ValueError
                    modes = entry["verification_modes"]
                    if (not isinstance(modes, list) or not modes
                            or len(modes) != len(set(modes)) or set(modes)-{"original", "white_text"}
                            or entry.get("cross_mode_confirmed") is not (field == "attack_candidates")
                            or (field == "attack_candidates" and set(modes) != {"original", "white_text"})
                            or result["ocr_preprocessing"] != "cross_mode" and modes != [result["ocr_preprocessing"]]):
                        raise ValueError
                    if result["ocr_preprocessing"] == "cross_mode":
                        history = entry["pass_evidence"]
                        if not isinstance(history, list) or not 1 <= len(history) <= _MAX_MERGE_SOURCES:
                            raise ValueError
                        proven_modes = set()
                        for proof in history:
                            leaf = proof["candidate"]
                            if "pass_evidence" in leaf:
                                raise ValueError
                            if not any(leaf == original for source in sources
                                       for original in source["unconfirmed_attack_candidates"]):
                                raise ValueError
                            if ((leaf["damage_per_hit"], leaf["hits"]) != (damage, hits)
                                    or not _same_reading_area(entry, leaf)):
                                raise ValueError
                            proven_modes.add(leaf["proof"]["ocr_preprocessing"])
                        if proven_modes != set(modes) or not any(
                                entry["proof"] == proof["candidate"]["proof"] for proof in history):
                            raise ValueError
                    elif "pass_evidence" in entry or entry["cross_mode_confirmed"]:
                        raise ValueError
    except (KeyError, TypeError, ValueError, _ReaderFailure) as exc:
        raise ValueError("intent_merge_invalid_evidence") from exc


def merge_intent_evidence(original, focused):
    """Fuse two independently parsed passes, preserving usable contradictions.

    A nonliteral or green-contaminated original is an abstention. Conflicting
    valid attack values or multipliers, and genuine ambiguous alternatives in
    neutral numeric pixels, veto a later reading of the same physical icon.
    """
    began = time.monotonic()
    _validate_result(original)
    _validate_result(focused)
    for key in ("image_path", "image_sha256", "parent_image_sha256", "source_dimensions", "viewport"):
        if original[key] != focused[key]:
            raise ValueError("intent_merge_source_mismatch")
    if (original["provenance"]["template_manifest_sha256"]
            != focused["provenance"]["template_manifest_sha256"]):
        raise ValueError("intent_merge_template_mismatch")
    sources = []
    for source in (original, focused):
        if source["ocr_preprocessing"] == "cross_mode":
            sources.extend(deepcopy(source["merge"]["sources"]))
        else:
            sources.append(deepcopy(source))
    if len(sources) > _MAX_MERGE_SOURCES:
        raise ValueError("intent_merge_source_count_bound")
    entries = [(name, card) for name, source in (("original", original), ("focused", focused))
               for card in [*source["attack_candidates"], *source["unconfirmed_attack_candidates"]]]
    groups = []
    for entry in entries:
        matching = [i for i, group in enumerate(groups)
                    if any(_same_reading_area(entry[1], existing[1]) for existing in group)]
        group = [entry]
        for index in reversed(matching):
            group.extend(groups.pop(index))
        groups.append(group)
    rejected = [{**deepcopy(card), "ocr_pass": name} for name, source in
                (("original", original), ("focused", focused)) for card in source["rejected_numeric_candidates"]]
    accepted, unconfirmed, conflict_count = [], [], 0
    for group in groups:
        values = {(card["damage_per_hit"], card["hits"]) for _, card in group}
        conflicted = len(values) != 1 or len({name for name, _ in group}) != len(group)
        for other in rejected:
            if not other["proof"]["number_pixels"].get("confirmed"):
                continue  # Coloured curls cannot supply a competing numeral.
            if not any(_same_reading_area(card, other) for _, card in group):
                continue
            _, parsed, literal_issues = _literal(other["proof"]["raw_observation"])
            if (any(value is not None and value not in values for value in parsed)
                    or "alternative_attack_readings_disagree" in literal_issues
                    and any(value is not None for value in parsed)
                    or "overlapping_or_shared_sword_readings" in other.get("issues", [])):
                conflicted = True
        if conflicted:
            conflict_count += 1
            rejected.extend({**deepcopy(card), "ocr_pass": name,
                             "issues": ["conflicting_attack_evidence_across_passes"]} for name, card in group)
            continue
        chosen = next((card for name, card in group if name == "focused"), group[0][1])
        merged = deepcopy(chosen)
        history = []
        for name, card in group:
            if "pass_evidence" in card:
                history.extend(deepcopy(card["pass_evidence"]))
            else:
                history.append({"ocr_pass": name, "candidate": deepcopy(card)})
        if len(history) > _MAX_MERGE_SOURCES:
            raise ValueError("intent_merge_candidate_history_bound")
        merged["pass_evidence"] = history
        modes = {mode for _, card in group for mode in card["verification_modes"]}
        merged["verification_modes"] = sorted(modes)
        merged["cross_mode_confirmed"] = modes == {"original", "white_text"}
        (accepted if merged["cross_mode_confirmed"] else unconfirmed).append(merged)
    accepted.sort(key=lambda card: (card["box"][0], card["box"][1]))
    unconfirmed.sort(key=lambda card: (card["box"][0], card["box"][1]))
    if len(accepted)+len(unconfirmed) > 64 or len(rejected) > 256:
        raise ValueError("intent_merge_evidence_bound")
    totals = [card["attack_total"] for card in accepted if card["attack_total"] is not None]
    result = deepcopy(focused)
    result.update(attack_candidates=accepted, unconfirmed_attack_candidates=unconfirmed,
                  rejected_numeric_candidates=rejected, ocr_preprocessing="cross_mode",
                  supported_attack_subtotal=sum(totals) if totals else None,
                  subtotal_candidate_count=len(totals), incoming_damage=None)
    result["merge"] = {"schema": "veda.intent-pass-merge.v1", "conflict_count": conflict_count,
                       "source_count": len(sources), "sources": sources}
    result["timing_ms"] = {"merge": (time.monotonic()-began)*1000}
    return result
