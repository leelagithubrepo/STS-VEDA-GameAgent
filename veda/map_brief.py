"""Bounded, declaration-based route facts; no route scoring or input authority.

The caller supplies reviewed visible nodes, directed edges, and optional resource
context. This module does not recognize a map or establish freshness. A missing
edge confidence means the caller declares that edge confirmed. Missing outgoing
completeness means unknown, unless graph_complete was explicitly set to True.

Distances count edges from each proposed next node (a rest option has distance
zero to itself). Distance maps use confirmed classifications and edges only.
They are minima within that confirmed subgraph, not lower bounds on unseen maps.
An event/question room has an unknown outcome, even on an elite-free node path.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import math
from typing import Any

from .map_reader import MAP_CHOICE_CONFIDENCE, NODE_KINDS


MAX_NODES = 128
MAX_EDGES = 512
MAX_CONTEXT_ITEMS = 256


def _identifier(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 128
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"{label} must be a nonempty identifier of at most 128 characters")
    return value


def _confidence(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError(f"{label} must be finite and between zero and one")
    return float(value)


def _json_copy(value: Any, label: str) -> Any:
    """Bound nested reviewer context without interpreting its strategic value."""
    remaining = MAX_CONTEXT_ITEMS

    def visit(item: Any, depth: int) -> Any:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 5:
            raise ValueError(f"{label} exceeds the bounded context size")
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            if len(item) > 1024:
                raise ValueError(f"{label} contains oversized text")
            return item
        if isinstance(item, (int, float)):
            if not -10**15 <= item <= 10**15 or not math.isfinite(item):
                raise ValueError(f"{label} contains a nonfinite or oversized number")
            return item
        if isinstance(item, list):
            if len(item) > MAX_CONTEXT_ITEMS:
                raise ValueError(f"{label} exceeds the bounded context size")
            return [visit(child, depth + 1) for child in item]
        if isinstance(item, dict):
            if len(item) > MAX_CONTEXT_ITEMS:
                raise ValueError(f"{label} exceeds the bounded context size")
            if any(not isinstance(key, str) or not key or len(key) > 128 for key in item):
                raise ValueError(f"{label} contains an invalid object key")
            return {key: visit(child, depth + 1) for key, child in sorted(item.items())}
        raise ValueError(f"{label} must contain JSON-compatible values")

    return visit(value, 0)


def _parse_graph(current_node_id: str, visible_nodes: list[dict], visible_edges: list[dict], graph_complete: bool):
    _identifier(current_node_id, "current_node_id")
    if type(graph_complete) is not bool:
        raise ValueError("graph_complete must be a boolean")
    if not isinstance(visible_nodes, list) or not 1 <= len(visible_nodes) <= MAX_NODES:
        raise ValueError(f"visible_nodes must contain 1..{MAX_NODES} nodes")
    if not isinstance(visible_edges, list) or len(visible_edges) > MAX_EDGES:
        raise ValueError(f"visible_edges must contain at most {MAX_EDGES} edges")
    nodes = {}
    for row in visible_nodes:
        if not isinstance(row, dict):
            raise ValueError("each visible node must be an object")
        node_id = _identifier(row.get("node_id"), "node_id")
        if node_id in nodes:
            raise ValueError("node IDs must be unique")
        kind = row.get("kind")
        if kind == "shop":
            kind = "merchant"
        if kind is not None and (not isinstance(kind, str) or kind not in NODE_KINDS | {"unknown"}):
            raise ValueError("node kind must be recognized or explicitly unknown")
        confidence = _confidence(row.get("confidence"), "node confidence")
        complete = row.get("outgoing_complete", graph_complete)
        if type(complete) is not bool:
            raise ValueError("outgoing_complete must be a boolean")
        basis = _json_copy(row.get("classification_evidence"), "classification_evidence")
        if basis is not None and not isinstance(basis, (str, list, dict)):
            raise ValueError("classification_evidence must be text, an array, or an object")
        has_basis = bool(basis.strip()) if isinstance(basis, str) else bool(basis)
        issues = []
        if kind in {None, "unknown"}:
            issues.append("unknown_node_kind")
        if confidence < MAP_CHOICE_CONFIDENCE:
            issues.append("node_confidence_below_threshold")
        if kind in {"elite", "merchant"} and not has_basis:
            issues.append("missing_classification_evidence")
        nodes[node_id] = {
            "node_id": node_id, "kind": kind or "unknown", "confidence": confidence,
            "classification_evidence": basis, "outgoing_complete": complete,
            "confirmed": not issues, "issues": issues,
        }
    if current_node_id not in nodes:
        raise ValueError("current_node_id must identify a visible node")
    edges, seen = [], set()
    outgoing = {node_id: [] for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for row in visible_edges:
        if not isinstance(row, dict):
            raise ValueError("each visible edge must be an object")
        source = _identifier(row.get("from_node_id"), "from_node_id")
        target = _identifier(row.get("to_node_id"), "to_node_id")
        if source not in nodes or target not in nodes:
            raise ValueError("every edge must connect visible node IDs")
        if source == target or (source, target) in seen:
            raise ValueError("self-loops and duplicate edges are invalid")
        seen.add((source, target))
        edge = {"from_node_id": source, "to_node_id": target,
                "confidence": _confidence(row.get("confidence", 1.0), "edge confidence")}
        edges.append(edge)
        outgoing[source].append(edge)
        indegree[target] += 1
    queue = deque(sorted(node_id for node_id, count in indegree.items() if not count))
    ordered = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for edge in outgoing[node_id]:
            target = edge["to_node_id"]
            indegree[target] -= 1
            if not indegree[target]:
                queue.append(target)
    if len(ordered) != len(nodes):
        raise ValueError("visible graph must be directed and acyclic, including unverified edges")
    for rows in outgoing.values():
        rows.sort(key=lambda edge: edge["to_node_id"])
    return nodes, outgoing, ordered


def _walk(start: str, nodes: dict, outgoing: dict, *, avoid_elites: bool = False,
          confirmed_nodes_only: bool = True):
    distances, frontier, uncertain_nodes, uncertain_edges = {start: 0}, set(), set(), []
    queue = deque([start])
    while queue:
        node_id = queue.popleft()
        node = nodes[node_id]
        if avoid_elites and node["confirmed"] and node["kind"] == "elite":
            continue
        if not node["confirmed"]:
            uncertain_nodes.add(node_id)
            frontier.add(node_id)
        if not node["outgoing_complete"]:
            frontier.add(node_id)
        for edge in outgoing[node_id]:
            target = edge["to_node_id"]
            if edge["confidence"] < MAP_CHOICE_CONFIDENCE:
                frontier.add(node_id)
                uncertain_edges.append(dict(edge))
                continue
            if not nodes[target]["confirmed"]:
                uncertain_nodes.add(target)
                frontier.add(node_id)
                if confirmed_nodes_only:
                    continue
            if target not in distances:
                distances[target] = distances[node_id] + 1
                queue.append(target)
    return distances, frontier, uncertain_nodes, uncertain_edges


def _targets(distances: dict, nodes: dict, kind: str) -> dict:
    return {node_id: distances[node_id] for node_id in sorted(distances)
            if nodes[node_id]["confirmed"] and nodes[node_id]["kind"] == kind}


def _exists(targets: dict, frontier: set) -> bool | None:
    return True if targets else None if frontier else False


def _elite_unavoidable(nodes: dict, outgoing: dict, ordered: list[str]) -> dict:
    """Every continuation meets an elite node before a rest or confirmed end.

    A known bypass proves False. Unreadable kinds/edges or incomplete exits keep
    a universal assertion unknown; a known elite at the entry proves True.
    This says nothing about combat/event damage or survival.
    """
    result = {}
    for node_id in reversed(ordered):
        node = nodes[node_id]
        if not node["confirmed"]:
            result[node_id] = None
        elif node["kind"] == "elite":
            result[node_id] = True
        elif node["kind"] == "rest":
            result[node_id] = False
        else:
            children = [result[edge["to_node_id"]] for edge in outgoing[node_id]
                        if edge["confidence"] >= MAP_CHOICE_CONFIDENCE]
            unknown_exits = not node["outgoing_complete"] or any(
                edge["confidence"] < MAP_CHOICE_CONFIDENCE for edge in outgoing[node_id])
            if False in children:
                result[node_id] = False
            elif unknown_exits or None in children:
                result[node_id] = None
            else:
                result[node_id] = bool(children)
    return result


def summarize_routes(*, current_node_id: str, visible_nodes: list[dict[str, Any]],
                     visible_edges: list[dict[str, Any]], resources: dict[str, Any] | None = None,
                     graph_complete: bool = False) -> dict[str, Any]:
    """Summarize reviewed visible route topology without choosing a route.

    Each node needs node_id, kind and confidence; optional outgoing_complete
    describes all its exits, not whether unseen future rooms are known. Elite
    and merchant/shop classifications additionally need a nonempty evidence
    declaration, retained verbatim. Edges need from_node_id/to_node_id and may
    supply confidence. Coordinates and input ordering have no meaning here.

    True reachability is proved by a confirmed path. False requires a closed
    search; null means no such path was proved and missing evidence may matter.
    A topological path avoiding *known* elites may cross an unknown node; the
    separate confirmed-elite-free predicate does not. Neither promises safety.
    Resources are only copied, never scored, converted into risk, or verified.
    """
    nodes, outgoing, ordered = _parse_graph(current_node_id, visible_nodes, visible_edges, graph_complete)
    if resources is not None and not isinstance(resources, dict):
        raise ValueError("resources must be an object or null")
    context = _json_copy(resources if resources is not None else {}, "resources")
    for key in ("hp", "max_hp", "gold"):
        value = context.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"resources.{key} must be a nonnegative integer or null")
    if context.get("hp") is not None and context.get("max_hp") is not None and context["hp"] > context["max_hp"]:
        raise ValueError("resources.hp cannot exceed max_hp")
    forced_elite = _elite_unavoidable(nodes, outgoing, ordered)
    options, unverified = [], []
    for edge in outgoing[current_node_id]:
        node_id = edge["to_node_id"]
        node = nodes[node_id]
        reasons = list(node["issues"])
        if edge["confidence"] < MAP_CHOICE_CONFIDENCE:
            reasons.append("edge_confidence_below_threshold")
        if reasons:
            unverified.append({"node_id": node_id, "declared_kind": node["kind"], "reasons": reasons,
                               "confidence": node["confidence"], "edge_confidence": edge["confidence"],
                               "classification_evidence": deepcopy(node["classification_evidence"])})
            continue
        distances, frontier, uncertain_nodes, uncertain_edges = _walk(node_id, nodes, outgoing)
        rests, merchants = _targets(distances, nodes, "rest"), _targets(distances, nodes, "merchant")
        no_elite, no_elite_frontier, _, _ = _walk(node_id, nodes, outgoing, avoid_elites=True)
        no_elite_rests = _targets(no_elite, nodes, "rest")
        topology, topology_frontier, _, _ = _walk(node_id, nodes, outgoing, avoid_elites=True, confirmed_nodes_only=False)
        topology_rests = _targets(topology, nodes, "rest")
        branches = []
        for visited in sorted(distances):
            rows = outgoing[visited]
            confirmed_exits = [row["to_node_id"] for row in rows
                               if row["confidence"] >= MAP_CHOICE_CONFIDENCE and nodes[row["to_node_id"]]["confirmed"]]
            unverified_exits = [row["to_node_id"] for row in rows if row["to_node_id"] not in confirmed_exits]
            if len(rows) > 1 or not nodes[visited]["outgoing_complete"] or unverified_exits:
                branches.append({"node_id": visited, "confirmed_exit_ids": confirmed_exits,
                                 "unverified_exit_ids": unverified_exits,
                                 "outgoing_complete": nodes[visited]["outgoing_complete"]})
        options.append({
            "node_id": node_id, "kind": node["kind"],
            "classification_evidence": deepcopy(node["classification_evidence"]),
            "reachable_rest_min_distance": rests,
            "reachable_merchant_min_distance": merchants,
            "rest": {"reachable": _exists(rests, frontier), "minimum_confirmed_distance": min(rests.values(), default=None)},
            "merchant": {"reachable": _exists(merchants, frontier), "minimum_confirmed_distance": min(merchants.values(), default=None)},
            "known_elite_node_ids": sorted(key for key in distances if nodes[key]["kind"] == "elite"),
            "has_rest_path_avoiding_known_elites": _exists(topology_rests, topology_frontier),
            "has_confirmed_elite_free_rest_path": _exists(no_elite_rests, no_elite_frontier),
            "elite_before_rest_unavoidable": forced_elite[node_id],
            "branching_exits": branches, "frontier_node_ids": sorted(frontier),
            "uncertain_node_ids": sorted(uncertain_nodes), "uncertain_edges": uncertain_edges,
            "event_node_ids": sorted(key for key in distances if nodes[key]["kind"] == "event"),
        })
    warnings = []
    if unverified:
        warnings.append("Unverified next nodes are excluded from confirmed options.")
    if not nodes[current_node_id]["outgoing_complete"]:
        warnings.append("The current node may have additional unobserved exits.")
    if any(option["frontier_node_ids"] for option in options):
        warnings.append("Uncertain nodes, edges, or incomplete exits limit future-route conclusions.")
    if any(option["event_node_ids"] for option in options):
        warnings.append("Event/question-room outcomes are unknown; an elite-free node path is not a safety prediction.")
    return {
        "schema": "veda.map-brief.v1", "current_node_id": current_node_id,
        "evidence_origin": "reviewed_declarations", "freshness_established": False,
        "confirmation_threshold": MAP_CHOICE_CONFIDENCE,
        "distance_scope": "edges from the proposed next node, within confirmed visible paths",
        "resources": context, "resources_role": "reviewer_context_only",
        "graph_complete_declared": graph_complete,
        "current_exits_complete": nodes[current_node_id]["outgoing_complete"],
        "options": options, "unverified_reachable": unverified, "warnings": warnings,
        "blocked": not options, "controller_authorized": False, "runtime_authorized": False,
    }
