"""Bounded second-pass OCR of automatically selected saved-image card headers.

This produces candidate depictions, never a complete or actionable hand.
No deck lookup, standard-cost substitution, capture, or control is performed.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time

from .card_regions import detect_card_regions
from .card_costs import read_card_cost
from .card_discovery import hand_discovery_request, discover_titles


def _matches_anchor(candidate, anchor):
    a, b = candidate["title_box_px"], anchor["title_box_px"]
    search = anchor.get("title_search_box_px", b)
    cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    small_area = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return search[0] <= cx <= search[2] and search[1] <= cy <= search[3] and overlap >= .25 * small_area


def _merge_candidate(original, refined, evidence):
    result = deepcopy(refined)
    result["anchor_id"] = original.get("anchor_id")
    result["refinement"] = evidence
    result["refinement"]["original_candidate"] = deepcopy(original)
    # An exact identity that was independently confirmed remains useful when
    # the focused pass is unreadable, but two asserted identities cannot agree
    # merely because one pass has a higher OCR confidence.
    old_name, new_name = original.get("name"), refined.get("name")
    if evidence.get("identity_conflict") or (old_name is not None and new_name is not None and old_name != new_name):
        result["name"] = result["upgraded"] = None
        for field in ("name", "upgraded"):
            result["field_issues"][field].append("conflicting asserted identities across OCR passes")
    elif new_name is None and old_name is not None and original.get("upgraded") is not None:
        result["name"], result["upgraded"] = old_name, original["upgraded"]
        result["title_color"] = original.get("title_color")
        result["field_issues"]["name"] = ["identity retained from original independently checked title"]
        result["field_issues"]["upgraded"] = []
    old_cost, new_cost = original.get("current_cost"), refined.get("current_cost")
    result["refinement"]["observed_cost_values"] = sorted(
        {value for value in (old_cost, new_cost) if value is not None})
    result["refinement"]["cost_ambiguous"] = any(_ambiguous_cost(card) for card in (original, refined))
    if result["refinement"]["cost_ambiguous"] or (old_cost is not None and new_cost is not None and old_cost != new_cost):
        result["current_cost"] = None
        result["field_issues"]["current_cost"].append("conflicting observed costs across OCR passes")
    # The focused pass must supply its own cost proof. A missing or ambiguous
    # numeral never inherits a standard cost or a neighbour's numeral.
    return result


def _cost_boxes(card):
    proof = card.get("provenance", {}).get("cost") or {}
    return ([proof["box_px"]] if proof.get("box_px") else []) + proof.get("possible_cost_boxes", [])


def _ambiguous_cost(card):
    proof = card.get("provenance", {}).get("cost") or {}
    return proof.get("ambiguous", False) or len(proof.get("numeric_alternatives", [])) > 1


def _merge_cost(original, card, reading):
    """Missing text may abstain; contradictory observed values always veto."""
    asserted = {value for value in (original.get("current_cost"), card.get("current_cost"),
                                    reading["current_cost"]) if value is not None}
    asserted.update(card.get("refinement", {}).get("observed_cost_values", []))
    ambiguous = (reading["ambiguous"] or card.get("refinement", {}).get("cost_ambiguous", False)
                 or any(_ambiguous_cost(item) for item in (original, card)))
    if len(asserted) > 1 or ambiguous:
        card["current_cost"] = None
        card["field_issues"]["current_cost"].append("conflicting or ambiguous cost-symbol evidence across OCR passes")
    elif reading["current_cost"] is not None:
        card["current_cost"] = reading["current_cost"]
        card["field_issues"]["current_cost"] = []
        card.setdefault("provenance", {})["cost"] = deepcopy(reading["evidence"])
    card["cost_refinement"] = reading


def refine_card_regions(image_path: Path, *, viewport, cards, reader, frame_id,
                        refine_cost_symbols=True, discover_hand_titles=False):
    began = time.monotonic()
    result = deepcopy(cards)
    candidates = cards["card_candidates"]
    if not candidates and not discover_hand_titles:
        result["refinement"] = {"status": "no_candidates", "region_count": 0, "timing_ms": 0}
        return result
    if len(candidates) > 20:
        raise ValueError("refinement_candidate_limit")
    # Discovery must not depend on the initial OCR finding a title, or on
    # headers/costs leaving unused capacity. It shares this single invocation.
    discovery_request = hand_discovery_request(viewport) if discover_hand_titles else None
    refinement_capacity = 20 - int(discovery_request is not None)
    header_requests = {index: {"id": f"header-{index}", "box": candidate["header_box_px"]}
                       for index, candidate in enumerate(candidates[:refinement_capacity])}
    requests = list(header_requests.values())
    omitted_headers = list(range(len(header_requests), len(candidates)))
    title_requests = {}
    omitted_titles = []
    for index, candidate in enumerate(candidates):
        if (candidate.get("name") is None
                and candidate.get("title_color") == "green" and candidate.get("title_search_box_px")):
            if len(requests) >= refinement_capacity:
                omitted_titles.append(index)
                continue
            request = {"id": f"title-{index}", "box": candidate["title_search_box_px"],
                       "preprocessing": "green_text"}
            requests.append(request)
            title_requests[index] = request
    cost_requests = {}
    omitted_costs = []
    if refine_cost_symbols:
        for index, candidate in enumerate(candidates):
            if candidate.get("cost_search_box_px") is None:
                continue
            if len(requests) >= refinement_capacity:
                omitted_costs.append(index)
                continue
            request = {"id": f"cost-{index}", "box": candidate["cost_search_box_px"]}
            requests.append(request)
            cost_requests[index] = request
    if discovery_request:
        requests.append(discovery_request)
    response = reader.observe_regions(image_path, frame_id=frame_id, regions=requests)
    if not isinstance(response, dict):
        raise ValueError("invalid_header_reader_response")
    if (response.get("image_sha256") != cards["image_sha256"]
            or response.get("source_dimensions") != cards["source_dimensions"]
            or response.get("image_path") != str(Path(image_path).resolve())
            or response.get("frame_id") != frame_id):
        raise ValueError("header_reader_source_mismatch")
    result["refinement"] = {"schema": "veda.card-header-refinement.v1", "status": "processed",
                            "region_count": len(requests), "reader_timing_ms": response.get("timing_ms", {}),
                            "header_region_count": len(header_requests),
                            "header_region_budget_omissions": omitted_headers,
                            "title_region_count": len(title_requests),
                            "title_region_budget_omissions": omitted_titles,
                            "cost_region_count": len(cost_requests),
                            "cost_region_budget_omissions": omitted_costs,
                            "hand_discovery": "requested" if discovery_request else "disabled",
                            "hand_discovery_reserved_regions": int(discovery_request is not None),
                            "runtime_authorized": False}
    if omitted_headers:
        result["issues"].append("header regions omitted at the 20-region batch limit; original candidates retained")
    if omitted_titles:
        result["issues"].append("contrast-title regions omitted at the 20-region batch limit")
    if omitted_costs:
        result["issues"].append("cost-symbol regions omitted at the 20-region batch limit")
    possible_boxes = {index: _cost_boxes(card) for index, card in enumerate(candidates)}
    if response.get("ok") is not True:
        # A source/protocol rejection is not equivalent to an unavailable OCR
        # process. Propagate it so the coordinator clears every derived field.
        if response.get("error") not in {"helper_start_failed", "helper_timeout", "helper_failed",
                                          "helper_output_limit", "helper_stderr_limit",
                                          "helper_not_prebuilt_or_executable"}:
            raise ValueError("focused_reader_integrity_failure:" + str(response.get("error", "unknown")))
        result["refinement"]["status"] = "reader_failed"
        result["refinement"]["error"] = response.get("error")
        result["issues"].append("focused header OCR failed; original partial evidence retained")
    else:
        regions = response.get("regions")
        if not isinstance(regions, list) or len(regions) != len(requests):
            raise ValueError("header_region_count_mismatch")
        indexed = {region.get("id"): region for region in regions if isinstance(region, dict)}
        if set(indexed) != {request["id"] for request in requests}:
            raise ValueError("header_region_identity_mismatch")
        for index, original in enumerate(candidates):
            request = header_requests.get(index)
            if request is None:
                result["card_candidates"][index]["refinement"] = {
                    "status": "budget_omitted", "region_id": f"header-{index}",
                    "requested_box_px": original["header_box_px"],
                    "parent_image_sha256": cards["image_sha256"],
                    "error": "header_region_budget_exhausted"}
                continue
            original, region = candidates[index], indexed[request["id"]]
            if region.get("box_original_pixels_ltrb") != request["box"]:
                raise ValueError("header_region_bounds_mismatch")
            evidence = {"region_id": request["id"], "requested_box_px": request["box"],
                        "parent_image_sha256": cards["image_sha256"], "status": "unresolved"}
            if region.get("ok") is not True:
                evidence["error"] = region.get("error", "region_failed")
                result["card_candidates"][index]["refinement"] = evidence
                continue
            observations = region.get("observations", [])
            evidence["observations"] = observations
            found = detect_card_regions(image_path, viewport=viewport, observations=observations)
            if (found.get("image_sha256") != cards["image_sha256"]
                    or found.get("source_dimensions") != cards["source_dimensions"]):
                raise ValueError("refined_card_source_mismatch")
            matches = [card for card in found["card_candidates"] if _matches_anchor(card, original)]
            if len(matches) == 1:
                selected = matches[0]
                title_request = title_requests.get(index)
                if title_request is not None:
                    title_region = indexed[title_request["id"]]
                    if (title_region.get("box_original_pixels_ltrb") != title_request["box"]
                            or title_region.get("preprocessing") != "green_text"):
                        raise ValueError("focused_title_region_mismatch")
                    evidence["title_preprocessing"] = "green_text"
                    evidence["title_region_id"] = title_request["id"]
                    evidence["title_observations"] = title_region.get("observations", [])
                    if title_region.get("ok") is True:
                        title_found = detect_card_regions(image_path, viewport=viewport,
                                                          observations=title_region.get("observations", []))
                        if (title_found.get("image_sha256") != cards["image_sha256"]
                                or title_found.get("source_dimensions") != cards["source_dimensions"]):
                            raise ValueError("focused_title_source_mismatch")
                        title_matches = [c for c in title_found["card_candidates"] if _matches_anchor(c, original)]
                        if len(title_matches) > 1:
                            evidence["identity_conflict"] = True
                            selected["name"] = selected["upgraded"] = None
                            for field in ("name", "upgraded"):
                                selected["field_issues"][field].append("multiple contrast titles match this anchor")
                        elif len(title_matches) == 1 and title_matches[0].get("name") is not None:
                            title = title_matches[0]
                            if selected.get("name") is not None and selected["name"] != title["name"]:
                                evidence["identity_conflict"] = True
                                selected["name"] = selected["upgraded"] = None
                                selected["field_issues"]["name"].append("conflicting original and contrast title readings")
                            elif title.get("upgraded") is True:
                                # Only literal '+' OCR with original-image green-letter
                                # evidence can confirm a title from the contrast pass.
                                for field in ("raw_text", "name", "upgraded", "title_color"):
                                    selected[field] = title[field]
                                for field in ("name", "upgraded"):
                                    selected["field_issues"][field] = title["field_issues"][field]
                                evidence["title_confirmation"] = "literal_plus_and_original_pixel_colour"
                evidence["status"] = "anchor_matched"
                result["card_candidates"][index] = _merge_candidate(original, selected, evidence)
            elif len(matches) > 1:
                entry = result["card_candidates"][index]
                for field in ("name", "upgraded", "current_cost"):
                    entry[field] = None
                    entry["field_issues"][field].append("multiple focused title observations match this anchor")
                evidence["status"] = "ambiguous_anchor"
                entry["refinement"] = evidence
            else:
                result["card_candidates"][index]["refinement"] = evidence
        # Cost crops use the original automatic geometry and run independently
        # of title recognition, including when a header crop finds no title.
        for index, card in enumerate(result["card_candidates"]):
            possible_boxes[index].extend(_cost_boxes(card))
            request = cost_requests.get(index)
            if request is None:
                continue
            reading = read_card_cost(image_path, viewport=viewport, candidate=candidates[index],
                                     observations=indexed[request["id"]])
            if (reading.get("image_sha256") != cards["image_sha256"]
                    or reading.get("source_dimensions") != cards["source_dimensions"]):
                raise ValueError("cost_reader_source_mismatch")
            possible_boxes[index].extend(reading["evidence"]["possible_cost_boxes"])
            _merge_cost(candidates[index], card, reading)
        if discovery_request:
            additional = discover_titles(image_path, viewport=viewport, cards=cards,
                existing=result["card_candidates"], region=indexed[discovery_request["id"]])
            result["refinement"]["hand_discovery_evidence"] = additional
            result["issues"].extend("hand discovery: " + issue for issue in additional.get("detector_issues", []))
            if additional.get("status") == "candidate_limit":
                result["issues"].append("discovered title candidates omitted at the 20-candidate limit")
            if additional.get("hand_complete") is False:
                result["hand_complete"] = False
            for flag in ("popup_or_nonhand_candidate", "menu_text_present"):
                if additional.get(flag):
                    result[flag] = True
            for card in additional["card_candidates"]:
                possible_boxes[len(result["card_candidates"])] = _cost_boxes(card)
                result["card_candidates"].append(card)
    result["refinement"]["timing_ms"] = (time.monotonic() - began) * 1000
    # Independently cropped headers may overlap. One observed numeral cannot
    # provide separate cost evidence for two candidate depictions.
    for index, card in enumerate(result["card_candidates"]):
        for other_index in range(index + 1, len(result["card_candidates"])):
            shared = False
            for a in possible_boxes[index]:
                for b in possible_boxes[other_index]:
                    overlap = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
                    small_area = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
                    shared = shared or overlap > .5 * small_area
            if shared:
                for entry in (card, result["card_candidates"][other_index]):
                    entry["current_cost"] = None
                    entry["field_issues"]["current_cost"].append("overlapping cost evidence across focused headers")
    # Neither a scheduled discovery crop nor an empty result proves a full hand.
    result["hand_complete"] = False if result.get("hand_complete") is False else None
    result["card_candidates"].sort(key=lambda card: (card["title_box_px"][0], card["title_box_px"][1]))
    result["candidate_count"] = len(result["card_candidates"])
    result["hand_candidate_count"] = sum(card.get("location") == "hand_band" for card in result["card_candidates"])
    result["issues"].append("focused OCR does not establish unique hand membership or completeness")
    return result
