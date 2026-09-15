#!/usr/bin/env python3
"""Operate VEDA's local SQLite memory without exposing it publicly."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.telemetry_database import TelemetryDatabase
from veda.observation import capture_visible_ps5_feed


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "artifacts" / "veda-memory.sqlite3"


def _document(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def _database(path: Path) -> TelemetryDatabase:
    database = TelemetryDatabase(path)
    database.initialize()
    return database


def _required_evidence_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """Return supplied evidence or capture one passive frame before logging."""
    if args.capture and args.screenshot:
        parser.error("use either --capture or --screenshot, not both")
    if args.capture:
        return str(capture_visible_ps5_feed())
    if args.screenshot:
        return args.screenshot
    parser.error("a meaningful decision or completed floor requires --capture or --screenshot")


def main() -> int:
    parser = argparse.ArgumentParser(description="Private SQLite telemetry for standalone VEDA")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")

    run = commands.add_parser("run")
    run.add_argument("--game", default="Slay the Spire")
    run.add_argument("--character", default="Ironclad")
    run.add_argument("--ascension", type=int)

    run_new = commands.add_parser("run-new", help="explicitly start a new run and archive matching active runs")
    run_new.add_argument("--game", default="Slay the Spire")
    run_new.add_argument("--character", default="Ironclad")
    run_new.add_argument("--ascension", type=int)
    run_new.add_argument("--metadata", type=_document, default={})

    floor_start = commands.add_parser("floor-start", help="open a floor before logging its decisions")
    floor_start.add_argument("--run-id", required=True)
    floor_start.add_argument("--act", type=int)
    floor_start.add_argument("--floor", type=int)
    floor_start.add_argument("--node-type")
    floor_start.add_argument("--state", type=_document, required=True)
    floor_start.add_argument("--summary", type=_document, default={})

    floor_finish = commands.add_parser("floor-finish", help="close a floor with its observed outcome")
    floor_finish.add_argument("--floor-id", required=True)
    floor_finish.add_argument("--outcome", required=True)
    floor_finish.add_argument("--state", type=_document, required=True)
    floor_finish.add_argument("--summary", type=_document, default={})
    floor_finish.add_argument("--screenshot")
    floor_finish.add_argument("--capture", action="store_true", help="passively capture the visible game screen before closing")

    decision = commands.add_parser("decision", help="record a recommendation before the player acts")
    decision.add_argument("--run-id", required=True)
    decision.add_argument("--phase", required=True)
    decision.add_argument("--state", type=_document, required=True)
    decision.add_argument("--options", type=_document, required=True, help='JSON object, for example {"cards":["Strike"]}')
    decision.add_argument("--recommendation", type=_document, required=True)
    decision.add_argument("--reasoning", required=True)
    decision.add_argument("--prediction", type=_document, required=True)
    decision.add_argument("--floor-id", required=True)
    decision.add_argument("--combat-id")
    decision.add_argument("--turn-id")
    decision.add_argument("--screenshot")
    decision.add_argument("--capture", action="store_true", help="passively capture the visible game screen before recommending")
    decision.add_argument("--safety-margin-hp", type=int)
    decision.add_argument("--confidence", type=float)

    resolve = commands.add_parser("resolve", help="attach the player action and observed result")
    resolve.add_argument("--decision-id", required=True)
    resolve.add_argument("--action", type=_document, required=True)
    resolve.add_argument("--outcome", type=_document, required=True)
    resolve.add_argument("--skipped", action="store_true")

    review = commands.add_parser("review", help="queue LLM analysis for a safe boundary")
    review.add_argument("--run-id", required=True)
    review.add_argument("--trigger", required=True)
    review.add_argument("--priority", choices=("routine", "important", "critical"), required=True)
    review.add_argument("--summary", type=_document, required=True)
    review.add_argument("--floor-id")
    review.add_argument("--evidence-id", action="append", default=[])

    builder_request = commands.add_parser("builder-request", help="record a persistent change for Builder")
    builder_request.add_argument("--requested-by", default="spire")
    builder_request.add_argument("--title", required=True)
    builder_request.add_argument("--rationale", required=True)
    builder_request.add_argument("--scope", type=_document, required=True)
    builder_request.add_argument("--dedupe-key")

    builder_update = commands.add_parser("builder-update", help="record Builder acceptance, implementation, or verification")
    builder_update.add_argument("--request-id", required=True)
    builder_update.add_argument("--status", choices=("accepted", "implemented", "verified", "deferred"), required=True)
    builder_update.add_argument("--summary", required=True)
    builder_update.add_argument("--verification", action="store_true")

    builder_list = commands.add_parser("builder-requests", help="read local Builder requests")
    builder_list.add_argument("--status", choices=("requested", "accepted", "implemented", "verified", "deferred"))

    route_snapshot = commands.add_parser("route-snapshot", help="record only confirmed legal next map nodes")
    route_snapshot.add_argument("--run-id", required=True)
    route_snapshot.add_argument("--act", type=int)
    route_snapshot.add_argument("--floor", type=int)
    route_snapshot.add_argument("--current-node", required=True)
    route_snapshot.add_argument("--legal-next-nodes", type=_document, required=True, help='JSON object with a "nodes" list')
    route_snapshot.add_argument("--resources", type=_document, required=True)
    route_snapshot.add_argument("--floor-id")
    route_snapshot.add_argument("--screenshot")
    route_snapshot.add_argument("--capture", action="store_true", help="passively capture the visible map before logging")
    route_snapshot.add_argument("--source", default="veda")
    route_snapshot.add_argument("--confidence", type=float)

    route_recommendation = commands.add_parser("route-recommendation", help="record a legal route choice and its tradeoff")
    route_recommendation.add_argument("--snapshot-id", required=True)
    route_recommendation.add_argument("--selected-node", required=True)
    route_recommendation.add_argument("--safety-rationale", required=True)
    route_recommendation.add_argument("--reward-tradeoff", required=True)

    route_latest = commands.add_parser("route-latest", help="read the latest confirmed route snapshot")
    route_latest.add_argument("--run-id", required=True)

    combat_state = commands.add_parser("combat-state", help="record a confirmed combat snapshot")
    combat_state.add_argument("--run-id", required=True)
    combat_state.add_argument("--phase", required=True)
    combat_state.add_argument("--state", type=_document, required=True)
    combat_state.add_argument("--payload", type=_document, default={})
    combat_state.add_argument("--floor-id")
    combat_state.add_argument("--combat-id")
    combat_state.add_argument("--turn-id")
    combat_state.add_argument("--screenshot")
    combat_state.add_argument("--confidence", type=float)

    combat_ledger = commands.add_parser("combat-ledger", help="read confirmed combat snapshots")
    combat_ledger.add_argument("--run-id", required=True)
    combat_ledger.add_argument("--floor-id")

    zones_start = commands.add_parser("combat-zones-start", help="record confirmed deck and opening hand for one combat")
    zones_start.add_argument("--combat-id", required=True)
    zones_start.add_argument("--deck", type=_document, required=True, help='JSON object with a "cards" list')
    zones_start.add_argument("--hand", type=_document, required=True, help='JSON object with a "cards" list')
    zones_start.add_argument("--source", required=True)
    zones_start.add_argument("--confidence", type=float)

    zones_event = commands.add_parser("combat-zone-event", help="record one confirmed card movement or unknown gap")
    zones_event.add_argument("--combat-id", required=True)
    zones_event.add_argument("--turn-id")
    zones_event.add_argument("--kind", choices=("draw", "play", "discard", "exhaust", "return", "generate", "shuffle", "unknown"), required=True)
    zones_event.add_argument("--card")
    zones_event.add_argument("--from-zone", choices=("hand", "draw", "discard", "exhaust"))
    zones_event.add_argument("--to-zone", choices=("hand", "draw", "discard", "exhaust"))
    zones_event.add_argument("--count", type=int, default=1)
    zones_event.add_argument("--reason")
    zones_event.add_argument("--source", required=True)
    zones_event.add_argument("--confidence", type=float)

    zones_state = commands.add_parser("combat-zone-state", help="retrieve only the replayable confirmed card zones")
    zones_state.add_argument("--combat-id", required=True)

    inventory_event = commands.add_parser("inventory-event", help="record a confirmed card, relic, or potion state change")
    inventory_event.add_argument("--run-id", required=True)
    inventory_event.add_argument("--kind", choices=("card", "relic", "potion"), required=True)
    inventory_event.add_argument("--action", choices=("acquired", "removed", "consumed", "replaced", "property_confirmed"), required=True)
    inventory_event.add_argument("--item", required=True)
    inventory_event.add_argument("--related-item")
    inventory_event.add_argument("--property")
    inventory_event.add_argument("--floor-id")
    inventory_event.add_argument("--screenshot")
    inventory_event.add_argument("--source", required=True)
    inventory_event.add_argument("--confidence", type=float)

    inventory_ledger = commands.add_parser("inventory-ledger", help="retrieve confirmed run inventory and its history")
    inventory_ledger.add_argument("--run-id", required=True)

    inventory_baseline = commands.add_parser(
        "inventory-baseline",
        help="record the current observed inventory without claiming earlier acquisition timing",
    )
    inventory_baseline.add_argument("--run-id", required=True)
    inventory_baseline.add_argument("--items", type=_document, required=True, help='JSON object with an "items" list')
    inventory_baseline.add_argument(
        "--coverage", type=_document, required=True,
        help='JSON object: card/relic/potion each complete, partial, or unknown',
    )
    inventory_baseline.add_argument("--floor-id")
    inventory_baseline.add_argument("--screenshot")
    inventory_baseline.add_argument("--capture", action="store_true", help="passively capture the visible inventory before logging")
    inventory_baseline.add_argument("--source", required=True)
    inventory_baseline.add_argument("--confidence", type=float)

    map_snapshot = commands.add_parser("map-snapshot", help="record the full map graph visibly present on one screen")
    map_snapshot.add_argument("--run-id", required=True)
    map_snapshot.add_argument("--current-node", required=True)
    map_snapshot.add_argument("--nodes", type=_document, required=True, help='JSON object with a "nodes" list')
    map_snapshot.add_argument("--edges", type=_document, required=True, help='JSON object with an "edges" list')
    map_snapshot.add_argument("--act", type=int)
    map_snapshot.add_argument("--floor", type=int)
    map_snapshot.add_argument("--floor-id")
    map_snapshot.add_argument("--screenshot")
    map_snapshot.add_argument("--capture", action="store_true", help="passively capture the visible map before logging")
    map_snapshot.add_argument("--source", required=True)
    map_snapshot.add_argument("--confidence", type=float)

    map_latest = commands.add_parser("map-latest", help="retrieve the latest complete visible map graph")
    map_latest.add_argument("--run-id", required=True)

    combat_start = commands.add_parser("combat-start", help="open a combat anchored to a recorded floor")
    combat_start.add_argument("--run-id", required=True)
    combat_start.add_argument("--floor-id", required=True)
    combat_start.add_argument("--state", type=_document, required=True)
    combat_start.add_argument("--encounter-name")
    combat_start.add_argument("--encounter-type")
    combat_start.add_argument("--summary", type=_document, default={})

    combat_finish = commands.add_parser("combat-finish", help="close a combat with its observed outcome")
    combat_finish.add_argument("--combat-id", required=True)
    combat_finish.add_argument("--outcome", required=True)
    combat_finish.add_argument("--state", type=_document, required=True)
    combat_finish.add_argument("--summary", type=_document, default={})

    turn_start = commands.add_parser("turn-start", help="open a numbered turn within a combat")
    turn_start.add_argument("--combat-id", required=True)
    turn_start.add_argument("--number", type=int, required=True)
    turn_start.add_argument("--phase", default="combat")
    turn_start.add_argument("--state", type=_document, required=True)
    turn_start.add_argument("--summary", type=_document, default={})

    turn_finish = commands.add_parser("turn-finish", help="close a combat turn with its observed state")
    turn_finish.add_argument("--turn-id", required=True)
    turn_finish.add_argument("--state", type=_document, required=True)
    turn_finish.add_argument("--summary", type=_document, default={})

    floor_retrospective = commands.add_parser("floor-retrospective", help="read only evidence and decisions linked to one floor")
    floor_retrospective.add_argument("--floor-id", required=True)

    floor_completeness = commands.add_parser("floor-completeness", help="report evidence gaps before a floor retrospective")
    floor_completeness.add_argument("--floor-id", required=True)

    args = parser.parse_args()
    database = _database(args.database)
    if args.command == "status":
        print(json.dumps(database.status(), indent=2, sort_keys=True))
    elif args.command == "run":
        print(database.start_or_resume_run(game=args.game, character_name=args.character, ascension=args.ascension))
    elif args.command == "run-new":
        print(database.start_new_run(
            game=args.game, character_name=args.character, ascension=args.ascension, metadata=args.metadata,
        ))
    elif args.command == "floor-start":
        print(database.record_floor(
            run_id=args.run_id, act=args.act, floor=args.floor, node_type=args.node_type,
            outcome=None, starting_state=args.state, summary=args.summary,
        ))
    elif args.command == "floor-finish":
        screenshot_path = _required_evidence_path(parser, args)
        database.complete_floor(
            floor_id=args.floor_id, outcome=args.outcome, ending_state=args.state, summary=args.summary,
        )
        floor = database.floor_retrospective(floor_id=args.floor_id)["floor"]
        evidence_id = database.record_event(
            run_id=floor["run_id"], floor_id=args.floor_id, kind="floor_completion_evidence",
            phase="safe_boundary", state=args.state,
            payload={"outcome": args.outcome, "summary": args.summary}, screenshot_path=screenshot_path,
            source="passive-screen-capture" if args.capture else "supplied-screenshot", confidence=1.0,
        )
        print(json.dumps({"floor_id": args.floor_id, "evidence_id": evidence_id, "screenshot": screenshot_path}))
    elif args.command == "decision":
        screenshot_path = _required_evidence_path(parser, args)
        decision_id = database.record_decision(
            run_id=args.run_id, phase=args.phase, state=args.state, options=[args.options], recommendation=args.recommendation,
            reasoning=args.reasoning, prediction=args.prediction, floor_id=args.floor_id,
            combat_id=args.combat_id, turn_id=args.turn_id, screenshot_path=screenshot_path,
            safety_margin_hp=args.safety_margin_hp, confidence=args.confidence,
        )
        print(json.dumps({"decision_id": decision_id, "screenshot": screenshot_path}))
    elif args.command == "resolve":
        database.resolve_decision(
            decision_id=args.decision_id, chosen_action=args.action, actual_outcome=args.outcome,
            status="skipped" if args.skipped else "resolved",
        )
        print(args.decision_id)
    elif args.command == "review":
        print(database.queue_review(
            run_id=args.run_id, trigger=args.trigger, priority=args.priority,
            evidence_ids=args.evidence_id, summary=args.summary, floor_id=args.floor_id,
        ))
    elif args.command == "builder-request":
        print(database.request_builder_change(
            requested_by=args.requested_by, title=args.title, rationale=args.rationale,
            scope=args.scope, dedupe_key=args.dedupe_key,
        ))
    elif args.command == "builder-update":
        database.update_builder_request(
            request_id=args.request_id, status=args.status, summary=args.summary,
            verification=args.verification,
        )
        print(args.request_id)
    elif args.command == "builder-requests":
        print(json.dumps(database.builder_requests(status=args.status), indent=2, sort_keys=True))
    elif args.command == "route-snapshot":
        nodes = args.legal_next_nodes.get("nodes")
        if not isinstance(nodes, list):
            parser.error("--legal-next-nodes must be an object with a nodes list")
        screenshot_path = _required_evidence_path(parser, args)
        print(database.record_route_snapshot(
            run_id=args.run_id, act=args.act, floor=args.floor, current_node_id=args.current_node,
            legal_next_nodes=nodes, resource_context=args.resources, floor_id=args.floor_id,
            screenshot_path=screenshot_path, source=args.source, confidence=args.confidence,
        ))
    elif args.command == "route-recommendation":
        print(database.record_route_recommendation(
            snapshot_id=args.snapshot_id, selected_node_id=args.selected_node,
            safety_rationale=args.safety_rationale, reward_tradeoff=args.reward_tradeoff,
        ))
    elif args.command == "route-latest":
        print(json.dumps(database.latest_route_snapshot(run_id=args.run_id), indent=2, sort_keys=True))
    elif args.command == "combat-state":
        print(database.record_event(
            run_id=args.run_id, floor_id=args.floor_id, kind="combat_state", phase=args.phase,
            state=args.state, payload=args.payload, screenshot_path=args.screenshot,
            source="veda", confidence=args.confidence, combat_id=args.combat_id, turn_id=args.turn_id,
        ))
    elif args.command == "combat-ledger":
        print(json.dumps(database.combat_ledger(run_id=args.run_id, floor_id=args.floor_id), indent=2, sort_keys=True))
    elif args.command == "combat-zones-start":
        print(database.start_combat_zones(
            combat_id=args.combat_id, deck=database._zone_cards(args.deck, field="cards"),
            hand=database._zone_cards(args.hand, field="cards"), source=args.source, confidence=args.confidence,
        ) or args.combat_id)
    elif args.command == "combat-zone-event":
        print(database.record_combat_zone_event(
            combat_id=args.combat_id, turn_id=args.turn_id, kind=args.kind, card_name=args.card,
            from_zone=args.from_zone, to_zone=args.to_zone, count=args.count, reason=args.reason,
            source=args.source, confidence=args.confidence,
        ))
    elif args.command == "combat-zone-state":
        print(json.dumps(database.combat_zone_state(combat_id=args.combat_id), indent=2, sort_keys=True))
    elif args.command == "inventory-event":
        print(database.record_inventory_event(
            run_id=args.run_id, floor_id=args.floor_id, item_kind=args.kind, action=args.action,
            item_name=args.item, related_item_name=args.related_item, property_text=args.property,
            screenshot_path=args.screenshot, source=args.source, confidence=args.confidence,
        ))
    elif args.command == "inventory-ledger":
        print(json.dumps(database.inventory_ledger(run_id=args.run_id), indent=2, sort_keys=True))
    elif args.command == "inventory-baseline":
        items = args.items.get("items")
        if not isinstance(items, list):
            parser.error("--items must be an object with an items list")
        screenshot_path = _required_evidence_path(parser, args)
        print(database.record_inventory_baseline(
            run_id=args.run_id, items=items, coverage=args.coverage, floor_id=args.floor_id,
            screenshot_path=screenshot_path, source=args.source, confidence=args.confidence,
        ))
    elif args.command == "map-snapshot":
        nodes, edges = args.nodes.get("nodes"), args.edges.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            parser.error("--nodes and --edges must contain nodes and edges lists")
        screenshot_path = _required_evidence_path(parser, args)
        print(database.record_map_snapshot(
            run_id=args.run_id, current_node_id=args.current_node, visible_nodes=nodes, visible_edges=edges,
            act=args.act, floor=args.floor, floor_id=args.floor_id, screenshot_path=screenshot_path,
            source=args.source, confidence=args.confidence,
        ))
    elif args.command == "map-latest":
        print(json.dumps(database.latest_map_snapshot(run_id=args.run_id), indent=2, sort_keys=True))
    elif args.command == "combat-start":
        print(database.start_combat(
            run_id=args.run_id, floor_id=args.floor_id, opening_state=args.state,
            encounter_name=args.encounter_name, encounter_type=args.encounter_type, summary=args.summary,
        ))
    elif args.command == "combat-finish":
        database.complete_combat(
            combat_id=args.combat_id, outcome=args.outcome, closing_state=args.state, summary=args.summary,
        )
        print(args.combat_id)
    elif args.command == "turn-start":
        print(database.start_combat_turn(
            combat_id=args.combat_id, turn_number=args.number, phase=args.phase,
            opening_state=args.state, summary=args.summary,
        ))
    elif args.command == "turn-finish":
        database.complete_combat_turn(turn_id=args.turn_id, closing_state=args.state, summary=args.summary)
        print(args.turn_id)
    elif args.command == "floor-retrospective":
        print(json.dumps(database.floor_retrospective(floor_id=args.floor_id), indent=2, sort_keys=True))
    elif args.command == "floor-completeness":
        print(json.dumps(database.floor_completeness(floor_id=args.floor_id), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
