"""Private SQLite memory for auditable VEDA game-advisor telemetry.

The database records evidence and decisions; it never promotes an observation
into strategy by itself.  Public pages are generated from deliberately
sanitized summaries, while reviewer guidance remains only in this local DB.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


SCHEMA_VERSION = "veda.telemetry.sqlite.v5"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _load_json(value: str | None) -> Any:
    return json.loads(value) if value else {}


class TelemetryDatabase:
    """Small transactional API over VEDA's private, append-only evidence log."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create the schema without requiring any third-party package."""
        with self._connection() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    game TEXT NOT NULL,
                    character_name TEXT,
                    ascension INTEGER,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'abandoned')),
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS floors (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    act INTEGER,
                    floor INTEGER,
                    node_type TEXT,
                    outcome TEXT,
                    starting_state_json TEXT NOT NULL,
                    ending_state_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS floors_by_run_location ON floors(run_id, act, floor);
                CREATE TABLE IF NOT EXISTS combats (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT NOT NULL REFERENCES floors(id),
                    encounter_name TEXT,
                    encounter_type TEXT,
                    opening_state_json TEXT NOT NULL,
                    closing_state_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    outcome TEXT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS combats_by_floor ON combats(floor_id, opened_at);
                CREATE TABLE IF NOT EXISTS combat_turns (
                    id TEXT PRIMARY KEY,
                    combat_id TEXT NOT NULL REFERENCES combats(id),
                    turn_number INTEGER NOT NULL CHECK(turn_number > 0),
                    phase TEXT NOT NULL,
                    opening_state_json TEXT NOT NULL,
                    closing_state_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    UNIQUE(combat_id, turn_number)
                );
                CREATE INDEX IF NOT EXISTS combat_turns_by_combat ON combat_turns(combat_id, turn_number);
                CREATE TABLE IF NOT EXISTS combat_zone_bases (
                    combat_id TEXT PRIMARY KEY REFERENCES combats(id) ON DELETE CASCADE,
                    deck_json TEXT NOT NULL,
                    hand_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS combat_zone_events (
                    id TEXT PRIMARY KEY,
                    combat_id TEXT NOT NULL REFERENCES combats(id) ON DELETE CASCADE,
                    turn_id TEXT REFERENCES combat_turns(id),
                    kind TEXT NOT NULL CHECK(kind IN ('draw', 'play', 'discard', 'exhaust', 'return', 'generate', 'shuffle', 'unknown')),
                    card_name TEXT,
                    from_zone TEXT,
                    to_zone TEXT,
                    count INTEGER NOT NULL DEFAULT 1 CHECK(count > 0),
                    reason TEXT,
                    source TEXT NOT NULL,
                    confidence REAL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS combat_zone_events_by_combat ON combat_zone_events(combat_id, observed_at, id);
                CREATE TABLE IF NOT EXISTS inventory_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT REFERENCES floors(id),
                    item_kind TEXT NOT NULL CHECK(item_kind IN ('card', 'relic', 'potion')),
                    action TEXT NOT NULL CHECK(action IN ('acquired', 'removed', 'consumed', 'replaced', 'property_confirmed')),
                    item_name TEXT NOT NULL,
                    property_text TEXT,
                    related_item_name TEXT,
                    screenshot_path TEXT,
                    source TEXT NOT NULL,
                    confidence REAL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS inventory_events_by_run ON inventory_events(run_id, observed_at, id);
                CREATE TABLE IF NOT EXISTS inventory_baselines (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT REFERENCES floors(id),
                    coverage_json TEXT NOT NULL,
                    screenshot_path TEXT,
                    source TEXT NOT NULL,
                    confidence REAL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS inventory_baselines_by_run ON inventory_baselines(run_id, observed_at, id);
                CREATE TABLE IF NOT EXISTS inventory_baseline_items (
                    id TEXT PRIMARY KEY,
                    baseline_id TEXT NOT NULL REFERENCES inventory_baselines(id) ON DELETE CASCADE,
                    item_kind TEXT NOT NULL CHECK(item_kind IN ('card', 'relic', 'potion')),
                    item_name TEXT NOT NULL,
                    property_text TEXT
                );
                CREATE INDEX IF NOT EXISTS inventory_baseline_items_by_snapshot ON inventory_baseline_items(baseline_id, item_kind, id);
                CREATE TABLE IF NOT EXISTS map_snapshots (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT REFERENCES floors(id),
                    act INTEGER,
                    floor INTEGER,
                    current_node_id TEXT NOT NULL,
                    screenshot_path TEXT,
                    source TEXT NOT NULL,
                    confidence REAL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS map_nodes (
                    snapshot_id TEXT NOT NULL REFERENCES map_snapshots(id) ON DELETE CASCADE,
                    node_id TEXT NOT NULL,
                    node_kind TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    PRIMARY KEY(snapshot_id, node_id)
                );
                CREATE TABLE IF NOT EXISTS visible_map_edges (
                    snapshot_id TEXT NOT NULL REFERENCES map_snapshots(id) ON DELETE CASCADE,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, from_node_id, to_node_id)
                );
                CREATE INDEX IF NOT EXISTS map_snapshots_by_run ON map_snapshots(run_id, observed_at);
                CREATE TABLE IF NOT EXISTS evidence_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT REFERENCES floors(id),
                    kind TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    screenshot_path TEXT,
                    source TEXT NOT NULL,
                    confidence REAL
                );
                CREATE INDEX IF NOT EXISTS evidence_by_run_time ON evidence_events(run_id, observed_at);
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES evidence_events(id),
                    options_json TEXT NOT NULL,
                    recommendation_json TEXT NOT NULL,
                    reasoning TEXT NOT NULL,
                    prediction_json TEXT NOT NULL,
                    chosen_action_json TEXT,
                    actual_outcome_json TEXT,
                    status TEXT NOT NULL CHECK(status IN ('recommended', 'resolved', 'skipped')),
                    safety_margin_hp INTEGER,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS decisions_by_status ON decisions(status);
                CREATE TABLE IF NOT EXISTS review_queue (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT REFERENCES floors(id),
                    trigger TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK(priority IN ('routine', 'important', 'critical')),
                    evidence_ids_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'reviewed', 'deferred')),
                    reviewer_guidance TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    dedupe_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS review_queue_status ON review_queue(status, priority, created_at);
                CREATE TABLE IF NOT EXISTS builder_requests (
                    id TEXT PRIMARY KEY,
                    requested_by TEXT NOT NULL,
                    title TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('requested', 'accepted', 'implemented', 'verified', 'deferred')),
                    implementation_summary TEXT,
                    verification_summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    dedupe_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS builder_requests_status ON builder_requests(status, created_at);
                CREATE TABLE IF NOT EXISTS route_snapshots (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    floor_id TEXT REFERENCES floors(id),
                    act INTEGER,
                    floor INTEGER,
                    current_node_id TEXT NOT NULL,
                    resource_context_json TEXT NOT NULL,
                    screenshot_path TEXT,
                    source TEXT NOT NULL,
                    confidence REAL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS route_snapshots_by_run_time ON route_snapshots(run_id, observed_at);
                CREATE TABLE IF NOT EXISTS route_nodes (
                    snapshot_id TEXT NOT NULL REFERENCES route_snapshots(id) ON DELETE CASCADE,
                    node_id TEXT NOT NULL,
                    node_kind TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    PRIMARY KEY(snapshot_id, node_id)
                );
                CREATE TABLE IF NOT EXISTS legal_route_edges (
                    snapshot_id TEXT NOT NULL REFERENCES route_snapshots(id) ON DELETE CASCADE,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, from_node_id, to_node_id)
                );
                CREATE TABLE IF NOT EXISTS route_recommendations (
                    id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES route_snapshots(id) ON DELETE CASCADE,
                    selected_node_id TEXT NOT NULL,
                    safety_rationale TEXT NOT NULL,
                    reward_tradeoff TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS route_recommendations_by_snapshot ON route_recommendations(snapshot_id, created_at);
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO schema_metadata(key, value) VALUES (?, ?)",
                ("schema", SCHEMA_VERSION),
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(review_queue)")}
            if "dedupe_key" not in columns:
                db.execute("ALTER TABLE review_queue ADD COLUMN dedupe_key TEXT")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS review_queue_dedupe_key ON review_queue(dedupe_key)")
            evidence_columns = {row[1] for row in db.execute("PRAGMA table_info(evidence_events)")}
            if "combat_id" not in evidence_columns:
                db.execute("ALTER TABLE evidence_events ADD COLUMN combat_id TEXT REFERENCES combats(id)")
            if "turn_id" not in evidence_columns:
                db.execute("ALTER TABLE evidence_events ADD COLUMN turn_id TEXT REFERENCES combat_turns(id)")
            db.execute("CREATE INDEX IF NOT EXISTS evidence_by_combat_turn ON evidence_events(combat_id, turn_id, observed_at)")

    def start_or_resume_run(
        self,
        *,
        game: str = "Slay the Spire",
        character_name: str = "Ironclad",
        ascension: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.initialize()
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM runs WHERE game = ? AND character_name = ? AND ascension IS ? "
                "AND status = 'active' ORDER BY started_at DESC LIMIT 1",
                (game, character_name, ascension),
            ).fetchone()
            if row:
                return str(row["id"])
            run_id = str(uuid4())
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL, 'active', ?)",
                (run_id, game, character_name, ascension, _now(), _json(metadata)),
            )
            return run_id

    def start_new_run(
        self,
        *,
        game: str = "Slay the Spire",
        character_name: str = "Ironclad",
        ascension: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start an explicitly new run and archive matching active records.

        This operation is intentionally separate from ``start_or_resume_run``:
        a real fresh run must never silently inherit a prior active run.
        Existing telemetry is retained; only its status is changed to
        ``abandoned`` with a closing timestamp.
        """
        self.initialize()
        run_id = str(uuid4())
        now = _now()
        with self._connection() as db:
            db.execute(
                "UPDATE runs SET status = 'abandoned', ended_at = ? "
                "WHERE game = ? AND character_name = ? AND ascension IS ? AND status = 'active'",
                (now, game, character_name, ascension),
            )
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL, 'active', ?)",
                (run_id, game, character_name, ascension, now, _json(metadata)),
            )
        return run_id

    def record_floor(
        self,
        *,
        run_id: str,
        act: int | None,
        floor: int | None,
        node_type: str | None,
        outcome: str | None,
        starting_state: dict[str, Any] | None = None,
        ending_state: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> str:
        self.initialize()
        floor_id = str(uuid4())
        with self._connection() as db:
            db.execute(
                "INSERT INTO floors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (floor_id, run_id, act, floor, node_type, outcome, _json(starting_state), _json(ending_state), _json(summary), _now()),
            )
        return floor_id

    def complete_floor(
        self,
        *,
        floor_id: str,
        outcome: str,
        ending_state: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Close an existing floor record without replacing its entry evidence."""
        if not outcome.strip():
            raise ValueError("floor outcome is required")
        self.initialize()
        with self._connection() as db:
            result = db.execute(
                "UPDATE floors SET outcome = ?, ending_state_json = ?, summary_json = ? WHERE id = ?",
                (outcome.strip(), _json(ending_state), _json(summary), floor_id),
            )
            if result.rowcount != 1:
                raise ValueError("unknown floor")

    def start_combat(
        self,
        *,
        run_id: str,
        floor_id: str,
        opening_state: dict[str, Any],
        encounter_name: str | None = None,
        encounter_type: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> str:
        """Open a combat that is explicitly anchored to a recorded floor."""
        self.initialize()
        combat_id = str(uuid4())
        with self._connection() as db:
            floor = db.execute("SELECT run_id FROM floors WHERE id = ?", (floor_id,)).fetchone()
            if floor is None:
                raise ValueError("unknown floor")
            if floor["run_id"] != run_id:
                raise ValueError("combat floor belongs to a different run")
            db.execute(
                "INSERT INTO combats VALUES (?, ?, ?, ?, ?, ?, '{}', ?, NULL, ?, NULL)",
                (combat_id, run_id, floor_id, encounter_name, encounter_type, _json(opening_state), _json(summary), _now()),
            )
        return combat_id

    def complete_combat(
        self,
        *,
        combat_id: str,
        outcome: str,
        closing_state: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Close a combat after its observed result is known."""
        if not outcome.strip():
            raise ValueError("combat outcome is required")
        self.initialize()
        with self._connection() as db:
            result = db.execute(
                "UPDATE combats SET outcome = ?, closing_state_json = ?, summary_json = ?, closed_at = ? "
                "WHERE id = ? AND closed_at IS NULL",
                (outcome.strip(), _json(closing_state), _json(summary), _now(), combat_id),
            )
            if result.rowcount != 1:
                raise ValueError("unknown or already completed combat")

    def start_combat_turn(
        self,
        *,
        combat_id: str,
        turn_number: int,
        phase: str,
        opening_state: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> str:
        """Open a numbered turn inside an active recorded combat."""
        if turn_number < 1 or not phase.strip():
            raise ValueError("positive turn number and phase are required")
        self.initialize()
        turn_id = str(uuid4())
        with self._connection() as db:
            combat = db.execute("SELECT closed_at FROM combats WHERE id = ?", (combat_id,)).fetchone()
            if combat is None:
                raise ValueError("unknown combat")
            if combat["closed_at"] is not None:
                raise ValueError("cannot add a turn to a completed combat")
            try:
                db.execute(
                    "INSERT INTO combat_turns VALUES (?, ?, ?, ?, ?, '{}', ?, ?, NULL)",
                    (turn_id, combat_id, turn_number, phase.strip(), _json(opening_state), _json(summary), _now()),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("combat turn number is already recorded") from error
        return turn_id

    def complete_combat_turn(
        self,
        *,
        turn_id: str,
        closing_state: dict[str, Any],
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Close a turn; decisions remain separately resolved with player action."""
        self.initialize()
        with self._connection() as db:
            result = db.execute(
                "UPDATE combat_turns SET closing_state_json = ?, summary_json = ?, closed_at = ? "
                "WHERE id = ? AND closed_at IS NULL",
                (_json(closing_state), _json(summary), _now(), turn_id),
            )
            if result.rowcount != 1:
                raise ValueError("unknown or already completed combat turn")

    @staticmethod
    def _zone_cards(document: dict[str, Any], *, field: str) -> list[str]:
        cards = document.get(field)
        if not isinstance(cards, list) or any(not isinstance(card, str) or not card.strip() for card in cards):
            raise ValueError(f"{field} must be a non-empty-name cards list")
        return [card.strip() for card in cards]

    def start_combat_zones(
        self,
        *,
        combat_id: str,
        deck: list[str],
        hand: list[str],
        source: str,
        confidence: float | None = None,
    ) -> None:
        """Record the confirmed deck and opening hand for zone reconstruction."""
        if not source.strip():
            raise ValueError("zone source is required")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("zone confidence must be between 0 and 1")
        if not deck:
            raise ValueError("combat deck cannot be empty")
        deck_cards = [card.strip() for card in deck]
        hand_cards = [card.strip() for card in hand]
        if any(not card for card in deck_cards + hand_cards):
            raise ValueError("card names are required")
        if Counter(hand_cards) - Counter(deck_cards):
            raise ValueError("opening hand contains a card absent from the confirmed deck")
        self.initialize()
        with self._connection() as db:
            combat = db.execute("SELECT id FROM combats WHERE id = ?", (combat_id,)).fetchone()
            if combat is None:
                raise ValueError("unknown combat")
            try:
                db.execute(
                    "INSERT INTO combat_zone_bases VALUES (?, ?, ?, ?, ?, ?)",
                    (combat_id, _json(deck_cards), _json(hand_cards), source.strip(), confidence, _now()),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("combat zones are already initialized") from error

    def combat_zone_state(self, *, combat_id: str) -> dict[str, Any]:
        """Reconstruct zones from confirmed movements, never filling gaps by guesswork."""
        self.initialize()
        with self._connection() as db:
            base = db.execute("SELECT * FROM combat_zone_bases WHERE combat_id = ?", (combat_id,)).fetchone()
            if base is None:
                raise ValueError("combat zones have not been initialized")
            events = db.execute(
                "SELECT * FROM combat_zone_events WHERE combat_id = ? ORDER BY observed_at, id", (combat_id,)
            ).fetchall()
        deck = Counter(_load_json(base["deck_json"]))
        hand = Counter(_load_json(base["hand_json"]))
        draw = deck - hand
        zones: dict[str, Counter[str]] = {"hand": hand, "draw": draw, "discard": Counter(), "exhaust": Counter()}
        known = True
        unknown_reasons: list[str] = []
        for event in events:
            kind = event["kind"]
            if kind == "unknown":
                known = False
                unknown_reasons.append(event["reason"] or "an intervening card movement was unobserved")
                continue
            if kind == "shuffle":
                zones["draw"].update(zones["discard"])
                zones["discard"].clear()
                continue
            from_zone, to_zone, card_name, count = event["from_zone"], event["to_zone"], event["card_name"], event["count"]
            if from_zone is not None:
                if zones[from_zone][card_name] < count:
                    known = False
                    unknown_reasons.append(f"{kind} could not be replayed from confirmed {from_zone} state")
                    continue
                zones[from_zone][card_name] -= count
                if zones[from_zone][card_name] == 0:
                    del zones[from_zone][card_name]
            if to_zone is not None:
                zones[to_zone][card_name] += count
        return {
            "combat_id": combat_id,
            "known": known,
            "unknown_reasons": unknown_reasons,
            "zones": {zone: sorted(cards.elements()) for zone, cards in zones.items()},
            "eligible_discard_cards": sorted(zones["discard"].elements()) if known else [],
        }

    def record_combat_zone_event(
        self,
        *,
        combat_id: str,
        kind: str,
        source: str,
        turn_id: str | None = None,
        card_name: str | None = None,
        from_zone: str | None = None,
        to_zone: str | None = None,
        count: int = 1,
        reason: str | None = None,
        confidence: float | None = None,
    ) -> str:
        """Append one confirmed card movement or an explicit uncertainty marker."""
        zones = {"hand", "draw", "discard", "exhaust"}
        kinds = {"draw", "play", "discard", "exhaust", "return", "generate", "shuffle", "unknown"}
        if kind not in kinds or not source.strip() or count < 1:
            raise ValueError("valid zone kind, source, and positive count are required")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("zone confidence must be between 0 and 1")
        if kind in {"shuffle", "unknown"}:
            if any(value is not None for value in (card_name, from_zone, to_zone)):
                raise ValueError("shuffle and unknown events cannot claim a card movement")
        else:
            if not card_name or (from_zone is None and to_zone is None):
                raise ValueError("card movements require a card and at least one zone")
            if from_zone is not None and from_zone not in zones:
                raise ValueError("invalid source zone")
            if to_zone is not None and to_zone not in zones:
                raise ValueError("invalid destination zone")
        self.initialize()
        event_id = str(uuid4())
        with self._connection() as db:
            base = db.execute("SELECT 1 FROM combat_zone_bases WHERE combat_id = ?", (combat_id,)).fetchone()
            if base is None:
                raise ValueError("combat zones have not been initialized")
            if turn_id is not None:
                turn = db.execute("SELECT combat_id FROM combat_turns WHERE id = ?", (turn_id,)).fetchone()
                if turn is None or turn["combat_id"] != combat_id:
                    raise ValueError("zone event turn must belong to its combat")
            db.execute(
                "INSERT INTO combat_zone_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, combat_id, turn_id, kind, card_name.strip() if card_name else None, from_zone, to_zone,
                 count, reason.strip() if reason else None, source.strip(), confidence, _now()),
            )
        return event_id

    @staticmethod
    def _validate_event_context(
        db: sqlite3.Connection,
        *,
        run_id: str,
        floor_id: str | None,
        combat_id: str | None,
        turn_id: str | None,
    ) -> None:
        """Reject cross-run or cross-combat links instead of silently guessing."""
        if floor_id is not None:
            floor = db.execute("SELECT run_id FROM floors WHERE id = ?", (floor_id,)).fetchone()
            if floor is None:
                raise ValueError("unknown floor")
            if floor["run_id"] != run_id:
                raise ValueError("floor belongs to a different run")
        if combat_id is not None:
            combat = db.execute("SELECT run_id, floor_id FROM combats WHERE id = ?", (combat_id,)).fetchone()
            if combat is None:
                raise ValueError("unknown combat")
            if combat["run_id"] != run_id:
                raise ValueError("combat belongs to a different run")
            if floor_id != combat["floor_id"]:
                raise ValueError("combat must be linked to its recorded floor")
        if turn_id is not None:
            turn = db.execute("SELECT combat_id FROM combat_turns WHERE id = ?", (turn_id,)).fetchone()
            if turn is None:
                raise ValueError("unknown combat turn")
            if turn["combat_id"] != combat_id:
                raise ValueError("turn must be linked to its recorded combat")

    def record_inventory_event(
        self,
        *,
        run_id: str,
        item_kind: str,
        action: str,
        item_name: str,
        source: str,
        floor_id: str | None = None,
        property_text: str | None = None,
        related_item_name: str | None = None,
        screenshot_path: str | None = None,
        confidence: float | None = None,
    ) -> str:
        """Record a confirmed inventory fact and retain its acquisition history."""
        kinds = {"card", "relic", "potion"}
        actions = {"acquired", "removed", "consumed", "replaced", "property_confirmed"}
        if item_kind not in kinds or action not in actions or not item_name.strip() or not source.strip():
            raise ValueError("valid inventory kind, action, item name, and source are required")
        if action == "property_confirmed" and not property_text:
            raise ValueError("property confirmation requires property text")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("inventory confidence must be between 0 and 1")
        self.initialize()
        event_id = str(uuid4())
        with self._connection() as db:
            self._validate_event_context(db, run_id=run_id, floor_id=floor_id, combat_id=None, turn_id=None)
            db.execute(
                "INSERT INTO inventory_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, run_id, floor_id, item_kind, action, item_name.strip(), property_text.strip() if property_text else None,
                 related_item_name.strip() if related_item_name else None, screenshot_path, source.strip(), confidence, _now()),
            )
        return event_id

    def record_inventory_baseline(
        self,
        *,
        run_id: str,
        items: list[dict[str, Any]],
        coverage: dict[str, str],
        source: str,
        floor_id: str | None = None,
        screenshot_path: str | None = None,
        confidence: float | None = None,
    ) -> str:
        """Record the inventory observed at a late-start capture boundary.

        A baseline says only "these items were present when observed".  It is
        intentionally not an acquisition event and therefore cannot rewrite
        when an item was earned on an earlier, unlogged floor.
        """
        kinds = {"card", "relic", "potion"}
        expected_coverage = {"card", "relic", "potion"}
        levels = {"complete", "partial", "unknown"}
        if not items or not source.strip():
            raise ValueError("baseline items and source are required")
        if set(coverage) != expected_coverage or any(level not in levels for level in coverage.values()):
            raise ValueError("baseline coverage must describe card, relic, and potion as complete, partial, or unknown")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("baseline confidence must be between 0 and 1")

        normalized: list[tuple[str, str, str | None]] = []
        seen_relics: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each baseline item must be an object")
            item_kind, item_name = item.get("kind"), item.get("item")
            property_text = item.get("property")
            if item_kind not in kinds or not isinstance(item_name, str) or not item_name.strip():
                raise ValueError("baseline items require a valid kind and item name")
            if property_text is not None and (not isinstance(property_text, str) or not property_text.strip()):
                raise ValueError("baseline item property must be non-empty text when provided")
            name = item_name.strip()
            if item_kind == "relic":
                key = name.casefold()
                if key in seen_relics:
                    raise ValueError("a baseline cannot list the same relic twice")
                seen_relics.add(key)
            normalized.append((item_kind, name, property_text.strip() if property_text else None))

        self.initialize()
        baseline_id = str(uuid4())
        now = _now()
        with self._connection() as db:
            self._validate_event_context(db, run_id=run_id, floor_id=floor_id, combat_id=None, turn_id=None)
            db.execute(
                "INSERT INTO inventory_baselines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (baseline_id, run_id, floor_id, _json(coverage), screenshot_path, source.strip(), confidence, now),
            )
            db.executemany(
                "INSERT INTO inventory_baseline_items VALUES (?, ?, ?, ?, ?)",
                [(str(uuid4()), baseline_id, item_kind, item_name, property_text) for item_kind, item_name, property_text in normalized],
            )
        return baseline_id

    def inventory_ledger(self, *, run_id: str) -> dict[str, Any]:
        """Return history plus current confirmed inventory, preserving uncertainty."""
        self.initialize()
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM inventory_events WHERE run_id = ? ORDER BY observed_at, id", (run_id,)
            ).fetchall()
            baselines = db.execute(
                "SELECT * FROM inventory_baselines WHERE run_id = ? ORDER BY observed_at, id", (run_id,)
            ).fetchall()
            latest_baseline = baselines[-1] if baselines else None
            baseline_items = [] if latest_baseline is None else db.execute(
                "SELECT * FROM inventory_baseline_items WHERE baseline_id = ? ORDER BY item_kind, id",
                (latest_baseline["id"],),
            ).fetchall()
            baseline_history = []
            for baseline in baselines:
                snapshot_items = db.execute(
                    "SELECT item_kind, item_name, property_text FROM inventory_baseline_items "
                    "WHERE baseline_id = ? ORDER BY item_kind, id", (baseline["id"],),
                ).fetchall()
                baseline_history.append({**dict(baseline), "items": [dict(item) for item in snapshot_items]})

        current_records: dict[str, list[dict[str, Any]]] = {"card": [], "relic": [], "potion": []}
        coverage = {"card": "unknown", "relic": "unknown", "potion": "unknown"}
        if latest_baseline is not None:
            coverage = _load_json(latest_baseline["coverage_json"])
            for item in baseline_items:
                current_records[item["item_kind"]].append({
                    "item_name": item["item_name"],
                    "provenance": "baseline_snapshot",
                    "baseline_id": latest_baseline["id"],
                    "floor_id": latest_baseline["floor_id"],
                    "source": latest_baseline["source"],
                    "confidence": latest_baseline["confidence"],
                })
        properties: dict[tuple[str, str], dict[str, Any]] = {}
        history: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            history.append(event)
            key = (row["item_kind"], row["item_name"].casefold())
            if row["action"] == "property_confirmed":
                properties[key] = {"property": row["property_text"], "source": row["source"], "confidence": row["confidence"], "event_id": row["id"]}
                continue
            if latest_baseline is not None and row["observed_at"] <= latest_baseline["observed_at"]:
                continue
            items = current_records[row["item_kind"]]
            if row["action"] == "acquired":
                if row["item_kind"] == "relic" and any(item["item_name"].casefold() == row["item_name"].casefold() for item in items):
                    continue
                items.append({
                    "item_name": row["item_name"], "provenance": "inventory_event", "event_id": row["id"],
                    "floor_id": row["floor_id"], "source": row["source"], "confidence": row["confidence"],
                })
            elif row["action"] in {"removed", "consumed"}:
                match = next((index for index, item in enumerate(items) if item["item_name"] == row["item_name"]), None)
                if match is not None:
                    items.pop(match)
            elif row["action"] == "replaced":
                match = next((index for index, item in enumerate(items) if item["item_name"] == row["item_name"]), None)
                if match is not None:
                    items.pop(match)
                if row["related_item_name"]:
                    items.append({
                        "item_name": row["related_item_name"], "provenance": "inventory_event", "event_id": row["id"],
                        "floor_id": row["floor_id"], "source": row["source"], "confidence": row["confidence"],
                    })

        for item in baseline_items:
            if item["property_text"]:
                key = (item["item_kind"], item["item_name"].casefold())
                properties[key] = {
                    "property": item["property_text"], "source": latest_baseline["source"],
                    "confidence": latest_baseline["confidence"], "baseline_id": latest_baseline["id"],
                }
        return {
            "run_id": run_id,
            "current": {kind: [item["item_name"] for item in items] for kind, items in current_records.items()},
            "current_items": current_records,
            "coverage": coverage,
            "latest_baseline": None if latest_baseline is None else baseline_history[-1],
            "baseline_history": baseline_history,
            "properties": {f"{kind}:{name}": value for (kind, name), value in properties.items()},
            "history": history,
        }

    def record_event(
        self,
        *,
        run_id: str,
        kind: str,
        phase: str,
        state: dict[str, Any],
        payload: dict[str, Any] | None = None,
        floor_id: str | None = None,
        combat_id: str | None = None,
        turn_id: str | None = None,
        screenshot_path: str | None = None,
        source: str = "veda",
        confidence: float | None = None,
    ) -> str:
        if not kind.strip() or not phase.strip() or not source.strip():
            raise ValueError("event kind, phase, and source are required")
        self.initialize()
        event_id = str(uuid4())
        with self._connection() as db:
            self._validate_event_context(
                db, run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id,
            )
            db.execute(
                "INSERT INTO evidence_events (id, run_id, floor_id, kind, phase, observed_at, state_json, payload_json, screenshot_path, source, confidence, combat_id, turn_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, run_id, floor_id, kind.strip(), phase.strip(), _now(), _json(state), _json(payload), screenshot_path, source.strip(), confidence, combat_id, turn_id),
            )
        return event_id

    def combat_ledger(self, *, run_id: str, floor_id: str | None = None) -> list[dict[str, Any]]:
        """Return recorded combat snapshots in observation order for a run.

        This is deliberately a read-only view over evidence events: it never
        turns an observation into a strategic conclusion.
        """
        query = (
            "SELECT id, floor_id, combat_id, turn_id, phase, observed_at, state_json, payload_json, "
            "screenshot_path, source, confidence "
            "FROM evidence_events WHERE run_id = ? AND kind = 'combat_state'"
        )
        values: list[Any] = [run_id]
        if floor_id is not None:
            query += " AND floor_id = ?"
            values.append(floor_id)
        query += " ORDER BY observed_at, id"
        with self._connection() as db:
            rows = db.execute(query, values).fetchall()
        return [
            {
                "id": row["id"],
                "floor_id": row["floor_id"],
                "combat_id": row["combat_id"],
                "turn_id": row["turn_id"],
                "phase": row["phase"],
                "observed_at": row["observed_at"],
                "state": json.loads(row["state_json"]),
                "payload": json.loads(row["payload_json"]),
                "screenshot_path": row["screenshot_path"],
                "source": row["source"],
                "confidence": row["confidence"],
            }
            for row in rows
        ]

    def record_decision(
        self,
        *,
        run_id: str,
        phase: str,
        state: dict[str, Any],
        options: list[dict[str, Any]],
        recommendation: dict[str, Any],
        reasoning: str,
        prediction: dict[str, Any],
        floor_id: str | None = None,
        combat_id: str | None = None,
        turn_id: str | None = None,
        screenshot_path: str | None = None,
        safety_margin_hp: int | None = None,
        confidence: float | None = None,
    ) -> str:
        """Persist a recommendation before the player acts, for later scoring."""
        if not reasoning.strip():
            raise ValueError("decision reasoning is required")
        if floor_id is None:
            raise ValueError("new decisions must be linked to a recorded floor")
        if phase.strip().lower() == "combat" and (combat_id is None or turn_id is None):
            raise ValueError("combat decisions must be linked to a recorded combat and turn")
        event_id = self.record_event(
            run_id=run_id, floor_id=floor_id, kind="meaningful_decision", phase=phase,
            state=state, payload={"recommendation": recommendation}, screenshot_path=screenshot_path,
            source="veda", confidence=confidence, combat_id=combat_id, turn_id=turn_id,
        )
        decision_id = str(uuid4())
        with self._connection() as db:
            db.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'recommended', ?, NULL)",
                (decision_id, event_id, _json(options), _json(recommendation), reasoning.strip(), _json(prediction), safety_margin_hp),
            )
        return decision_id

    def floor_retrospective(self, *, floor_id: str) -> dict[str, Any]:
        """Return only evidence actually linked to one floor for on-demand review.

        Unlinked historical rows are deliberately absent.  This view is an
        audit record, not a best-effort reconstruction of past play.
        """
        self.initialize()
        with self._connection() as db:
            floor = db.execute("SELECT * FROM floors WHERE id = ?", (floor_id,)).fetchone()
            if floor is None:
                raise ValueError("unknown floor")
            combats = db.execute(
                "SELECT * FROM combats WHERE floor_id = ? ORDER BY opened_at, id", (floor_id,)
            ).fetchall()
            events = db.execute(
                "SELECT * FROM evidence_events WHERE floor_id = ? ORDER BY observed_at, id", (floor_id,)
            ).fetchall()
            decisions = db.execute(
                "SELECT d.*, e.floor_id, e.combat_id, e.turn_id, e.observed_at, e.screenshot_path "
                "FROM decisions d JOIN evidence_events e ON e.id = d.event_id "
                "WHERE e.floor_id = ? ORDER BY e.observed_at, d.id",
                (floor_id,),
            ).fetchall()
            turns = db.execute(
                "SELECT t.* FROM combat_turns t JOIN combats c ON c.id = t.combat_id "
                "WHERE c.floor_id = ? ORDER BY c.opened_at, t.turn_number, t.id",
                (floor_id,),
            ).fetchall()

        def event_view(row: sqlite3.Row) -> dict[str, Any]:
            return {
                "id": row["id"], "kind": row["kind"], "phase": row["phase"],
                "combat_id": row["combat_id"], "turn_id": row["turn_id"],
                "observed_at": row["observed_at"], "screenshot_path": row["screenshot_path"],
                "source": row["source"], "confidence": row["confidence"],
            }

        return {
            "floor": {
                "id": floor["id"], "run_id": floor["run_id"], "act": floor["act"],
                "floor": floor["floor"], "node_type": floor["node_type"], "outcome": floor["outcome"],
                "starting_state": _load_json(floor["starting_state_json"]),
                "ending_state": _load_json(floor["ending_state_json"]),
                "summary": _load_json(floor["summary_json"]),
            },
            "evidence_record_ids": [row["id"] for row in events],
            "evidence": [event_view(row) for row in events],
            "decision_record_ids": [row["id"] for row in decisions],
            "decisions": [
                {
                    "id": row["id"], "event_id": row["event_id"], "combat_id": row["combat_id"],
                    "turn_id": row["turn_id"], "status": row["status"],
                    "reasoning": row["reasoning"], "recommendation": _load_json(row["recommendation_json"]),
                    "prediction": _load_json(row["prediction_json"]),
                    "chosen_action": _load_json(row["chosen_action_json"]),
                    "actual_outcome": _load_json(row["actual_outcome_json"]),
                    "screenshot_path": row["screenshot_path"],
                }
                for row in decisions
            ],
            "combats": [
                {
                    "id": row["id"], "encounter_name": row["encounter_name"],
                    "encounter_type": row["encounter_type"], "outcome": row["outcome"],
                    "opening_state": _load_json(row["opening_state_json"]),
                    "closing_state": _load_json(row["closing_state_json"]),
                    "summary": _load_json(row["summary_json"]),
                }
                for row in combats
            ],
            "turns": [
                {
                    "id": row["id"], "combat_id": row["combat_id"], "turn_number": row["turn_number"],
                    "phase": row["phase"], "opening_state": _load_json(row["opening_state_json"]),
                    "closing_state": _load_json(row["closing_state_json"]), "summary": _load_json(row["summary_json"]),
                }
                for row in turns
            ],
        }

    def floor_completeness(self, *, floor_id: str) -> dict[str, Any]:
        """Report whether a completed floor is reviewable without guessing.

        This is deliberately a gate report, not a repair tool.  It identifies
        absent evidence and open records so Spire can collect them at a safe
        boundary rather than inventing a retrospective after the fact.
        """
        self.initialize()
        with self._connection() as db:
            floor = db.execute("SELECT id, run_id, outcome FROM floors WHERE id = ?", (floor_id,)).fetchone()
            if floor is None:
                raise ValueError("unknown floor")
            screenshot_count = db.execute(
                "SELECT COUNT(*) FROM evidence_events WHERE floor_id = ? AND screenshot_path IS NOT NULL", (floor_id,)
            ).fetchone()[0]
            decision_counts = db.execute(
                "SELECT status, COUNT(*) AS count FROM decisions d JOIN evidence_events e ON e.id = d.event_id "
                "WHERE e.floor_id = ? GROUP BY status", (floor_id,)
            ).fetchall()
            combats = db.execute("SELECT id, closed_at FROM combats WHERE floor_id = ?", (floor_id,)).fetchall()
            turn_counts = db.execute(
                "SELECT COUNT(*) FROM combat_turns t JOIN combats c ON c.id = t.combat_id "
                "WHERE c.floor_id = ? AND t.closed_at IS NULL", (floor_id,)
            ).fetchone()[0]
            zone_base_count = 0 if not combats else db.execute(
                "SELECT COUNT(*) FROM combat_zone_bases WHERE combat_id IN "
                f"({','.join('?' for _ in combats)})", [combat["id"] for combat in combats],
            ).fetchone()[0]

        decisions = {row["status"]: row["count"] for row in decision_counts}
        unresolved = decisions.get("recommended", 0)
        open_combats = sum(combat["closed_at"] is None for combat in combats)
        missing: list[str] = []
        if screenshot_count == 0:
            missing.append("at least one linked screenshot")
        if unresolved:
            missing.append(f"{unresolved} unresolved decision(s)")
        if open_combats:
            missing.append(f"{open_combats} open combat record(s)")
        if turn_counts:
            missing.append(f"{turn_counts} open combat turn(s)")
        if combats and zone_base_count < len(combats):
            missing.append(f"combat-zone base missing for {len(combats) - zone_base_count} combat(s)")
        if floor["outcome"] is None:
            missing.append("floor outcome")
        return {
            "floor_id": floor_id,
            "run_id": floor["run_id"],
            "review_ready": not missing,
            "linked_screenshots": screenshot_count,
            "decisions": {"resolved": decisions.get("resolved", 0), "skipped": decisions.get("skipped", 0), "unresolved": unresolved},
            "combats": {"total": len(combats), "open": open_combats, "open_turns": turn_counts, "with_zone_base": zone_base_count},
            "missing": missing,
        }

    def resolve_decision(
        self, *, decision_id: str, chosen_action: dict[str, Any], actual_outcome: dict[str, Any], status: str = "resolved"
    ) -> None:
        if status not in {"resolved", "skipped"}:
            raise ValueError("decision status must be resolved or skipped")
        self.initialize()
        with self._connection() as db:
            result = db.execute(
                "UPDATE decisions SET chosen_action_json = ?, actual_outcome_json = ?, status = ?, resolved_at = ? WHERE id = ?",
                (_json(chosen_action), _json(actual_outcome), status, _now(), decision_id),
            )
            if result.rowcount != 1:
                raise ValueError("unknown decision")

    def queue_review(
        self,
        *,
        run_id: str,
        trigger: str,
        priority: str,
        evidence_ids: list[str],
        summary: dict[str, Any],
        floor_id: str | None = None,
        dedupe_key: str | None = None,
    ) -> str:
        """Queue a safe-boundary LLM review; this never interrupts combat."""
        if priority not in {"routine", "important", "critical"}:
            raise ValueError("invalid review priority")
        self.initialize()
        with self._connection() as db:
            if dedupe_key:
                existing = db.execute("SELECT id FROM review_queue WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
                if existing:
                    return str(existing["id"])
            review_id = str(uuid4())
            db.execute(
                "INSERT INTO review_queue VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL, ?)",
                (review_id, run_id, floor_id, trigger, priority, _json(evidence_ids), _json(summary), _now(), dedupe_key),
            )
            return review_id

    def record_reviewer_guidance(self, *, review_id: str, guidance: str) -> None:
        if not guidance.strip():
            raise ValueError("review guidance is required")
        self.initialize()
        with self._connection() as db:
            result = db.execute(
                "UPDATE review_queue SET status = 'reviewed', reviewer_guidance = ?, reviewed_at = ? WHERE id = ? AND status = 'pending'",
                (guidance.strip(), _now(), review_id),
            )
            if result.rowcount != 1:
                raise ValueError("review must be pending")

    def pending_reviews(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM review_queue WHERE status = 'pending' "
                "ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'important' THEN 1 ELSE 2 END, created_at"
            ).fetchall()
        return [{**dict(row), "evidence_ids": _load_json(row["evidence_ids_json"]), "summary": _load_json(row["summary_json"])} for row in rows]

    def request_builder_change(
        self,
        *,
        requested_by: str,
        title: str,
        rationale: str,
        scope: dict[str, Any],
        dedupe_key: str | None = None,
    ) -> str:
        """Record a durable product request without changing product code.

        Game advisors use this boundary for shared-system needs discovered
        during play.  A Builder must later accept, implement, and verify it.
        """
        if not requested_by.strip() or not title.strip() or not rationale.strip():
            raise ValueError("requested_by, title, and rationale are required")
        self.initialize()
        with self._connection() as db:
            if dedupe_key:
                existing = db.execute("SELECT id FROM builder_requests WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
                if existing:
                    return str(existing["id"])
            request_id = str(uuid4())
            now = _now()
            db.execute(
                "INSERT INTO builder_requests VALUES (?, ?, ?, ?, ?, 'requested', NULL, NULL, ?, ?, ?)",
                (request_id, requested_by.strip(), title.strip(), rationale.strip(), _json(scope), now, now, dedupe_key),
            )
        return request_id

    def update_builder_request(
        self,
        *,
        request_id: str,
        status: str,
        summary: str,
        verification: bool = False,
    ) -> None:
        """Move a Builder request through an explicit, auditable lifecycle."""
        if status not in {"accepted", "implemented", "verified", "deferred"}:
            raise ValueError("invalid builder request status")
        if not summary.strip():
            raise ValueError("builder request summary is required")
        self.initialize()
        summary_column = "verification_summary" if verification else "implementation_summary"
        with self._connection() as db:
            result = db.execute(
                f"UPDATE builder_requests SET status = ?, {summary_column} = ?, updated_at = ? WHERE id = ?",
                (status, summary.strip(), _now(), request_id),
            )
            if result.rowcount != 1:
                raise ValueError("unknown builder request")

    def builder_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """Read Builder work without exposing private gameplay evidence publicly."""
        self.initialize()
        query = "SELECT * FROM builder_requests"
        values: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY created_at, id"
        with self._connection() as db:
            rows = db.execute(query, values).fetchall()
        return [{**dict(row), "scope": _load_json(row["scope_json"])} for row in rows]

    def record_route_snapshot(
        self,
        *,
        run_id: str,
        act: int | None,
        floor: int | None,
        current_node_id: str,
        legal_next_nodes: list[dict[str, Any]],
        resource_context: dict[str, Any],
        floor_id: str | None = None,
        screenshot_path: str | None = None,
        source: str = "veda",
        confidence: float | None = None,
    ) -> str:
        """Persist only the currently visible legal map connections.

        This intentionally accepts a flat set of legal next nodes rather than
        a guessed multi-floor route.  The resulting edges are always from the
        confirmed current node to those exact nodes.
        """
        if not current_node_id.strip() or not source.strip():
            raise ValueError("current node and source are required")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("route confidence must be between 0 and 1")
        parsed_nodes: list[tuple[str, str, float]] = []
        seen_nodes: set[str] = set()
        for node in legal_next_nodes:
            node_id = str(node.get("node_id", "")).strip()
            node_kind = str(node.get("kind", "")).strip()
            node_confidence = node.get("confidence")
            if not node_id or not node_kind or not isinstance(node_confidence, (int, float)):
                raise ValueError("each legal node needs node_id, kind, and numeric confidence")
            if not 0 <= float(node_confidence) <= 1:
                raise ValueError("legal-node confidence must be between 0 and 1")
            if node_id in seen_nodes:
                raise ValueError("legal node IDs must be unique within a snapshot")
            seen_nodes.add(node_id)
            parsed_nodes.append((node_id, node_kind, float(node_confidence)))
        if not parsed_nodes:
            raise ValueError("at least one legal next node is required")
        self.initialize()
        snapshot_id = str(uuid4())
        observed_at = _now()
        with self._connection() as db:
            db.execute(
                "INSERT INTO route_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot_id, run_id, floor_id, act, floor, current_node_id.strip(), _json(resource_context), screenshot_path,
                 source.strip(), confidence, observed_at),
            )
            db.executemany(
                "INSERT INTO route_nodes VALUES (?, ?, ?, ?)",
                [(snapshot_id, node_id, node_kind, node_confidence) for node_id, node_kind, node_confidence in parsed_nodes],
            )
            db.executemany(
                "INSERT INTO legal_route_edges VALUES (?, ?, ?)",
                [(snapshot_id, current_node_id.strip(), node_id) for node_id, _, _ in parsed_nodes],
            )
        return snapshot_id

    def record_route_recommendation(
        self,
        *,
        snapshot_id: str,
        selected_node_id: str,
        safety_rationale: str,
        reward_tradeoff: str,
    ) -> str:
        """Attach an auditable decision only when its node was legally visible."""
        if not selected_node_id.strip() or not safety_rationale.strip() or not reward_tradeoff.strip():
            raise ValueError("selected node, safety rationale, and reward tradeoff are required")
        self.initialize()
        with self._connection() as db:
            legal = db.execute(
                "SELECT 1 FROM route_nodes WHERE snapshot_id = ? AND node_id = ?",
                (snapshot_id, selected_node_id.strip()),
            ).fetchone()
            if legal is None:
                raise ValueError("selected node is not a recorded legal next node")
            recommendation_id = str(uuid4())
            db.execute(
                "INSERT INTO route_recommendations VALUES (?, ?, ?, ?, ?, ?)",
                (recommendation_id, snapshot_id, selected_node_id.strip(), safety_rationale.strip(), reward_tradeoff.strip(), _now()),
            )
        return recommendation_id

    def latest_route_snapshot(self, *, run_id: str) -> dict[str, Any] | None:
        """Return the latest evidence-backed map state, never an inferred route."""
        self.initialize()
        with self._connection() as db:
            snapshot = db.execute(
                "SELECT * FROM route_snapshots WHERE run_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if snapshot is None:
                return None
            nodes = db.execute(
                "SELECT node_id, node_kind, confidence FROM route_nodes WHERE snapshot_id = ? ORDER BY node_id",
                (snapshot["id"],),
            ).fetchall()
            edges = db.execute(
                "SELECT from_node_id, to_node_id FROM legal_route_edges WHERE snapshot_id = ? ORDER BY to_node_id",
                (snapshot["id"],),
            ).fetchall()
            recommendation = db.execute(
                "SELECT selected_node_id, safety_rationale, reward_tradeoff, created_at "
                "FROM route_recommendations WHERE snapshot_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (snapshot["id"],),
            ).fetchone()
        return {
            **dict(snapshot),
            "resource_context": _load_json(snapshot["resource_context_json"]),
            "legal_next_nodes": [dict(node) for node in nodes],
            "legal_next_edges": [dict(edge) for edge in edges],
            "recommendation": dict(recommendation) if recommendation else None,
        }

    def record_map_snapshot(
        self,
        *,
        run_id: str,
        current_node_id: str,
        visible_nodes: list[dict[str, Any]],
        visible_edges: list[dict[str, str]],
        source: str,
        act: int | None = None,
        floor: int | None = None,
        floor_id: str | None = None,
        screenshot_path: str | None = None,
        confidence: float | None = None,
    ) -> str:
        """Save the whole graph visible in one map view, never invented edges."""
        if not current_node_id.strip() or not source.strip():
            raise ValueError("current node and source are required")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("map confidence must be between 0 and 1")
        nodes: list[tuple[str, str, float]] = []
        seen: set[str] = set()
        for node in visible_nodes:
            node_id = str(node.get("node_id", "")).strip()
            kind = str(node.get("kind", "")).strip()
            node_confidence = node.get("confidence")
            if not node_id or not kind or not isinstance(node_confidence, (int, float)) or not 0 <= float(node_confidence) <= 1:
                raise ValueError("each visible node needs id, kind, and confidence between 0 and 1")
            if node_id in seen:
                raise ValueError("visible node IDs must be unique")
            seen.add(node_id)
            nodes.append((node_id, kind, float(node_confidence)))
        if current_node_id not in seen:
            raise ValueError("current node must be included in the visible graph")
        edges: list[tuple[str, str]] = []
        for edge in visible_edges:
            from_node = str(edge.get("from_node_id", "")).strip()
            to_node = str(edge.get("to_node_id", "")).strip()
            if not from_node or not to_node or from_node not in seen or to_node not in seen:
                raise ValueError("every visible edge must connect two visible nodes")
            if from_node == to_node:
                raise ValueError("visible map edges cannot loop to the same node")
            edges.append((from_node, to_node))
        if len(set(edges)) != len(edges):
            raise ValueError("visible map edges must be unique")
        self.initialize()
        snapshot_id = str(uuid4())
        with self._connection() as db:
            self._validate_event_context(db, run_id=run_id, floor_id=floor_id, combat_id=None, turn_id=None)
            db.execute(
                "INSERT INTO map_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot_id, run_id, floor_id, act, floor, current_node_id.strip(), screenshot_path,
                 source.strip(), confidence, _now()),
            )
            db.executemany("INSERT INTO map_nodes VALUES (?, ?, ?, ?)", [(snapshot_id, *node) for node in nodes])
            db.executemany("INSERT INTO visible_map_edges VALUES (?, ?, ?)", [(snapshot_id, *edge) for edge in edges])
        return snapshot_id

    def latest_map_snapshot(self, *, run_id: str) -> dict[str, Any] | None:
        """Retrieve a visible graph exactly as logged, without extrapolation."""
        self.initialize()
        with self._connection() as db:
            snapshot = db.execute(
                "SELECT * FROM map_snapshots WHERE run_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1", (run_id,)
            ).fetchone()
            if snapshot is None:
                return None
            nodes = db.execute("SELECT node_id, node_kind, confidence FROM map_nodes WHERE snapshot_id = ? ORDER BY node_id", (snapshot["id"],)).fetchall()
            edges = db.execute("SELECT from_node_id, to_node_id FROM visible_map_edges WHERE snapshot_id = ? ORDER BY from_node_id, to_node_id", (snapshot["id"],)).fetchall()
        return {**dict(snapshot), "visible_nodes": [dict(row) for row in nodes], "visible_edges": [dict(row) for row in edges]}

    def status(self) -> dict[str, int | str]:
        self.initialize()
        with self._connection() as db:
            return {
                "schema": SCHEMA_VERSION,
                "runs": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                "floors": db.execute("SELECT COUNT(*) FROM floors").fetchone()[0],
                "events": db.execute("SELECT COUNT(*) FROM evidence_events").fetchone()[0],
                "decisions": db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
                "unresolved_decisions": db.execute("SELECT COUNT(*) FROM decisions WHERE status = 'recommended'").fetchone()[0],
                "pending_reviews": db.execute("SELECT COUNT(*) FROM review_queue WHERE status = 'pending'").fetchone()[0],
                "route_snapshots": db.execute("SELECT COUNT(*) FROM route_snapshots").fetchone()[0],
                "map_snapshots": db.execute("SELECT COUNT(*) FROM map_snapshots").fetchone()[0],
                "combat_zone_bases": db.execute("SELECT COUNT(*) FROM combat_zone_bases").fetchone()[0],
                "inventory_events": db.execute("SELECT COUNT(*) FROM inventory_events").fetchone()[0],
            }
