"""Offline OCR-led card candidates, never a complete hand or runtime reader.

Only saved pixels and OCR observations are consumed. The caller supplies a
verified game viewport. No deck, standard costs, model calls, or input controls
are used. Header/cost regions are estimates for a further OCR pass, not proof
that a card or cost has been read.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
from statistics import median

from .card_catalog import DISPLAY_TITLES, TITLE_SOURCES, canonical_title


# Compatibility alias; these are display-only entries, not supported effects.
REVIEWED_TITLES = DISPLAY_TITLES
_UI_TEXT = {"End Turn", "Drink", "Discard", "Unplayable", "Weak", "Vulnerable",
            "Block", "Attack", "Skill", "Power", "Status"}
_UI_TEXT_LOWER = {text.lower() for text in _UI_TEXT}
_PLAUSIBLE_TITLE = re.compile(r"[A-Za-z][A-Za-z '\-+?]{0,31}\Z")
_BODY_LINE = re.compile(r"^(?:exhaust$|put\s+(?:a|an|the)\s+card\b|discard\s+pile\b|your\s+draw\s+pile\b|"
                        r"(?:deal|gain|draw|apply|lose|shuffle|add)\s+|whenever\s+|until\s+)", re.I)


def _box(values):
    if (not isinstance(values, (list, tuple)) or len(values) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
        raise ValueError("finite four-coordinate box required")
    left, top, right, bottom = values
    if right <= left or bottom <= top:
        raise ValueError("positive box area required")
    return tuple(float(v) for v in values)


def _inside(box, outer):
    return box[0] >= outer[0] and box[1] >= outer[1] and box[2] <= outer[2] and box[3] <= outer[3]


def _pixels(box):
    return [math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])]


def _clip(box, outer):
    return (max(box[0], outer[0]), max(box[1], outer[1]),
            min(box[2], outer[2]), min(box[3], outer[3]))


def _colour_evidence(image, box):
    """Sample the OCR title only; cyan card borders never count as green."""
    region = image.crop(_pixels(box))
    if region.width * region.height > 200_000:
        return None, {"sampling_issue": "title exceeds the bounded pixel sample"}
    white = green = cyan = 0
    samples = region.get_flattened_data() if hasattr(region, "get_flattened_data") else region.getdata()
    for r, g, b in samples:
        if min(r, g, b) >= 185 and max(r, g, b) - min(r, g, b) <= 35:
            white += 1
        # Upgraded titles are yellow/green. A cyan frame has blue approximately
        # equal to green and fails this independent colour condition.
        if g >= 110 and g > 1.12 * r and g > 1.4 * b:
            green += 1
        if g > 1.2 * r and b > .8 * g:
            cyan += 1
    minimum = max(6, region.width * region.height * .02)
    colour = None
    if green >= minimum and green > white * 3 and green < region.width * region.height * .7:
        colour = "green"
    elif white >= minimum and white > green * 3:
        colour = "white"
    return colour, {"white_pixels": white, "green_pixels": green,
                    "excluded_cyan_pixels": cyan, "sample_pixels": region.width * region.height}


def _orb_evidence(image, numeral_box, scale, viewport):
    """Require warm circular-surround pixels around an observed cost digit."""
    cx = (numeral_box[0] + numeral_box[2]) / 2
    cy = (numeral_box[1] + numeral_box[3]) / 2
    radius = scale * .85
    ring = _clip((cx - radius * 1.5, cy - radius * 1.5,
                  cx + radius * 1.5, cy + radius * 1.5), viewport)
    left, top, right, bottom = _pixels(ring)
    if (right - left) * (bottom - top) > 100_000:
        return False, {"sampling_issue": "orb exceeds the bounded pixel sample"}
    warm = total = 0
    pixels = image.load()
    for y in range(top, bottom):
        for x in range(left, right):
            distance = ((x - cx) ** 2 + (y - cy) ** 2) ** .5
            if .55 * radius <= distance <= 1.5 * radius:
                r, g, b = pixels[x, y]
                total += 1
                warm += int(r >= 105 and r > b * 1.25 and (r > g * 1.1 or g > r * .45))
    return total > 0 and warm / total >= .28, {
        "warm_ring_pixels": warm, "ring_pixels": total,
        "method": "observed_numeric_neighbour_and_warm_orb_surround",
    }


def _title_axes(row, fallback_scale):
    """Native text quadrilateral: top-left, top-right, bottom-right, bottom-left."""
    x, y, right, bottom = row["box"]
    quad = row.get("quad")
    if (isinstance(quad, (list, tuple)) and len(quad) == 4
            and all(isinstance(p, (list, tuple)) and len(p) == 2
                    and all(type(v) in (int, float) and math.isfinite(v) for v in p) for p in quad)
            and all(x - 2 <= p[0] <= right + 2 and y - 2 <= p[1] <= bottom + 2 for p in quad)):
        tl, tr, br, bl = quad
        dx, dy = tr[0] - tl[0], tr[1] - tl[1]
        length = math.hypot(dx, dy)
        font_height = (math.dist(tl, bl) + math.dist(tr, br)) / 2
        if length > 0 and dx > 0 and abs(dy / length) < .6 and 0 < font_height <= bottom - y + 2:
            return ((tl[0] + bl[0]) / 2, (tl[1] + bl[1]) / 2), (dx / length, dy / length), font_height, "native_text_quad"
    return (x, (y + bottom) / 2), (1.0, 0.0), min(bottom - y, fallback_scale * 1.3), "axis_box_fallback"


def _gold_orb_component(image, search, font_height, expected_center):
    """Locate a compact warm-gold component, without reading or assuming cost."""
    from PIL import Image, ImageFilter

    left, top, right, bottom = _pixels(search)
    if (right - left) * (bottom - top) > 100_000:
        return None, "cost component pixel bound exceeded"
    region = image.crop((left, top, right, bottom))
    mask = Image.new("L", region.size)
    source, dest = region.load(), mask.load()
    for py in range(region.height):
        for px in range(region.width):
            r, g, b = source[px, py]
            if r >= 95 and g >= 65 and .8 * g <= r <= 2.1 * g and b < .75 * g:
                dest[px, py] = 255
    # Join small antialiasing gaps in a ring; no image text is reconstructed.
    mask = mask.filter(ImageFilter.MaxFilter(3))
    pixels = mask.load()
    seen = set()
    components = []
    for py in range(region.height):
        for px in range(region.width):
            if not pixels[px, py] or (px, py) in seen:
                continue
            queue = [(px, py)]
            seen.add((px, py))
            minx = maxx = px
            miny = maxy = py
            count = 0
            while queue:
                cx, cy = queue.pop()
                count += 1
                minx, maxx = min(minx, cx), max(maxx, cx)
                miny, maxy = min(miny, cy), max(maxy, cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if (0 <= nx < region.width and 0 <= ny < region.height
                            and pixels[nx, ny] and (nx, ny) not in seen):
                        seen.add((nx, ny))
                        queue.append((nx, ny))
            w, h = maxx - minx + 1, maxy - miny + 1
            center = (left + (minx + maxx) / 2, top + (miny + maxy) / 2)
            if (.65 * font_height <= w <= 3.4 * font_height
                    and .8 * font_height <= h <= 3.4 * font_height
                    and .5 <= w / h <= 1.5 and count >= w * h * .15
                    and math.dist(center, expected_center) <= font_height * 1.35
                    and minx > 0 and miny > 0 and maxx < region.width - 1 and maxy < region.height - 1):
                components.append((left + minx, top + miny, left + maxx + 1, top + maxy + 1))
    if len(components) == 1:
        return components[0], "unique_compact_gold_component"
    return None, "no unique compact gold component; retain geometric estimate"


def _header_geometry(image, row, scale, viewport):
    left_center, along, font_height, source = _title_axes(row, scale)
    font_height = max(4.0, min(font_height, (viewport[3] - viewport[1]) * .065))
    ux, uy = along
    normal = (-uy, ux)
    center = (left_center[0] - ux * font_height * 2.4 - normal[0] * font_height * .45,
              left_center[1] - uy * font_height * 2.4 - normal[1] * font_height * .45)
    radius = font_height * 1.85
    broad = _clip((center[0] - radius, center[1] - radius,
                   center[0] + radius, center[1] + radius), viewport)
    component, component_note = _gold_orb_component(image, broad, font_height, center)
    if component:
        margin = font_height * .4
        cost = _clip((component[0] - margin, component[1] - margin,
                      component[2] + margin, component[3] + margin), viewport)
    else:
        cost = broad
    x, y, right, bottom = row["box"]
    # Include room for a plus missed by OCR. Keep letter-colour sampling on the
    # original title observation, not on this padded search area or cyan frame.
    title = _clip((x - font_height * .35, y - font_height * .45,
                   right + font_height * .95, bottom + font_height * .45), viewport)
    header = (min(cost[0], title[0]), min(cost[1], title[1]),
              max(cost[2], title[2]), max(cost[3], title[3]))
    return header, title, cost, font_height, {
        "axis_source": source, "font_height_px": font_height,
        "cost_region_source": component_note,
        "cost_component_box_px": _pixels(component) if component else None,
        "estimates_are_not_recognition": True,
    }


def detect_card_regions(image_path, *, viewport, observations):
    """Return conservative candidates using original-image OCR coordinates.

    ``viewport`` is a caller-verified [left, top, right, bottom] game rectangle.
    Each observation uses native OCR's ``box_original_pixels_top_left`` (xywh)
    and ``candidates`` (top text/confidence first). Optional native quadrilaterals
    are retained as provenance. OCR alternatives never repair the top reading.
    The return value cannot authorize input or certify complete hand membership.
    """
    from PIL import Image

    if not isinstance(observations, list) or len(observations) > 2000:
        raise ValueError("at most 2000 native OCR observations required")
    path = Path(image_path)
    with path.open("rb") as source:
        raw = source.read(64_000_001)
    if len(raw) > 64_000_000:
        raise ValueError("saved image exceeds the offline byte bound")
    with Image.open(BytesIO(raw)) as opened:
        if opened.width * opened.height > 40_000_000:
            raise ValueError("saved image exceeds the offline pixel bound")
        image = opened.convert("RGB")
    image_digest = hashlib.sha256(raw).hexdigest()
    viewport = _box(viewport)
    if not _inside(viewport, (0, 0, image.width, image.height)):
        raise ValueError("verified viewport must be inside the saved image")
    vx, vy, vr, vb = viewport
    width, height = vr - vx, vb - vy
    rows, issues = [], []
    for index, observation in enumerate(observations):
        try:
            x, y, w, h = observation["box_original_pixels_top_left"]
            box = _box([x, y, x + w, y + h])
            candidate = observation["candidates"][0]
            text, confidence = candidate["text"], candidate["confidence"]
            if (not isinstance(text, str) or type(confidence) not in (float, int)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("invalid OCR text/confidence")
        except (KeyError, TypeError, IndexError, ValueError):
            issues.append(f"observation {index}: malformed OCR evidence ignored")
            continue
        if _inside(box, viewport):
            rows.append({"index": index, "text": text, "confidence": confidence,
                         "box": box, "quad": observation.get("quadrilateral_original_pixels_top_left"),
                         "numeric_alternatives": sorted({c["text"] for c in observation["candidates"]
                             if isinstance(c, dict) and isinstance(c.get("text"), str)
                             and re.fullmatch(r"[0-9]", c["text"])})})
    menu = any(row["text"].lower() in {"drink", "discard"} for row in rows)
    titles = []
    rejected_text_regions = []
    sampled_titles = 0
    for row in rows:
        text, box = row["text"], row["box"]
        canonical = canonical_title(text)
        x, y, right, bottom = box
        if text.lower() in _UI_TEXT_LOWER or not _PLAUSIBLE_TITLE.fullmatch(text):
            continue
        if canonical is None and _BODY_LINE.match(text):
            rejected_text_regions.append({"raw_text": text, "box_px": _pixels(box),
                                          "reason": "card-effect sentence fragment, not a title"})
            continue
        if not (vx + .12 * width <= x and right <= vx + .88 * width
                and .008 * height <= bottom - y <= .075 * height
                and right - x <= .3 * width):
            continue
        if not vy + .2 * height <= y <= vy + .925 * height:
            continue
        sampled_titles += 1
        if sampled_titles > 100:
            issues.append("title sampling bound exceeded; remaining OCR text was not evaluated")
            break
        colour, colour_counts = _colour_evidence(image, box)
        in_hand_band = vy + .73 * height <= y <= vy + .925 * height
        # A known title or independently green lettering outside the lower
        # hand band is a popup/nonhand candidate, never another retained card.
        above_hand = vy + .2 * height <= y < vy + .73 * height
        if not in_hand_band and not (above_hand and (canonical is not None or colour == "green")):
            continue
        titles.append({**row, "canonical_title": canonical, "colour": colour, "colour_counts": colour_counts,
                       "location": "hand_band" if in_hand_band else "popup_or_nonhand"})
        if len(titles) > 20:
            break
    if len(titles) > 20:
        issues.append("candidate bound exceeded; extra candidates were not evaluated")
        titles = titles[:20]
    scale = median([row["box"][3] - row["box"][1] for row in titles
                    if row["location"] == "hand_band"] or [height * .022])
    scale = max(height * .01, min(scale, height * .05))
    cards = []
    used_numerals = {}
    for row in titles:
        header, title_search, search, local_scale, geometry = _header_geometry(image, row, scale, viewport)
        field_issues = {"name": [], "upgraded": [], "current_cost": []}
        name = row["canonical_title"] if row["confidence"] >= .8 else None
        if name is None:
            field_issues["name"].append("top OCR text is not a confident display title (case-only matching)")
        literal_plus = row["text"].endswith("+")
        upgraded = None
        if name is not None and row["colour"] is not None:
            if literal_plus == (row["colour"] == "green"):
                upgraded = literal_plus
            else:
                field_issues["upgraded"].append("literal plus and independently sampled letter colour disagree")
                field_issues["name"].append("exact upgrade variant is unresolved because title and letter colour disagree")
                name = None
        else:
            field_issues["upgraded"].append("exact title or independent letter colour is unknown")
            if name is not None:
                field_issues["name"].append("independent letter colour is required to publish an exact upgrade variant")
                name = None
        matches = []
        for number in rows:
            if not re.fullmatch(r"[0-9]", number["text"]) or number["confidence"] < .8:
                continue
            left, top, nr, nb = number["box"]
            center = ((left + nr) / 2, (top + nb) / 2)
            if (search[0] <= center[0] <= search[2] and search[1] <= center[1] <= search[3]
                    and .45 * local_scale <= nb - top <= 1.6 * local_scale
                    and nr - left <= 1.1 * local_scale):
                orb, evidence = _orb_evidence(image, number["box"], local_scale, viewport)
                if orb:
                    matches.append((number, evidence))
                    if len(matches) > 1:
                        break  # Further numerals cannot resolve this ambiguity.
        cost = None
        cost_evidence = None
        if len(matches) == 1:
            number, cost_evidence = matches[0]
            cost_evidence = {**cost_evidence, "observation_index": number["index"],
                             "raw_text": number["text"], "box_px": _pixels(number["box"]),
                             "numeric_alternatives": number["numeric_alternatives"]}
            if len(number["numeric_alternatives"]) > 1:
                field_issues["current_cost"].append("conflicting numeric OCR alternatives for the cost orb")
            else:
                cost = int(number["text"])
                used_numerals.setdefault(number["index"], []).append(len(cards))
        else:
            field_issues["current_cost"].append("no unique observed numeral with cost-orb evidence")
            if len(matches) > 1:
                cost_evidence = {"ambiguous": True, "box_px": None,
                                 "possible_cost_boxes": [_pixels(number["box"]) for number, _ in matches],
                                 "numeric_alternatives": sorted({value for number, _ in matches
                                                                 for value in number["numeric_alternatives"]})}
        if row["location"] != "hand_band":
            field_issues["name"].append("popup/nonhand location: do not append this to the current hand")
        anchor = hashlib.sha256((image_digest + repr(row["box"]) + str(row["index"])).encode()).hexdigest()[:20]
        cards.append({"anchor_id": anchor, "raw_text": row["text"], "name": name, "upgraded": upgraded,
                      "title_color": row["colour"], "current_cost": cost,
                      "location": row["location"], "title_box_px": _pixels(row["box"]),
                      "header_box_px": _pixels(header), "title_search_box_px": _pixels(title_search),
                      "cost_search_box_px": _pixels(search),
                      "field_issues": field_issues,
                      "provenance": {"observation_index": row["index"],
                                     "ocr_confidence": row["confidence"],
                                     "display_title_sources": list(TITLE_SOURCES.get(row["canonical_title"], ())),
                                     "title_case_normalized": row["canonical_title"] is not None and row["text"] != row["canonical_title"],
                                     "ocr_quadrilateral_original_pixels_top_left": row["quad"],
                                     "header_geometry": geometry,
                                     "letter_colour": row["colour_counts"],
                                     "cost": cost_evidence}})
    for positions in used_numerals.values():
        if len(positions) > 1:
            for position in positions:
                cards[position]["current_cost"] = None
                cards[position]["field_issues"]["current_cost"].append("same numeral could belong to multiple titles")
    cards.sort(key=lambda card: (card["title_box_px"][0], card["title_box_px"][1]))
    overlapping_titles = False
    for i, card in enumerate(cards):
        a = card["title_box_px"]
        for other in cards[i + 1:]:
            b = other["title_box_px"]
            overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
            if overlap > .25 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])):
                overlapping_titles = True
                for entry in (card, other):
                    entry["field_issues"]["name"].append("overlapping title observations: unique card membership is unconfirmed")
    popup = any(card["location"] == "popup_or_nonhand" for card in cards)
    if popup:
        issues.append("popup/nonhand card candidate: full hand visibility is unconfirmed")
    if menu:
        issues.append("Drink/Discard menu text present: normal card-input UI is unconfirmed")
    if overlapping_titles:
        issues.append("overlapping title observations cannot establish separate cards")
    issues.append("candidate discovery does not prove card count or absence of hidden cards")
    return {"schema": "veda.card-region-candidates.v1", "image_sha256": image_digest,
            "source_dimensions": [image.width, image.height], "viewport": _pixels(viewport),
            "candidate_count": len(cards), "hand_candidate_count": sum(c["location"] == "hand_band" for c in cards),
            "card_candidates": cards, "hand_complete": False if popup or overlapping_titles else None,
            "popup_or_nonhand_candidate": popup, "menu_text_present": menu, "issues": issues,
            "rejected_text_regions": rejected_text_regions,
            "runtime_authorized": False, "controller_authorized": False,
            "provenance": {"method": "native_ocr_title_geometry_and_independent_pixel_colour",
                           "title_matching": "top OCR candidate, letter case only; no whitespace, punctuation, plus, deck or fuzzy repair",
                           "cost_matching": "observed unique numeral plus warm orb pixels; no standard costs",
                           "viewport_source": "explicit caller-verified game bounds",
                           "effect_support_not_asserted": True}}
