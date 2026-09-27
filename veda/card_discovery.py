"""Bounded additional title discovery from an immutable archived hand region."""
from copy import deepcopy

from .card_regions import detect_card_regions


def hand_discovery_request(viewport):
    left, top, right, bottom = viewport
    width, height = right - left, bottom - top
    return {"id": "hand-discovery", "box": [int(left + .12 * width), int(top + .68 * height),
                                              int(left + .88 * width), int(top + .97 * height)]}


def _overlaps(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return area > .25 * smaller


def discover_titles(image_path, *, viewport, cards, existing, region):
    """Add only exact, color-confirmed, spatially distinct title depictions.

    This never fills an existing unknown title by proximity or copies an
    identity from an enlarged popup into a covered hand card.
    """
    request = hand_discovery_request(viewport)
    if (region.get("id") != request["id"] or region.get("box_original_pixels_ltrb") != request["box"]
            or region.get("preprocessing") != "original"):
        raise ValueError("hand_discovery_region_mismatch")
    if (region.get("image_sha256") != cards["image_sha256"]
            or region.get("parent_image_sha256") != cards["image_sha256"]
            or region.get("source_dimensions") != cards["source_dimensions"]):
        raise ValueError("hand_discovery_source_mismatch")
    result = {"schema": "veda.hand-title-discovery.v1", "status": "processed",
              "requested_box_px": request["box"], "image_sha256": cards["image_sha256"],
              "card_candidates": [], "withheld_candidates": [], "withheld_candidate_count": 0,
              "runtime_authorized": False, "hand_complete": None}
    if region.get("ok") is not True:
        result.update(status="region_failed", error=region.get("error"))
        return result
    found = detect_card_regions(image_path, viewport=viewport, observations=region["observations"])
    if (found.get("image_sha256") != cards["image_sha256"]
            or found.get("source_dimensions") != cards["source_dimensions"]):
        raise ValueError("hand_discovery_pixel_source_mismatch")
    result["observations"] = region["observations"]
    result["observed_candidate_count"] = len(found["card_candidates"])
    result["detector_issues"] = deepcopy(found.get("issues", []))
    result["rejected_text_regions"] = deepcopy(found.get("rejected_text_regions", []))
    result["detector_limits_reached"] = [issue for issue in result["detector_issues"]
                                        if "bound exceeded" in issue]
    # The detector does not count all rows beyond its bounds. Do not invent an
    # exact omission count when it stopped evaluating the remaining evidence.
    result["unexamined_candidate_count"] = None if result["detector_limits_reached"] else 0
    result["hand_complete"] = False if found.get("hand_complete") is False else None
    for flag in ("popup_or_nonhand_candidate", "menu_text_present"):
        result[flag] = found.get(flag, False)
    anchors = [*existing, *cards.get("card_candidates", [])]
    for card in found["card_candidates"]:
        reason = None
        if card.get("location") != "hand_band":
            reason = "popup_or_nonhand_location"
        elif card.get("name") is None or type(card.get("upgraded")) is not bool:
            reason = "exact_title_and_original_colour_unconfirmed"
        # Any competing spatial anchor, including unresolved old titles, is
        # enough to withhold a new depiction. Repeated names elsewhere survive.
        elif any(_overlaps(card["title_box_px"], old["title_box_px"]) for old in anchors):
            reason = "overlaps_existing_anchor"
        elif any(_overlaps(card["title_box_px"], other["title_box_px"])
                 for other in found["card_candidates"] if other is not card):
            reason = "overlaps_discovery_candidate"
        elif len(existing) + len(result["card_candidates"]) >= 20:
            reason = "candidate_limit"
            result["status"] = "candidate_limit"
        if reason:
            result["withheld_candidates"].append({
                "anchor_id": card.get("anchor_id"), "raw_text": card.get("raw_text"),
                "title_box_px": deepcopy(card["title_box_px"]), "reason": reason})
            continue
        added = deepcopy(card)
        added["discovery"] = {"region_id": request["id"], "method": "exact_title_and_original_pixel_colour"}
        result["card_candidates"].append(added)
    result["withheld_candidate_count"] = len(result["withheld_candidates"])
    return result
