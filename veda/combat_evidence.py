"""Partial combat numerals from an immutable saved image and validated OCR.

Health bars and shield colours corroborate text; they never supply numbers.
Candidate locations do not establish enemy identity, target order, intent,
complete state, or permission to act.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import math
from pathlib import Path
import re

from .native_ocr import _ReaderFailure, _validate_observations


_MAX_BYTES = 64_000_000
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_OBSERVATIONS = 2000
_MAX_NUMERIC_CANDIDATES = 64
_MAX_SAMPLE_PIXELS = 2_000_000
_HEALTH = re.compile(r"([0-9]{1,6})\s*/\s*([0-9]{1,6})\Z")
_INTEGER = re.compile(r"[0-9]{1,6}\Z")


def _read(path):
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("combat_evidence_image_byte_bound")
    return raw


def _viewport(value, dimensions):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not 0 <= value[0] < value[2] <= dimensions[0]
            or not 0 <= value[1] < value[3] <= dimensions[1]):
        raise ValueError("combat_evidence_invalid_viewport")
    return tuple(value)


def _inside(box, region):
    return region[0] <= box[0] and region[1] <= box[1] and box[2] <= region[2] and box[3] <= region[3]


def _red(rgb):
    r, g, b = rgb
    return r >= 95 and r > 1.5 * g and r > 1.35 * b


def _blue(rgb):
    r, g, b = rgb
    return b >= 95 and g >= 90 and b > 1.2 * r and g > 1.1 * r and .55 <= g / b <= 1.6


class _Pixels:
    def __init__(self, image, viewport):
        self.image, self.viewport, self.sampled = image, viewport, 0

    def mask(self, box, predicate):
        v = self.viewport
        # Inward rounding keeps every sampled pixel inside the verified view.
        left, top = math.ceil(max(box[0], v[0])), math.ceil(max(box[1], v[1]))
        right, bottom = math.floor(min(box[2], v[2])), math.floor(min(box[3], v[3]))
        area = max(0, right-left) * max(0, bottom-top)
        if not area or area > 100_000 or self.sampled + area > _MAX_SAMPLE_PIXELS:
            raise ValueError("combat_evidence_pixel_sample_bound")
        self.sampled += area
        crop = self.image.crop((left, top, right, bottom))
        data = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
        return (left, top, right, bottom), bytearray(bool(predicate(pixel)) for pixel in data)


def _components(mask, width, height):
    seen = bytearray(len(mask))
    found = []
    for start, on in enumerate(mask):
        if not on or seen[start]:
            continue
        pending = [start]
        seen[start] = 1
        minx = maxx = start % width
        miny = maxy = start // width
        count = 0
        while pending:
            point = pending.pop()
            x, y = point % width, point // width
            count += 1
            minx, maxx, miny, maxy = min(minx, x), max(maxx, x), min(miny, y), max(maxy, y)
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if 0 <= nx < width and 0 <= ny < height:
                    target = ny * width + nx
                    if mask[target] and not seen[target]:
                        seen[target] = 1
                        pending.append(target)
        if count >= 4:
            found.append((minx, miny, maxx+1, maxy+1, count))
        if len(found) > 256:
            raise ValueError("combat_evidence_colour_component_bound")
    return found


def _bar_proof(pixels, search, text_height, center_y, *, blue_allowed=False, min_width_factor=1.4,
               allow_text_split=False):
    bounds, mask = pixels.mask(search, (lambda rgb: _red(rgb) or _blue(rgb)) if blue_allowed else _red)
    left, top, right, bottom = bounds
    bars, segments = [], []
    for x, y, r, b, count in _components(mask, right-left, bottom-top):
        width, height = r-x, b-y
        if (width >= max(12, text_height * .75)
                and 2 <= height <= text_height * .8 and width / height >= 3
                and count >= width * height * .45
                and abs(top + (y+b)/2-center_y) <= text_height * .6):
            segment = {"box_px": [left+x, top+y, left+r, top+b], "colour_pixels": count}
            segments.append(segment)
            if width >= max(12, text_height * min_width_factor):
                bars.append(segment)
    if allow_text_split and not bars:
        # A numeral over a health row can split its fill. Combine only two
        # substantial thin pieces on the same row, across a bounded text gap;
        # the surrounding shield remains an independent prerequisite.
        for index, first in enumerate(segments):
            for second in segments[index+1:]:
                a, b = sorted((first["box_px"], second["box_px"]))
                if (0 <= b[0]-a[2] <= 4*text_height
                        and abs((a[1]+a[3]-b[1]-b[3])/2) <= .2*text_height
                        and min(a[3], b[3])-max(a[1], b[1]) >= .5*min(a[3]-a[1], b[3]-b[1])
                        and a[2]-a[0]+b[2]-b[0] >= max(12, text_height*min_width_factor)
                        and b[2]-a[0] <= 10*text_height):
                    bars.append({"box_px": [a[0], min(a[1], b[1]), b[2], max(a[3], b[3])],
                                 "colour_pixels": first["colour_pixels"]+second["colour_pixels"],
                                 "text_split_segments": [first, second]})
    return {"confirmed": bool(bars), "sample_box_px": list(bounds), "bar_components": bars,
            "colour_test": "red_or_blue_horizontal_bar" if blue_allowed else "red_horizontal_bar"}


def _shield_proof(pixels, box):
    left, top, right, bottom = box
    height = bottom-top
    cx, cy = (left+right)/2, (top+bottom)/2
    radius = max(height, (right-left)/2)
    bounds, mask = pixels.mask((cx-radius*1.3, cy-radius*1.3, cx+radius*1.3, cy+radius*1.3), _blue)
    a, b, c, d = bounds
    total, blue = [0]*4, [0]*4
    for y in range(d-b):
        for x in range(c-a):
            dx, dy = (a+x+.5-cx)/radius, (b+y+.5-cy)/radius
            if .3 <= dx*dx+dy*dy <= 1.4:
                quadrant = (2 if dy >= 0 else 0) + (1 if dx >= 0 else 0)
                total[quadrant] += 1
                blue[quadrant] += mask[y*(c-a)+x]
    fractions = [count/area if area else 0 for count, area in zip(blue, total)]
    confirmed = all(value >= .035 for value in fractions) and sum(blue)/max(1, sum(total)) >= .14
    return {"confirmed": confirmed, "sample_box_px": list(bounds), "blue_pixels": sum(blue),
            "ring_pixels": sum(total), "quadrant_blue_fractions": fractions,
            "center_px": [cx, cy], "radius_px": radius,
            "method": "blue_cyan_surround_on_all_four_sides_of_literal_numeral"}


def _literal(row, *, health):
    values, issues = [], []
    for candidate in row["candidates"]:
        text = candidate["text"].strip()
        match = _HEALTH.fullmatch(text) if health else _INTEGER.fullmatch(text)
        value = (tuple(map(int, match.groups())) if health else int(text)) if match else None
        values.append(value)
    if values[0] is None:
        issues.append("top_text_is_not_a_complete_literal")
    if row["candidates"][0]["confidence"] < .8:
        issues.append("top_confidence_below_0.8")
    if any(value is None or value != values[0] for value in values[1:]):
        issues.append("alternative_reading_disagrees_or_is_not_literal")
    if health and values[0] is not None and (values[0][1] == 0 or values[0][0] > values[0][1]):
        issues.append("invalid_health_fraction")
    return (values[0] if not issues else None), issues


def _overlap(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return area > .25 * smaller


def extract_combat_evidence(image_path, *, viewport, observations):
    """Return corroborated candidate numerals, never a complete combat state.

    All coordinates are original saved-image pixels. Outside-viewport desktop
    OCR is ignored; malformed/image-outside evidence raises ValueError. The
    caller handles an unavailable optional Pillow dependency. No input is sent.
    """
    from PIL import Image

    if not isinstance(observations, list) or len(observations) > _MAX_OBSERVATIONS:
        raise ValueError("combat_evidence_observation_bound")
    path = Path(image_path).expanduser().resolve()
    raw = _read(path)
    digest = hashlib.sha256(raw).hexdigest()
    with Image.open(BytesIO(raw)) as opened:
        if opened.width * opened.height > _MAX_IMAGE_PIXELS:
            raise ValueError("combat_evidence_image_pixel_bound")
        image = opened.convert("RGB")
    dimensions = [image.width, image.height]
    view = _viewport(viewport, dimensions)
    try:
        _validate_observations(observations, dimensions)
    except _ReaderFailure as exc:
        raise ValueError("combat_evidence_invalid_observation:" + str(exc)) from exc
    vx, vy, vr, vb = view
    width, height = vr-vx, vb-vy
    enemy_region = (vx+.4*width, vy+.38*height, vx+.94*width, vy+.79*height)
    block_region = (vx+.13*width, vy+.6*height, vx+.28*width, vy+.79*height)
    pixels = _Pixels(image, view)
    enemies, blocks, rejected = [], [], []
    numerical_count = 0
    for index, row in enumerate(observations):
        x, y, w, h = row["box_original_pixels_top_left"]
        box = (x, y, x+w, y+h)
        if not _inside(box, view) or not .008*height <= h <= .04*height:
            continue
        enemy = _inside(box, enemy_region) and any("/" in c["text"] for c in row["candidates"])
        block = _inside(box, block_region) and any(_INTEGER.fullmatch(c["text"].strip()) for c in row["candidates"])
        if not enemy and not block:
            continue
        numerical_count += 1
        if numerical_count > _MAX_NUMERIC_CANDIDATES:
            raise ValueError("combat_evidence_numeric_candidate_bound")
        value, issues = _literal(row, health=enemy)
        proof = {"observation_index": index, "raw_observation": deepcopy(row),
                 "literal_rule": "top_confidence_at_least_0.8_and_all_alternatives_agree",
                 "location_region_px": list(enemy_region if enemy else block_region)}
        cy = y+h/2
        if value is not None and enemy:
            bar = _bar_proof(pixels, (x-.055*width, cy-h, x+w+.045*width, cy+h), h, cy)
            proof["health_bar"] = bar
            if not bar["confirmed"]:
                issues.append("no_independent_horizontal_red_health_bar")
        elif value is not None:
            shield = _shield_proof(pixels, box)
            proof["shield"] = shield
            if not shield["confirmed"]:
                issues.append("no_independent_blue_shield_surround")
            else:
                # Start beside the corroborated shield, not beside the text's
                # right edge: a second digit otherwise clips the same visible
                # health row more heavily. All bar shape/width checks remain.
                bar_left = shield["center_px"][0] + shield["radius_px"]
                bar = _bar_proof(pixels, (bar_left, cy-h, x+w+.15*width, cy+h), h, cy,
                                 blue_allowed=True, min_width_factor=3, allow_text_split=True)
                proof["adjacent_health_row"] = bar
                if not bar["confirmed"]:
                    issues.append("no_adjacent_horizontal_player_health_row")
        candidate = {"box": list(box), "proof": proof, "issues": issues}
        if issues:
            rejected.append({"kind": "enemy_health" if enemy else "player_block", **candidate})
        elif enemy:
            enemies.append({"hp": value[0], "max_hp": value[1], **candidate})
        else:
            blocks.append({"value": value, **candidate})
    # Two OCR depictions of one bar cannot establish two enemies or settle a
    # contradictory health reading. Keep the rejected evidence for inspection.
    ambiguous = set()
    for index, candidate in enumerate(enemies):
        if any(other["kind"] == "enemy_health" and _overlap(candidate["box"], other["box"])
               for other in rejected):
            ambiguous.add(index)
        for other_index in range(index+1, len(enemies)):
            if _overlap(candidate["box"], enemies[other_index]["box"]):
                ambiguous.update((index, other_index))
    for index in sorted(ambiguous):
        rejected.append({"kind": "enemy_health", **enemies[index], "issues": ["overlapping_health_observations"]})
    enemies = [candidate for index, candidate in enumerate(enemies) if index not in ambiguous]
    enemies.sort(key=lambda candidate: (candidate["box"][0], candidate["box"][1]))
    block_conflict = any(other["kind"] == "player_block" and _overlap(card["box"], other["box"])
                         for card in blocks for other in rejected)
    player_block = blocks[0]["value"] if len(blocks) == 1 and not block_conflict else None
    issues = ["Candidate locations do not establish enemy identity, target order or complete combat state.",
              "Intent icons and hit counts are not independently recognized; incoming damage remains unknown."]
    if len(blocks) > 1 or block_conflict:
        issues.append("Multiple shield-backed block observations are ambiguous.")
    elif not blocks:
        issues.append("No unique shield-backed block numeral; absence does not imply zero.")
    if hashlib.sha256(_read(path)).hexdigest() != digest:
        raise ValueError("combat_evidence_source_changed")
    return {"schema": "veda.combat-evidence.v1", "image_path": str(path), "image_sha256": digest,
            "source_dimensions": dimensions, "viewport": list(viewport),
            "player_block": player_block, "player_block_candidates": blocks,
            "enemy_hp_candidates": enemies, "enemy_count": None, "intent_number_cues": [],
            "incoming_damage": None, "hand_complete": None, "hand_ready": False,
            "combat_ready": False, "runtime_ready": False,
            "runtime_authorized": False, "controller_authorized": False,
            "rejected_numeric_candidates": rejected, "issues": issues,
            "provenance": {"method": "literal_ocr_plus_independent_saved_pixel_context",
                           "sampled_pixels": pixels.sampled, "numeric_candidates_examined": numerical_count,
                           "viewport_source": "caller supplied", "pixel_thresholds_are_not_icon_identity": True}}
