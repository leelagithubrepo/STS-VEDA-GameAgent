#!/usr/bin/env python3
"""Operate VEDA's local SQLite memory without exposing it publicly."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
        parsed = json.loads(Path(value[1:]).read_text() if value.startswith("@") else value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def _database(path: Path) -> TelemetryDatabase:
    database = TelemetryDatabase(path)
    database.initialize()
    return database


@dataclass(frozen=True)
class EvidenceCapture:
    """Evidence provenance kept separate from the observed game state."""

    screenshot_path: str | None
    source: str
    confidence_cap: float
    metadata: dict[str, str]


def _evidence_for_logging(parser: argparse.ArgumentParser, args: argparse.Namespace) -> EvidenceCapture:
    """Return evidence details without allowing a capture failure to erase a turn.

    A supplied still remains the strongest evidence.  When macOS denies the
    passive capture process, the decision is still recorded as a written
    observation with deliberately lower confidence and an explicit reason.
    It is therefore auditable but never mistaken for screenshot-backed proof.
    """
    if args.capture and args.screenshot:
        parser.error("use either --capture or --screenshot, not both")
    if args.capture:
        try:
            return EvidenceCapture(
                screenshot_path=str(capture_visible_ps5_feed()),
                source="passive-screen-capture",
                confidence_cap=1.0,
                metadata={"evidence_mode": "passive_screen_capture"},
            )
        except RuntimeError as error:
            return EvidenceCapture(
                screenshot_path=None,
                source="written-observation-capture-unavailable",
                confidence_cap=0.6,
                metadata={
                    "evidence_mode": "written_fallback",
                    "capture_status": "unavailable",
                    "capture_error": str(error),
                },
            )
    if args.screenshot:
        return EvidenceCapture(
            screenshot_path=args.screenshot,
            source="supplied-screenshot",
            confidence_cap=1.0,
            metadata={"evidence_mode": "supplied_screenshot"},
        )
    parser.error("a meaningful decision or completed floor requires --capture or --screenshot")


def _evidence_confidence(value: float | None, evidence: EvidenceCapture) -> float:
    """Keep capture-unavailable entries visibly lower confidence."""
    return min(evidence.confidence_cap, 1.0 if value is None else value)


def _source_with_evidence(source: str, evidence: EvidenceCapture) -> str:
    return f"{source}; {evidence.source}"


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
    decision.add_argument("--high-stakes", action="store_true", help="allow one unresolved sequence only for this combat")
    decision.add_argument(
        "--requires-boss-preflight", action="store_true",
        help="require a confirmed boss inventory snapshot on this floor before advice",
    )

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

    boss_preflight = commands.add_parser(
        "boss-preflight", help="record the confirmed relic, potion, and key-card inventory before a boss",
    )
    boss_preflight.add_argument("--run-id", required=True)
    boss_preflight.add_argument("--floor-id", required=True)
    boss_preflight.add_argument("--boss")
    boss_preflight.add_argument("--relics", type=_document, required=True, help='JSON object with an "items" list')
    boss_preflight.add_argument("--potions", type=_document, required=True, help='JSON object with an "items" list')
    boss_preflight.add_argument("--key-cards", type=_document, required=True, help='JSON object with an "items" list')
    boss_preflight.add_argument("--unknowns", type=_document, default={"items": []}, help='JSON object with an "items" list')
    boss_preflight.add_argument("--screenshot")
    boss_preflight.add_argument("--capture", action="store_true", help="passively capture the visible inventory before logging")
    boss_preflight.add_argument("--source", default="visible boss-inventory screen")
    boss_preflight.add_argument("--confidence", type=float)

    run_card = commands.add_parser("run-card", help="show only current wins, health/resources, and next priority")
    run_card.add_argument("--run-id", required=True)
    run_card.add_argument("--next-priority")

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

    observe = commands.add_parser("combat-observe", help="save a fresh, explicitly verified advisory snapshot")
    for key in ("run-id", "floor-id", "combat-id", "turn-id", "source"):
        observe.add_argument("--" + key, required=True)
    observe.add_argument("--state", type=_document, required=True, help="JSON or @path; see docs/spire-advisory-example.json")
    observe.add_argument("--screenshot")
    observe.add_argument("--capture", action="store_true")
    context = commands.add_parser("combat-context", help="read fresh state, inventory and relevant reviewed rules together")
    context.add_argument("--combat-id", required=True)
    for name in ("advice-check", "advice-decide"):
        cmd = commands.add_parser(name, help="check a plan against the current advisory context")
        cmd.add_argument("--combat-id", required=True)
        cmd.add_argument("--plan", type=_document, required=True)
        if name == "advice-decide":
            cmd.add_argument("--snapshot-id", required=True)
            cmd.add_argument("--reasoning", required=True)
    potion = commands.add_parser("potion-use", help="atomically record confirmed consumption and its combat context")
    for key in ("run-id", "floor-id", "item", "source"):
        potion.add_argument("--" + key, required=True)
    for key in ("combat-id", "turn-id", "screenshot"):
        potion.add_argument("--" + key)
    potion.add_argument("--observed-effect", type=_document, required=True)
    campfire = commands.add_parser("campfire-advice", help="compare Rest/Smith with confirmed automatic healing")
    for key in ("run-id", "floor-id", "reasoning", "source"):
        campfire.add_argument("--" + key, required=True)
    campfire.add_argument("--choice", choices=("Rest", "Smith"), required=True)
    campfire.add_argument("--state", type=_document, required=True)
    campfire.add_argument("--screenshot")
    campfire.add_argument("--capture", action="store_true")

    args = parser.parse_args()
    database = _database(args.database)
    if args.command == "combat-observe":
        evidence = _evidence_for_logging(parser, args)
        print(database.record_advisory_snapshot(run_id=args.run_id, floor_id=args.floor_id,
            combat_id=args.combat_id, turn_id=args.turn_id, state=args.state,
            source=_source_with_evidence(args.source, evidence), screenshot_path=evidence.screenshot_path))
    elif args.command == "combat-context":
        print(json.dumps(database.advisory_context(combat_id=args.combat_id), indent=2))
    elif args.command == "advice-check":
        from veda.advisory import check_plan
        result = check_plan(database.advisory_context(combat_id=args.combat_id), args.plan)
        print(json.dumps(result, indent=2))
        return 0 if result['allowed'] else 2
    elif args.command == "advice-decide":
        print(json.dumps(database.record_checked_advice(combat_id=args.combat_id, snapshot_id=args.snapshot_id,
            plan=args.plan, reasoning=args.reasoning), indent=2))
    elif args.command == "potion-use":
        print(database.record_potion_use(run_id=args.run_id, floor_id=args.floor_id, combat_id=args.combat_id,
            turn_id=args.turn_id, item_name=args.item, source=args.source, observed_effect=args.observed_effect,
            screenshot_path=args.screenshot))
    elif args.command == "campfire-advice":
        evidence = _evidence_for_logging(parser, args)
        print(database.record_campfire_advice(run_id=args.run_id, floor_id=args.floor_id, state=args.state,
            choice=args.choice, reasoning=args.reasoning, source=_source_with_evidence(args.source, evidence),
            screenshot_path=evidence.screenshot_path))
    elif args.command == "status":
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
        evidence = _evidence_for_logging(parser, args)
        database.complete_floor(
            floor_id=args.floor_id, outcome=args.outcome, ending_state=args.state, summary=args.summary,
        )
        floor = database.floor_retrospective(floor_id=args.floor_id)["floor"]
        evidence_id = database.record_event(
            run_id=floor["run_id"], floor_id=args.floor_id, kind="floor_completion_evidence",
            phase="safe_boundary", state=args.state,
            payload={"outcome": args.outcome, "summary": args.summary, "evidence": evidence.metadata},
            screenshot_path=evidence.screenshot_path, source=evidence.source,
            confidence=_evidence_confidence(None, evidence),
        )
        print(json.dumps({"floor_id": args.floor_id, "evidence_id": evidence_id,
                          "screenshot": evidence.screenshot_path, "evidence": evidence.metadata}))
    elif args.command == "decision":
        if args.phase.lower() == "combat":
            parser.error("combat advice requires combat-observe, combat-context and advice-decide")
        if args.phase.lower() in ("rest_site", "campfire"):
            parser.error("Rest/Smith advice requires campfire-advice")
        evidence = _evidence_for_logging(parser, args)
        decision_id = database.record_decision(
            run_id=args.run_id, phase=args.phase, state=args.state, options=[args.options], recommendation=args.recommendation,
            reasoning=args.reasoning, prediction=args.prediction, floor_id=args.floor_id,
            combat_id=args.combat_id, turn_id=args.turn_id, screenshot_path=evidence.screenshot_path,
            safety_margin_hp=args.safety_margin_hp, confidence=_evidence_confidence(args.confidence, evidence),
            source=evidence.source, evidence_metadata=evidence.metadata,
            high_stakes=args.high_stakes, requires_boss_preflight=args.requires_boss_preflight,
        )
        print(json.dumps({"decision_id": decision_id, "screenshot": evidence.screenshot_path,
                          "evidence": evidence.metadata}))
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
        evidence = _evidence_for_logging(parser, args)
        print(database.record_route_snapshot(
            run_id=args.run_id, act=args.act, floor=args.floor, current_node_id=args.current_node,
            legal_next_nodes=nodes, resource_context=args.resources, floor_id=args.floor_id,
            screenshot_path=evidence.screenshot_path, source=_source_with_evidence(args.source, evidence),
            confidence=_evidence_confidence(args.confidence, evidence),
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
    elif args.command == "boss-preflight":
        categories = (args.relics, args.potions, args.key_cards, args.unknowns)
        if any(not isinstance(category.get("items"), list) for category in categories):
            parser.error("--relics, --potions, --key-cards, and --unknowns must contain items lists")
        evidence = _evidence_for_logging(parser, args)
        print(database.record_boss_inventory_preflight(
            run_id=args.run_id, floor_id=args.floor_id, boss_name=args.boss,
            relics=args.relics["items"], potions=args.potions["items"], key_cards=args.key_cards["items"],
            unknowns=args.unknowns["items"], screenshot_path=evidence.screenshot_path,
            source=_source_with_evidence(args.source, evidence),
            confidence=_evidence_confidence(args.confidence, evidence),
        ))
    elif args.command == "run-card":
        print(json.dumps(database.run_card(run_id=args.run_id, next_priority=args.next_priority), indent=2, sort_keys=True))
    elif args.command == "inventory-baseline":
        items = args.items.get("items")
        if not isinstance(items, list):
            parser.error("--items must be an object with an items list")
        evidence = _evidence_for_logging(parser, args)
        print(database.record_inventory_baseline(
            run_id=args.run_id, items=items, coverage=args.coverage, floor_id=args.floor_id,
            screenshot_path=evidence.screenshot_path, source=_source_with_evidence(args.source, evidence),
            confidence=_evidence_confidence(args.confidence, evidence),
        ))
    elif args.command == "map-snapshot":
        nodes, edges = args.nodes.get("nodes"), args.edges.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            parser.error("--nodes and --edges must contain nodes and edges lists")
        evidence = _evidence_for_logging(parser, args)
        print(database.record_map_snapshot(
            run_id=args.run_id, current_node_id=args.current_node, visible_nodes=nodes, visible_edges=edges,
            act=args.act, floor=args.floor, floor_id=args.floor_id, screenshot_path=evidence.screenshot_path,
            source=_source_with_evidence(args.source, evidence), confidence=_evidence_confidence(args.confidence, evidence),
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
