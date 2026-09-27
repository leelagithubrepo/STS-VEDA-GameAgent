"""Merge inspected map views and compare routes without input authority.

This is an evidence ledger and topology helper, not a pixel recognizer or combat
simulator. The reviewer supplies stable node identities, every visible edge,
inventory completeness, and strategic priorities. A cropped upper map or unknown
boss never invalidates an otherwise confirmed immediate choice. Archived images
can support static topology; a new live action still needs its own fresh review.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from .map_brief import MAX_EDGES, MAX_NODES, _confidence, _identifier, _parse_graph, summarize_routes
from .map_reader import NODE_KINDS, MAP_CHOICE_CONFIDENCE
from .play_requests import reviewed_capture_source
from .saved_frame_reader import _identity

SURVEY_SCHEMA = "veda.map-survey.v1"
PLAN_SCHEMA = "veda.map-plan-review.v1"
MAX_VIEWS = 32
MAX_BYTES = 512_000
CRITERIA = {"avoid_forced_elite", "elite_free_rest", "earliest_rest", "merchant_access", "fewest_elites"}
MAX_CONTINUATIONS = 256
# Map bosses, not every enemy that can occur in an act (Mind Bloom is an
# important distinction). Names match data/spire_reference/enemies-ascension.json;
# Donu and Deca are the joint map encounter, represented as two catalog enemies.
BOSSES_BY_ACT = {1: {"Hexaghost", "Slime Boss", "The Guardian"},
                 2: {"The Champ", "The Collector", "Bronze Automaton"},
                 3: {"Awakened One", "Time Eater", "Donu and Deca"}, 4: {"Corrupt Heart"}}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, label, limit=2048):
    _require(isinstance(value, str) and value.strip() and len(value) <= limit and "\0" not in value,
             f"{label} must be nonempty bounded text")
    return value


def _keys(value, required, optional=()):
    _require(isinstance(value, dict) and set(required) <= set(value) and not set(value) - set(required) - set(optional),
             f"expected fields {', '.join(sorted(required))}")


def _time(value):
    _text(value, "captured_at", 64)
    result = datetime.fromisoformat(value)
    _require(result.tzinfo is not None and result.utcoffset() is not None, "captured_at requires a timezone")
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    _require(len(raw) <= MAX_BYTES, "map document exceeds byte bound")
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def _source(value):
    _keys(value, {"path", "sha256", "captured_at"})
    _text(value["path"], "source path", 4096)
    path = Path(value["path"]).expanduser().resolve()
    _require(_identity(path)[0] == value["sha256"], "map source hash does not match original image")
    _time(value["captured_at"])
    return {**value, "path": str(path)}


def survey_digest(survey):
    return hashlib.sha256(json.dumps(survey, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def merge_survey(draft):
    """Validate and merge reviewed overlapping views; contradictions are errors.

    A complete row declares *all* nodes in that row, across the map width. An
    outgoing-complete node declares all its edges, even when other rows are
    cropped. Missing facts in partial views never erase earlier observations.
    Coordinates identify logical row/lane positions, not pixel/control positions.
    """
    return _merge_survey(draft, verify_sources=True, require_current=True)


def _merge_survey(draft, *, verify_sources, require_current):
    _keys(draft, {"schema", "run_id", "act", "current_node_id", "views"})
    _require(draft["schema"] == SURVEY_SCHEMA, "veda.map-survey.v1 required")
    _identifier(draft["run_id"], "run_id")
    _identifier(draft["current_node_id"], "current_node_id")
    _require(type(draft["act"]) is int and 1 <= draft["act"] <= 4, "act must be 1..4")
    _require(isinstance(draft["views"], list) and 1 <= len(draft["views"]) <= MAX_VIEWS, "1..32 reviewed views required")
    nodes, positions, edges, sources, complete_rows, complete_exits = {}, {}, {}, [], {}, {}
    top = bottom = False
    expected_boss = None
    for view in draft["views"]:
        _keys(view, {"view_id", "source", "review", "coverage", "nodes", "edges"}, {"expected_boss"})
        view_id = _identifier(view["view_id"], "view_id")
        _require(view_id not in {item["view_id"] for item in sources}, "view IDs must be unique")
        _keys(view["review"], {"complete", "reviewer", "evidence_note"})
        _require(view["review"]["complete"] is True, "explicit inspected-view review required")
        _text(view["review"]["reviewer"], "reviewer", 128)
        _text(view["review"]["evidence_note"], "view evidence_note")
        source = _source(view["source"]) if verify_sources else deepcopy(view["source"])
        sources.append({"view_id": view_id, "source": source, "review": deepcopy(view["review"]),
                        "coverage": deepcopy(view["coverage"]),
                        "node_ids": [node.get("node_id") for node in view["nodes"]] if isinstance(view["nodes"], list) else []})
        coverage = view["coverage"]
        _keys(coverage, {"top_visible", "bottom_visible", "complete_rows"})
        _require(type(coverage["top_visible"]) is bool and type(coverage["bottom_visible"]) is bool,
                 "top/bottom coverage must be explicit booleans")
        _require(isinstance(coverage["complete_rows"], list) and len(coverage["complete_rows"]) <= 64
                 and all(type(row) is int and 0 <= row <= 64 for row in coverage["complete_rows"])
                 and len(set(coverage["complete_rows"])) == len(coverage["complete_rows"]), "invalid complete_rows")
        top |= coverage["top_visible"]
        bottom |= coverage["bottom_visible"]
        _require(isinstance(view["nodes"], list) and 1 <= len(view["nodes"]) <= MAX_NODES, "view needs bounded visible nodes")
        local = {}
        for node in view["nodes"]:
            _keys(node, {"node_id", "row", "lane", "kind", "confidence", "outgoing_complete", "classification_evidence"})
            node_id = _identifier(node["node_id"], "node_id")
            _require(node_id not in local, "duplicate node in view")
            _require(type(node["row"]) is int and 0 <= node["row"] <= 64
                     and type(node["lane"]) is int and 0 <= node["lane"] <= 64, "row/lane must be bounded logical integers")
            _require(node["kind"] in NODE_KINDS | {"unknown", "shop"}, "unsupported node kind")
            _confidence(node["confidence"], "node confidence")
            _require(type(node["outgoing_complete"]) is bool, "outgoing completeness must be explicit")
            _text(node["classification_evidence"], "node classification evidence")
            node = deepcopy(node)
            if node["kind"] == "shop":
                node["kind"] = "merchant"
            position = (node["row"], node["lane"])
            _require(position not in positions or positions[position] == node_id,
                     "overlap assigns different identities to the same row/lane")
            positions[position] = node_id
            if node_id in nodes:
                old = nodes[node_id]
                _require((old["row"], old["lane"]) == position, "node identity changed row/lane across views")
                _require(old["kind"] == node["kind"] or "unknown" in {old["kind"], node["kind"]},
                         "contradictory node classification across views")
                if node["kind"] != "unknown" and (old["kind"] == "unknown" or node["confidence"] > old["confidence"]):
                    old.update({key: node[key] for key in ("kind", "confidence", "classification_evidence")})
                old["outgoing_complete"] |= node["outgoing_complete"]
                old["view_ids"].append(view_id)
            else:
                nodes[node_id] = {**node, "view_ids": [view_id]}
            local[node_id] = node
        _require(len(nodes) <= MAX_NODES, "merged map exceeds node bound")
        local_edges = {}
        _require(isinstance(view["edges"], list) and len(view["edges"]) <= MAX_EDGES, "bounded edge list required")
        for edge in view["edges"]:
            _keys(edge, {"from_node_id", "to_node_id", "confidence", "evidence_note"})
            source_id, target_id = edge["from_node_id"], edge["to_node_id"]
            _require(source_id in local and target_id in local, "both edge endpoints must be in the same reviewed view")
            _require(local[source_id]["row"] < local[target_id]["row"], "edges must advance to a higher map row")
            _confidence(edge["confidence"], "edge confidence")
            _text(edge["evidence_note"], "edge evidence_note")
            key = (source_id, target_id)
            _require(key not in local_edges, "duplicate edge in view")
            local_edges[key] = edge
            if key not in edges:
                edges[key] = {**deepcopy(edge), "view_ids": [view_id]}
            else:
                edges[key]["confidence"] = max(edges[key]["confidence"], edge["confidence"])
                edges[key]["view_ids"].append(view_id)
        _require(len(edges) <= MAX_EDGES, "merged map exceeds edge bound")
        for node_id, node in local.items():
            if node["outgoing_complete"]:
                exits = {target for source, target in local_edges if source == node_id}
                _require(node_id not in complete_exits or complete_exits[node_id] == exits,
                         "contradictory complete outgoing edges across views")
                complete_exits[node_id] = exits
        for row in coverage["complete_rows"]:
            ids = {node_id for node_id, node in local.items() if node["row"] == row}
            _require(ids, "a declared complete row must contain its visible nodes")
            _require(row not in complete_rows or complete_rows[row] == ids,
                     "contradictory complete row: visible node omitted")
            complete_rows[row] = ids
        boss = view.get("expected_boss")
        if boss is not None:
            _keys(boss, {"name", "confidence", "evidence_note"})
            _text(boss["name"], "expected boss", 128)
            _require(boss["name"] in BOSSES_BY_ACT[draft["act"]], "expected boss is not a map boss for this act")
            _confidence(boss["confidence"], "boss confidence")
            _text(boss["evidence_note"], "boss portrait evidence")
            _require(coverage["top_visible"], "boss expectation must cite a top-map view")
            _require(expected_boss is None or expected_boss["name"] == boss["name"], "contradictory expected boss portraits")
            if expected_boss is None or boss["confidence"] > expected_boss["confidence"]:
                expected_boss = {**deepcopy(boss), "view_id": view_id, "basis": "reviewed_map_portrait", "encounter_confirmed": False}
    for row, ids in complete_rows.items():
        _require({key for key, node in nodes.items() if node["row"] == row} == ids,
                 "overlap added a node omitted from a declared complete row")
    for node_id, targets in complete_exits.items():
        _require({target for source, target in edges if source == node_id} == targets,
                 "overlap added an edge omitted from declared complete outgoing edges")
    ordered_nodes = sorted(nodes.values(), key=lambda node: (node["row"], node["lane"]))
    ordered_edges = [edges[key] for key in sorted(edges)]
    _parse_graph(draft["current_node_id"] if require_current else ordered_nodes[0]["node_id"], ordered_nodes, ordered_edges, False)
    rows = {node["row"] for node in ordered_nodes}
    row_gaps = sorted(set(range(min(rows), max(rows) + 1)) - set(complete_rows))
    connected_views = {sources[0]["view_id"]}
    changed = True
    while changed:
        changed = False
        connected_nodes = {node_id for view in sources if view["view_id"] in connected_views for node_id in view["node_ids"]}
        for view in sources:
            if view["view_id"] not in connected_views and connected_nodes.intersection(view["node_ids"]):
                connected_views.add(view["view_id"])
                changed = True
    overlap_complete = len(connected_views) == len(sources)
    graph_complete = top and bottom and not row_gaps and overlap_complete and all(node["outgoing_complete"] for node in ordered_nodes)
    unknown = []
    if not top:
        unknown.append("upper_map_not_reviewed")
    if not bottom:
        unknown.append("lower_map_not_reviewed")
    if row_gaps:
        unknown.append("incomplete_or_missing_rows")
    if not overlap_complete:
        unknown.append("view_overlap_gaps")
    if not all(node["outgoing_complete"] for node in ordered_nodes):
        unknown.append("some_outgoing_connections_unreviewed")
    if expected_boss is None or expected_boss["confidence"] < MAP_CHOICE_CONFIDENCE:
        unknown.append("expected_boss_unresolved")
    return {"schema": "veda.merged-map-survey.v1", "run_id": draft["run_id"], "act": draft["act"],
            "current_node_id": draft["current_node_id"], "nodes": ordered_nodes, "edges": ordered_edges,
            "views": sources, "coverage": {"top_visible": top, "bottom_visible": bottom,
                "complete_rows": sorted(complete_rows), "incomplete_rows": row_gaps,
                "overlapping_views_connected": overlap_complete, "graph_complete": graph_complete},
            "expected_boss": expected_boss, "encounter_confirmed": False, "unknowns": unknown,
            "source_time_basis": "reviewer-declared archival timestamps; live freshness is not established",
            "freshness_established": False, "controller_authorized": False, "runtime_authorized": False}


def _view_envelope(draft):
    _keys(draft, {"schema", "run_id", "act", "current_node_id", "view"})
    _require(draft["schema"] == "veda.map-survey-view-draft.v1", "source-free veda.map-survey-view-draft.v1 required")
    _keys(draft["view"], {"view_id", "coverage", "nodes", "edges"}, {"expected_boss"})
    return {"schema": SURVEY_SCHEMA, "run_id": draft["run_id"], "act": draft["act"],
            "current_node_id": draft["current_node_id"], "views": [{**deepcopy(draft["view"]),
                "source": {}, "review": {"complete": True, "reviewer": "schema-validation-only",
                                          "evidence_note": "Private structural validation; no image review established"}}]}


def validate_view_draft(draft):
    """Check a source-free view before capture. Return no synthetic source/review."""
    survey = _merge_survey(_view_envelope(draft), verify_sources=False, require_current=False)
    return {"valid": True, "schema": "veda.map-view-draft-validation.v1", "run_id": survey["run_id"],
            "act": survey["act"], "current_node_id": survey["current_node_id"],
            "nodes": len(survey["nodes"]), "edges": len(survey["edges"]),
            "image_review_established": False, "controller_authorized": False, "runtime_authorized": False}


def write_bound_view(draft, *, capture, reviewer, evidence_note, reviewed, output, now=None):
    """Bind one explicitly inspected fresh original capture, never action authority.

    The self-contained output keeps the run/act/current-node context around a
    single bound view. Combine its ``view`` with other inspected views in a
    veda.map-survey.v1 document. Binding derives hashes and capture timestamps;
    it does not transcribe nodes, recognize the map, or inspect pixels itself.
    """
    validate_view_draft(draft)
    bound = reviewed_capture_source(capture=capture, reviewer=reviewer, evidence_note=evidence_note,
                                    reviewed=reviewed, now=now)
    envelope = _view_envelope(draft)
    view = envelope["views"][0]
    view["source"] = {key: bound["source"][key] for key in ("path", "sha256", "captured_at")}
    view["review"] = {"complete": True, "reviewer": reviewer, "evidence_note": evidence_note}
    survey = _merge_survey(envelope, verify_sources=True, require_current=False)
    # Recheck the original receipt and source after graph validation. No manually
    # copied timestamp/hash or a changed capture can be substituted in this path.
    rechecked = reviewed_capture_source(capture=capture, reviewer=reviewer, evidence_note=evidence_note,
                                        reviewed=reviewed, now=now)
    _require(rechecked == bound, "capture changed while binding map view")
    artifact = {"schema": "veda.bound-map-view.v1", "run_id": draft["run_id"], "act": draft["act"],
                "current_node_id": draft["current_node_id"], "view": view,
                "controller_authorized": False, "runtime_authorized": False}
    path = write_artifact(output, artifact, survey)
    return {"valid": True, "view_file": path, "view_id": view["view_id"], "nodes": len(view["nodes"]),
            "controller_authorized": False, "runtime_authorized": False}


def _inventory(value, label, view_ids, run_id):
    _keys(value, {"status", "items", "evidence_note", "view_ids"}, {"ledger_reference"})
    _require(value["status"] in {"complete", "partial", "unknown"}, f"{label} completeness required")
    _require(isinstance(value["items"], list) and len(value["items"]) <= 128, f"bounded {label} items required")
    _require(isinstance(value["view_ids"], list) and len(value["view_ids"]) <= MAX_VIEWS
             and all(key in view_ids for key in value["view_ids"]), f"{label} references an absent view")
    _text(value["evidence_note"], f"{label} evidence")
    ledger = value.get("ledger_reference")
    if ledger is not None:
        _keys(ledger, {"run_id"}, {"baseline_id", "event_ids", "snapshot"})
        _require(ledger["run_id"] == run_id, "inventory ledger reference belongs to a different run")
        _require(any(key in ledger for key in ("baseline_id", "event_ids", "snapshot")), "inventory ledger reference needs an exact baseline, events, or snapshot")
        if "baseline_id" in ledger:
            _identifier(ledger["baseline_id"], "inventory baseline_id")
        if "event_ids" in ledger:
            _require(isinstance(ledger["event_ids"], list) and 1 <= len(ledger["event_ids"]) <= 128, "bounded nonempty inventory event IDs required")
            for key in ledger["event_ids"]:
                _identifier(key, "inventory event_id")
            _require(len(set(ledger["event_ids"])) == len(ledger["event_ids"]), "duplicate inventory event IDs")
        if "snapshot" in ledger:
            _keys(ledger["snapshot"], {"path", "sha256"})
            _text(ledger["snapshot"]["path"], "inventory snapshot path", 4096)
            with Path(ledger["snapshot"]["path"]).expanduser().open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            _require(len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == ledger["snapshot"]["sha256"], "inventory snapshot hash mismatch or size exceeded")
    _require(value["status"] == "unknown" or value["view_ids"] or ledger is not None,
             f"reviewed {label} requires evidence views or an exact ledger reference")
    _require(value["status"] != "unknown" or not value["items"], f"unknown {label} cannot assert items")
    for item in value["items"]:
        _keys(item, {"name", "count"})
        _text(item["name"], f"{label} item name", 128)
        _require(type(item["count"]) is int and 1 <= item["count"] <= 256, "item count must be a positive integer")
    _require(len({item["name"] for item in value["items"]}) == len(value["items"]), "combine duplicate inventory names into counts")
    return deepcopy(value)


def _route_comparison(option, criterion):
    """Comparison over one concrete continuation, never mixed branch benefits."""
    if criterion == "avoid_forced_elite":
        fact = option["elite_before_rest_unavoidable"]
        return ({False: 0, None: 1, True: 2}[fact],), fact
    if criterion == "elite_free_rest":
        fact = option["has_confirmed_elite_free_rest_path"]
        return ({True: 0, None: 1, False: 2}[fact],), fact
    if criterion in {"earliest_rest", "merchant_access"}:
        fact = option["rest" if criterion == "earliest_rest" else "merchant"]
        return ({True: 0, None: 1, False: 2}[fact["reachable"]], fact["minimum_confirmed_distance"] or 0), deepcopy(fact)
    fact = len(option["known_elite_node_ids"])
    return (fact,), {"known_elite_count_on_this_path": fact, "future_exposure_unknown": option["future_exposure_unknown"]}


def _continuations(survey, start):
    nodes, outgoing, _ = _parse_graph(survey["current_node_id"], survey["nodes"], survey["edges"], False)
    stack, paths = [[start]], []
    while stack and len(paths) < MAX_CONTINUATIONS:
        path = stack.pop()
        end = path[-1]
        children = [edge["to_node_id"] for edge in outgoing[end]
                    if edge["confidence"] >= MAP_CHOICE_CONFIDENCE and nodes[edge["to_node_id"]]["confirmed"]]
        uncertain = not nodes[end]["outgoing_complete"] or len(children) != len(outgoing[end])
        if not children or uncertain:
            rests = [i for i, key in enumerate(path) if nodes[key]["kind"] == "rest"]
            merchants = [i for i, key in enumerate(path) if nodes[key]["kind"] == "merchant"]
            elites = [i for i, key in enumerate(path) if nodes[key]["kind"] == "elite"]
            first_rest = min(rests, default=None)
            before = bool(elites and (first_rest is None or min(elites) < first_rest))
            before_fact = True if before else False if first_rest is not None or not uncertain else None
            free_rest = (not before) if rests else None if uncertain else False
            paths.append({"node_ids": path, "terminal_reason": "unreviewed_continuation" if uncertain else
                          "boss" if nodes[end]["kind"] == "boss" else "confirmed_end",
                "future_exposure_unknown": uncertain,
                "known_elite_node_ids": [path[i] for i in elites], "rest_node_ids": [path[i] for i in rests],
                "merchant_node_ids": [path[i] for i in merchants],
                "elite_before_rest_unavoidable": before_fact, "has_confirmed_elite_free_rest_path": free_rest,
                "rest": {"reachable": True if rests else None if uncertain else False, "minimum_confirmed_distance": first_rest},
                "merchant": {"reachable": True if merchants else None if uncertain else False,
                             "minimum_confirmed_distance": min(merchants, default=None)}})
        stack.extend(path + [child] for child in reversed(children))
    return paths, bool(stack)


def plan_routes(survey, review):
    """Rank known immediate choices by explicit lexicographic priorities.

    The reviewer supplies health/elite readiness and rationale, including the
    actual deck, relic and potion evidence. Critical/strained health or avoid/unknown
    elite readiness prefixes conservative topology criteria. The exact applied
    order is returned; ties stay ties. No survival probability is computed.
    """
    _require(survey.get("schema") == "veda.merged-map-survey.v1", "merged map survey required")
    _keys(review, {"schema", "run_id", "act", "ascension", "current_node_id", "reviewer", "resources", "readiness", "criteria", "boss_preparation"})
    _require(review["schema"] == PLAN_SCHEMA, "veda.map-plan-review.v1 required")
    _require(all(review[key] == survey[key] for key in ("run_id", "act", "current_node_id")), "plan and survey context must match")
    _require(type(review["ascension"]) is int and 0 <= review["ascension"] <= 20, "actual ascension 0..20 required")
    _text(review["reviewer"], "planner reviewer", 128)
    resources = review["resources"]
    _keys(resources, {"hp", "max_hp", "gold", "deck", "relics", "potions", "evidence_note", "view_ids"})
    _require(type(resources["max_hp"]) is int and resources["max_hp"] > 0
             and type(resources["hp"]) is int and 0 <= resources["hp"] <= resources["max_hp"]
             and type(resources["gold"]) is int and resources["gold"] >= 0, "explicit valid HP and gold required")
    view_ids = {view["view_id"] for view in survey["views"]}
    _require(isinstance(resources["view_ids"], list) and resources["view_ids"]
             and all(key in view_ids for key in resources["view_ids"]), "resources require inspected view IDs")
    _text(resources["evidence_note"], "resource evidence")
    inventories = {key: _inventory(resources[key], key, view_ids, review["run_id"]) for key in ("deck", "relics", "potions")}
    readiness = review["readiness"]
    _keys(readiness, {"health", "elites", "shop", "rationale"})
    _require(readiness["health"] in {"critical", "strained", "comfortable", "unknown"}, "explicit reviewed health readiness required")
    _require(readiness["elites"] in {"ready", "limited", "avoid", "unknown"}, "explicit reviewed elite readiness required")
    _require(readiness["shop"] in {"useful", "optional", "defer", "unknown"}, "explicit reviewed shop value required")
    _text(readiness["rationale"], "readiness rationale")
    criteria = review["criteria"]
    _require(isinstance(criteria, list) and criteria and len(criteria) <= len(CRITERIA)
             and all(isinstance(key, str) and key in CRITERIA for key in criteria)
             and len(set(criteria)) == len(criteria), "unique ordered supported route criteria required")
    conservative = readiness["health"] in {"critical", "strained", "unknown"} or readiness["elites"] in {"avoid", "unknown"}
    applied = (["avoid_forced_elite", "elite_free_rest", "earliest_rest"] if conservative else []) + criteria
    applied = list(dict.fromkeys(applied))
    brief = summarize_routes(current_node_id=survey["current_node_id"], visible_nodes=survey["nodes"],
                            visible_edges=survey["edges"], graph_complete=survey["coverage"]["graph_complete"],
                            resources={key: resources[key] for key in ("hp", "max_hp", "gold")})
    comparisons = []
    for option in brief["options"]:
        paths, truncated = _continuations(survey, option["node_id"])
        evaluated = []
        for path in paths:
            comparison = [_route_comparison(path, criterion) for criterion in applied]
            evaluated.append((tuple(key for key, _ in comparison), path, comparison))
        evaluated.sort(key=lambda row: (row[0], row[1]["node_ids"]))
        best_key, _, comparison = evaluated[0]
        tied = [path for key, path, _ in evaluated if key == best_key]
        comparisons.append((best_key, {"node_id": option["node_id"], "kind": option["kind"],
            "criteria": {criterion: value for criterion, (_, value) in zip(applied, comparison)}, "topology": option,
            "preferred_continuations": tied[:5], "equally_ranked_continuation_count": len(tied),
            "evaluated_continuation_count": len(paths), "continuation_search_truncated": truncated}))
    comparisons.sort(key=lambda row: (row[0], row[1]["node_id"]))
    ranked = []
    previous = None
    rank = 0
    for index, (key, option) in enumerate(comparisons, 1):
        if key != previous:
            rank = index
        ranked.append({**option, "rank": rank})
        previous = key
    boss = survey["expected_boss"]
    prep = review["boss_preparation"]
    _keys(prep, {"expected_name", "priorities"})
    _require(prep["expected_name"] == (boss["name"] if boss and boss["confidence"] >= MAP_CHOICE_CONFIDENCE else None),
             "boss preparation must match the supported expectation or remain general")
    _require(isinstance(prep["priorities"], list) and len(prep["priorities"]) <= 12, "bounded preparation priorities required")
    for item in prep["priorities"]:
        _keys(item, {"need", "reason", "reference"})
        for field in ("need", "reason", "reference"):
            _text(item[field], f"boss preparation {field}")
    warnings = list(survey["unknowns"]) + brief["warnings"]
    warnings += [f"{label}_inventory_{value['status']}" for label, value in inventories.items() if value["status"] != "complete"]
    if any(row["continuation_search_truncated"] for row in ranked):
        warnings.append("bounded_continuation_search_truncated; rankings are provisional among evaluated paths")
    if boss and boss["confidence"] >= MAP_CHOICE_CONFIDENCE and not prep["priorities"]:
        warnings.append("expected_boss_preparation_not_yet_reviewed")
    if review["act"] == 3 and review["ascension"] == 20:
        warnings.append("A20_second_act3_boss_not_identified_by_first_map_portrait; prepare for remaining possibilities and confirm upon entry")
    return {"schema": "veda.map-plan.v1", "run_id": survey["run_id"], "act": survey["act"],
        "ascension": review["ascension"],
        "current_node_id": survey["current_node_id"], "survey_sha256": survey_digest(survey),
        "resources": deepcopy(resources), "inventory_reference_basis": "reviewer-linked views or ledger IDs; referenced database rows are not independently re-read",
        "readiness": deepcopy(readiness), "reviewer": review["reviewer"],
        "requested_criteria": criteria[:], "applied_criteria": applied, "conservative_health_or_elite_prefix": conservative,
        "ranking_basis": "lexicographic reviewed priorities over known topology; unknown outcomes are not survival estimates",
        "options": ranked, "recommended_next_node_ids": [item["node_id"] for item in ranked if item["rank"] == 1],
        "unverified_next_nodes": brief["unverified_reachable"], "immediate_choice_available": bool(ranked),
        "full_route_planning_complete": survey["coverage"]["graph_complete"] and not any(row["continuation_search_truncated"] for row in ranked),
        "expected_boss": deepcopy(boss),
        "boss_preparation": deepcopy(prep), "encounter_confirmed": False,
        "research_targets": {"expected_boss": prep["expected_name"], "act": survey["act"], "ascension": review["ascension"],
            "second_act3_boss_unresolved": review["act"] == 3 and review["ascension"] == 20,
            "scope": "current ascension boss and elite behavior; preserve source-specific ascension limits"},
        "replan_after": ["each room arrival", "card or relic reward", "potion change", "material HP or gold change", "new map evidence"],
        "warnings": warnings, "fresh_action_review_required": True,
        "controller_authorized": False, "runtime_authorized": False}


def write_artifact(path, value, survey):
    """Recheck retained image identities and exclusively create a private artifact."""
    for view in survey["views"]:
        _source(view["source"])
    destination = Path(path).expanduser()
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with destination.open("x") as stream:
        stream.write(payload)
    return str(destination.resolve())
