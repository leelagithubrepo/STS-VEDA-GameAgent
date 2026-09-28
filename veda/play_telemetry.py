"""Durable intent/outcome bridge into the existing gameplay ledger.

This module verifies source bytes and context, not pixels or action legality.
Its receipts never authorize input. A prepared action must not be automatically
resent after a crash: it may have crossed the physical side-effect boundary.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from .telemetry_database import SCHEMA_VERSION as TELEMETRY_SCHEMA, TelemetryDatabase

MAX_REQUEST_BYTES = 262144
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_AGE_SECONDS = 180
SCHEMA = "veda.play-telemetry.v1"
CONTEXT_KEYS = {"run_id", "floor_id", "combat_id", "turn_id"}


def _json(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("request must be finite JSON") from exc
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds byte limit")
    return raw


def _text(value, field, limit=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} needs bounded nonempty text")
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be timezone-aware text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _object(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object; missing fields remain unknown")
    return value


def _image(source):
    path = Path(source["path"])
    if not path.is_absolute():
        raise ValueError("source path must be absolute")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ValueError("source is unavailable") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("source is empty or exceeds byte limit")
    if not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        raise ValueError("source must be saved PNG or JPEG bytes")
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("source hash changed or does not match")


def _request(request, kind, *, deep=True):
    raw = _json(request)
    req = json.loads(raw)
    common = {"schema", "operation_id", "context", "source", "state"}
    extra = {"decision": {"action", "reasoning", "phase", "prediction"},
             "outcome": {"decision_id", "status", "evidence_note", "inventory_events", "inventory_baseline",
                         "zone_events", "zone_baseline", "zone_coverage", "transitions"},
             "resume": {"evidence_note", "boundary"}}[kind]
    if not isinstance(req, dict) or req.get("schema") != SCHEMA or set(req) - common - extra:
        raise ValueError("invalid play telemetry request schema or fields")
    _text(req.get("operation_id"), "operation_id", 128)
    try:
        UUID(req["operation_id"])
    except ValueError as exc:
        raise ValueError("operation_id must be a UUID") from exc
    context = _object(req.get("context"), "context")
    if set(context) != CONTEXT_KEYS:
        raise ValueError("context needs run_id, floor_id, combat_id and turn_id (explicit nulls allowed)")
    for key in CONTEXT_KEYS:
        if key in {"run_id", "floor_id"} or context[key] is not None:
            _text(context[key], key, 128)
    if context["turn_id"] is not None and context["combat_id"] is None:
        raise ValueError("turn needs combat")
    source = _object(req.get("source"), "source")
    if set(source) != {"path", "sha256", "captured_at", "origin", "evidence_note"}:
        raise ValueError("source needs exact path, sha256, captured_at, origin and evidence_note")
    _text(source["path"], "source path", 4096)
    if not isinstance(source["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        raise ValueError("source SHA must be lowercase hexadecimal")
    if source["origin"] not in ("reader", "reviewer"):
        raise ValueError("source origin must be reader or reviewer")
    _text(source["evidence_note"], "source evidence")
    captured = _time(source["captured_at"])
    named = re.search(r"ps5_observation_(\d{8}T\d{6}(?:\.\d+)?)Z", Path(source["path"]).name)
    if named:
        fmt = "%Y%m%dT%H%M%S.%f" if "." in named[1] else "%Y%m%dT%H%M%S"
        if datetime.strptime(named[1], fmt).replace(tzinfo=timezone.utc) != captured:
            raise ValueError("source timestamp disagrees with capture filename")
    _object(req.get("state"), "state")
    if kind == "decision":
        action = _object(req.get("action"), "action")
        _text(action.get("kind"), "action kind", 128)
        _text(req.get("reasoning"), "reasoning")
        _text(req.get("phase"), "phase", 128)
        _object(req.get("prediction", {}), "prediction")
    else:
        _text(req.get("evidence_note"), "evidence_note")
    if kind == "outcome":
        _text(req.get("decision_id"), "decision_id", 128)
        if req.get("status") not in ("verified", "unknown", "not_performed"):
            raise ValueError("outcome status must be verified, unknown or not_performed")
        if req.get("zone_coverage", "unknown") not in ("unknown", "partial", "complete"):
            raise ValueError("zone coverage must be complete, partial or unknown")
        for key, limit in (("inventory_events", 100), ("zone_events", 100), ("transitions", 8)):
            if not isinstance(req.get(key, []), list) or len(req.get(key, [])) > limit:
                raise ValueError(f"{key} must be a bounded list")
        if req["status"] != "verified" and any(req.get(k) for k in
                ("inventory_events", "inventory_baseline", "zone_events", "zone_baseline", "transitions")):
            raise ValueError("unverified or unperformed action cannot commit game mutations")
    if kind == "resume" and req.get("boundary") not in ("map", "reward", "combat", "event", "controller", "other"):
        raise ValueError("resume needs a supported boundary")
    if kind == "outcome" and deep:
        _mutation_shapes(req)
    return req, hashlib.sha256(raw).hexdigest(), captured


def validate_outcome_request(request):
    """Validate all outcome/mutation shapes without reading sources or a DB."""
    return _request(request, "outcome")[0]


def outcome_request_digest(request):
    """Canonical digest, including malformed legacy metadata for CAS repair."""
    return hashlib.sha256(_json(request)).hexdigest()


def _mutation_shapes(req):
    kinds = {"card", "relic", "potion"}
    baseline = req.get("inventory_baseline")
    if baseline is not None:
        if req.get("inventory_events") or not isinstance(baseline, dict) or set(baseline) != {"items", "coverage", "evidence"}:
            raise ValueError("inventory baseline needs items, coverage and evidence, without events")
        coverage, evidence = _object(baseline["coverage"], "coverage"), _object(baseline["evidence"], "evidence")
        if set(coverage) != kinds or any(not isinstance(v, str) or v not in {"complete", "partial", "unknown"} for v in coverage.values()):
            raise ValueError("inventory baseline coverage is incomplete")
        if set(evidence) != {k for k, v in coverage.items() if v != "unknown"}:
            raise ValueError("each observed inventory category needs evidence")
        for note in evidence.values():
            _text(note, "inventory category evidence")
        if not isinstance(baseline["items"], list) or len(baseline["items"]) > 300:
            raise ValueError("inventory baseline items must be bounded")
        relics = set()
        for item in baseline["items"]:
            if not isinstance(item, dict) or set(item) - {"kind", "item", "property"}:
                raise ValueError("invalid inventory baseline item")
            kind = _text(item.get("kind"), "inventory kind", 128)
            name = _text(item.get("item"), "inventory item", 128)
            if kind not in kinds or coverage[kind] == "unknown":
                raise ValueError("unknown inventory category cannot assert items")
            if item.get("property") is not None:
                _text(item["property"], "inventory property")
            if kind == "relic":
                if name.strip().casefold() in relics:
                    raise ValueError("duplicate baseline relic")
                relics.add(name.strip().casefold())
    for event in req.get("inventory_events", []):
        if not isinstance(event, dict) or set(event) - {"kind", "action", "item", "property", "related_item", "evidence_note"}:
            raise ValueError("invalid inventory event fields")
        _text(event.get("evidence_note"), "inventory event evidence")
        kind = _text(event.get("kind"), "inventory kind", 128)
        action = _text(event.get("action"), "inventory action", 128)
        _text(event.get("item"), "inventory item", 128)
        if kind not in kinds or action not in {"acquired", "removed", "consumed", "replaced", "property_confirmed"}:
            raise ValueError("invalid inventory kind or action")
        if event.get("property") is not None or action == "property_confirmed":
            _text(event.get("property"), "inventory property")
        if action == "replaced":
            _text(event.get("related_item"), "replacement item", 128)
        elif event.get("related_item") is not None:
            raise ValueError("related_item is only valid for replacement")
    for event in req.get("zone_events", []):
        if not isinstance(event, dict) or set(event) - {"kind", "card_name", "from_zone", "to_zone", "count", "evidence_note"}:
            raise ValueError("invalid zone event fields")
        _text(event.get("evidence_note"), "zone movement evidence")
        kind = _text(event.get("kind"), "zone kind", 128)
        count = event.get("count", 1)
        if type(count) is not int or not 1 <= count <= 300:
            raise ValueError("zone count must be a bounded positive integer")
        if kind not in {"draw", "play", "discard", "exhaust", "return", "generate", "shuffle", "unknown"}:
            raise ValueError("invalid zone kind")
        if kind in {"shuffle", "unknown"}:
            if any(event.get(key) is not None for key in ("card_name", "from_zone", "to_zone")):
                raise ValueError("shuffle and unknown events cannot claim a card movement")
        else:
            _text(event.get("card_name"), "zone card", 128)
            if event.get("from_zone") is None and event.get("to_zone") is None:
                raise ValueError("card movement needs a zone")
            for key in ("from_zone", "to_zone"):
                if event.get(key) is not None:
                    zone = _text(event[key], key, 128)
                    if zone not in {"hand", "draw", "discard", "exhaust"}:
                        raise ValueError("invalid movement zone")
    baseline = req.get("zone_baseline")
    if baseline is not None:
        if (not isinstance(baseline, dict) or set(baseline) != {"deck", "hand", "complete", "opening", "evidence_note"}
                or baseline["complete"] is not True or baseline["opening"] is not True or req.get("zone_coverage") != "complete"):
            raise ValueError("zone baseline needs complete opening deck and hand proof")
        _text(baseline["evidence_note"], "zone baseline evidence")
        for key in ("deck", "hand"):
            if not isinstance(baseline[key], list) or len(baseline[key]) > 300:
                raise ValueError("zone baseline must be bounded")
            for card in baseline[key]:
                _text(card, "zone card", 128)
        if not baseline["deck"] or Counter(baseline["hand"]) - Counter(baseline["deck"]):
            raise ValueError("opening hand must belong to a nonempty deck")
    allowed = {"end_turn": {"closing_state"}, "start_turn": {"turn_number", "phase", "opening_state"},
        "end_combat": {"outcome", "closing_state"}, "start_combat": {"opening_state", "encounter_name", "encounter_type"},
        "advance_floor": {"act", "floor", "node_type", "previous_outcome", "previous_ending_state", "starting_state"}}
    for transition in req.get("transitions", []):
        if not isinstance(transition, dict):
            raise ValueError("transition must be an object")
        kind = _text(transition.get("kind"), "transition kind", 128)
        _text(transition.get("evidence_note"), "transition evidence")
        if kind not in allowed or set(transition) != allowed[kind] | {"kind", "evidence_note"}:
            raise ValueError("unsupported transition or fields")
        for key in allowed[kind]:
            if key.endswith("state"):
                _object(transition[key], key)
            elif key in {"act", "floor", "turn_number"}:
                if type(transition[key]) is not int or not 1 <= transition[key] <= (100 if key != "turn_number" else 100000):
                    raise ValueError("transition number must be a bounded positive integer")
            else:
                _text(transition[key], key, 128)



class PlayTelemetry:
    """One pending physical action per run; append explicit verified facts only."""

    def __init__(self, database: TelemetryDatabase):
        if not database.path.is_file():
            raise ValueError("play telemetry requires an existing run database")
        self.database = database

    def check_run_binding(self, *, run_id):
        """Reject ended or conflicting ledger bindings before any bridge access.

        Read lifecycle columns only in a read-only transaction; no inventory
        replay, initialization, migration, or pixel recognition occurs here.
        This check is a necessary guard, never permission to send input.
        """
        from .play_context import _context, _lifecycle
        _text(run_id, 'run_id', 128)
        db = None
        try:
            db = sqlite3.connect(self.database.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            schema = db.execute("SELECT value FROM schema_metadata WHERE key='schema'").fetchone()
            if schema is None or schema[0] != TELEMETRY_SCHEMA:
                raise ValueError('run binding schema is unavailable; no migration attempted')
            row = db.execute('SELECT id,game,status,ended_at FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None or row['status'] != 'active':
                raise ValueError('run binding is missing or no longer active')
            if row['game'] != 'Slay the Spire':
                raise ValueError('run binding belongs to a different game')
            if row['ended_at'] is not None:
                raise ValueError('run binding has a recorded end timestamp')
            run = dict(row)
            context = _context(db, run)
            lifecycle = _lifecycle(db, run, context)
            if lifecycle['status'] == 'terminal_recorded':
                raise ValueError('run binding has a recorded terminal outcome; bind an authorized new attempt')
            if lifecycle['status'] != 'not_established':
                raise ValueError('run binding has conflicting lifecycle records; reconcile before arming')
            if context['reasons'] and context['reasons'] != ['no recorded floor']:
                raise ValueError('run binding has conflicting current lifecycle IDs; reconcile before arming')
            return {'schema': 'veda.play-run-binding.v1', 'run_id': run_id, 'run_status': 'active',
                    'ended_at': None, 'lifecycle_status': 'not_established', 'allowed': True,
                    'controller_authorized': False, 'runtime_authorized': False}
        except sqlite3.Error:
            raise ValueError('run binding read transaction unavailable; no migration attempted') from None
        finally:
            if db is not None:
                db.close()

    def _replay(self, db, req, digest):
        row = db.execute("SELECT payload_json FROM evidence_events WHERE "
                         "json_extract(payload_json,'$.play_operation_id')=? LIMIT 1", (req["operation_id"],)).fetchone()
        if row:
            payload = json.loads(row[0])
            if payload["request_sha256"] != digest:
                raise ValueError("operation_id was already used with different content")
            return {**payload["receipt"], "idempotent_replay": True}

    def _clock(self, captured, now, *, event_bound=False):
        clock = now or datetime.now(timezone.utc)
        if not isinstance(clock, datetime) or clock.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        if captured > clock:
            raise ValueError("future source cannot become current evidence")
        if not event_bound and (clock - captured).total_seconds() > MAX_AGE_SECONDS:
            raise ValueError("source is stale; observe again before recording live changes")
        return clock.isoformat()

    def _context(self, db, context, captured, *, allow_paused=False):
        run = db.execute("SELECT * FROM runs WHERE id=?", (context["run_id"],)).fetchone()
        if run is None or run["status"] != "active":
            raise ValueError("exact active run is required")
        if captured < _time(run["started_at"]):
            raise ValueError("source predates run")
        self.database._validate_event_context(db, **context)
        floor = db.execute("SELECT id FROM floors WHERE run_id=? ORDER BY recorded_at DESC,rowid DESC LIMIT 1",
                           (context["run_id"],)).fetchone()
        if floor is None or floor[0] != context["floor_id"]:
            raise ValueError("context must identify latest recorded floor")
        opened = db.execute("SELECT id FROM combats WHERE run_id=? AND closed_at IS NULL", (context["run_id"],)).fetchall()
        if [r[0] for r in opened] != ([context["combat_id"]] if context["combat_id"] else []):
            raise ValueError("context must identify the sole open combat")
        if context["combat_id"]:
            turn = db.execute("SELECT id,opened_at,closed_at FROM combat_turns WHERE combat_id=? ORDER BY turn_number DESC LIMIT 1",
                              (context["combat_id"],)).fetchone()
            if turn is None:
                if context["turn_id"] is not None:
                    raise ValueError("turn is not open")
            elif ((turn[2] is not None and context["turn_id"] is not None)
                  or (turn[2] is None and turn[0] != context["turn_id"])):
                raise ValueError("context must identify latest open turn")
            elif captured < _time(turn[1]):
                raise ValueError("source predates turn")
        checkpoint = db.execute("SELECT kind,observed_at FROM session_checkpoints WHERE run_id=? "
                                "ORDER BY julianday(observed_at) DESC,rowid DESC LIMIT 1", (context["run_id"],)).fetchone()
        if checkpoint:
            if captured < _time(checkpoint[1]):
                raise ValueError("source predates pause/resume checkpoint")
            if checkpoint[0] == "pause" and not allow_paused:
                raise ValueError("run is paused; record an explicit resume first")
        return checkpoint

    def _chronology(self, db, context, captured):
        rows = db.execute("SELECT observed_at FROM inventory_events WHERE run_id=? "
            "UNION ALL SELECT observed_at FROM inventory_baselines WHERE run_id=? "
            "UNION ALL SELECT observed_at FROM evidence_events WHERE run_id=? "
            "UNION ALL SELECT z.observed_at FROM combat_zone_events z JOIN combats c ON c.id=z.combat_id WHERE c.run_id=?",
            (context["run_id"],) * 4).fetchmany(20001)
        if len(rows) > 20000:
            raise ValueError("run exceeds bounded chronology size")
        if any(_time(row[0]) > captured for row in rows):
            raise ValueError("source predates recorded inventory, zone or evidence changes")

    def _state_context(self, db, state, context, source):
        floor = db.execute("SELECT act,floor FROM floors WHERE id=?", (context["floor_id"],)).fetchone()
        run = db.execute("SELECT ascension FROM runs WHERE id=?", (context["run_id"],)).fetchone()
        # Choice and inspection packets place observed HUD identity in facts;
        # it is no less binding than a combat snapshot's top-level fields.
        facts = state.get('facts', {})
        if not isinstance(facts, dict):
            raise ValueError('observed facts must be an object')
        for observed in (state, facts):
            for key, expected in (("act", floor[0]), ("floor", floor[1]), ("ascension", run[0])):
                if observed.get(key) is not None:
                    if type(observed[key]) is not int or observed[key] != expected:
                        raise ValueError(f"observed {key} conflicts with recorded context")
        if state.get("observed_at") is not None and _time(state["observed_at"]) != _time(source["captured_at"]):
            raise ValueError("state observed_at disagrees with source capture")

    def _revision(self, db, run_id, *, ignore_checkpoints=False):
        # Include mutable lifecycle/decision rows; append-only tables need counts
        # and max rowid. An unrelated writer forces explicit reconciliation.
        value = {}
        for table, query in {
            "runs": "SELECT * FROM runs WHERE id=?",
            "floors": "SELECT * FROM floors WHERE run_id=? ORDER BY rowid",
            "combats": "SELECT * FROM combats WHERE run_id=? ORDER BY rowid",
            "turns": "SELECT t.* FROM combat_turns t JOIN combats c ON c.id=t.combat_id WHERE c.run_id=? ORDER BY t.rowid",
            "decisions": "SELECT d.* FROM decisions d JOIN evidence_events e ON e.id=d.event_id WHERE e.run_id=? ORDER BY d.rowid",
        }.items():
            rows = db.execute(query + " LIMIT 10001", (run_id,)).fetchall()
            if len(rows) > 10000:
                raise ValueError("run exceeds bounded lifecycle revision size")
            value[table] = [dict(r) for r in rows]
        for table in ("inventory_events", "inventory_baselines", "session_checkpoints"):
            if table == "session_checkpoints" and ignore_checkpoints:
                continue
            value[table] = tuple(db.execute(f"SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM {table} WHERE run_id=?", (run_id,)).fetchone())
        value["zones"] = tuple(db.execute("SELECT COUNT(*),COALESCE(MAX(z.rowid),0) FROM combat_zone_events z JOIN combats c ON c.id=z.combat_id WHERE c.run_id=?", (run_id,)).fetchone())
        value["bases"] = tuple(db.execute("SELECT COUNT(*),COALESCE(MAX(z.rowid),0) FROM combat_zone_bases z JOIN combats c ON c.id=z.combat_id WHERE c.run_id=?", (run_id,)).fetchone())
        value["evidence"] = tuple(db.execute("SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM evidence_events WHERE run_id=? AND kind NOT IN ('play_outcome','play_resume')", (run_id,)).fetchone())
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _revision_evidence(self, db, run_id):
        return {"revision": self._revision(db, run_id),
                "revision_without_checkpoints": self._revision(db, run_id, ignore_checkpoints=True),
                "checkpoint_count": db.execute("SELECT COUNT(*) FROM session_checkpoints WHERE run_id=?", (run_id,)).fetchone()[0]}

    def _only_intervening_pause(self, db, req, prior):
        run_id = req["context"]["run_id"]
        if self._revision(db, run_id, ignore_checkpoints=True) != prior.get("revision_without_checkpoints"):
            return False
        count = db.execute("SELECT COUNT(*) FROM session_checkpoints WHERE run_id=?", (run_id,)).fetchone()[0]
        if count != prior.get("checkpoint_count", -1) + 1:
            return False
        pause = db.execute("SELECT * FROM session_checkpoints WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        return bool(pause and pause["kind"] == "pause" and pause["floor_id"] == req["context"]["floor_id"]
                    and pause["combat_id"] == req["context"]["combat_id"]
                    and _time(prior["request"]["source"]["captured_at"]) <= _time(pause["observed_at"]) < _time(req["source"]["captured_at"]))

    def _pending(self, db, run_id):
        return db.execute("SELECT d.*,e.payload_json,e.run_id,e.floor_id,e.combat_id,e.turn_id FROM decisions d "
                          "JOIN evidence_events e ON e.id=d.event_id WHERE e.run_id=? AND d.status='recommended' ORDER BY d.rowid",
                          (run_id,)).fetchall()

    def _finish(self, db, event_id, req, digest, receipt, *, extra=None):
        payload = json.loads(db.execute("SELECT payload_json FROM evidence_events WHERE id=?", (event_id,)).fetchone()[0])
        payload.update({"play_operation_id": req["operation_id"], "request_sha256": digest,
                        "request": req, "receipt": receipt, **(extra or {})})
        db.execute("UPDATE evidence_events SET payload_json=?,observed_at=? WHERE id=?",
                   (json.dumps(payload, sort_keys=True), req["source"]["captured_at"], event_id))
        _image(req["source"])
        return receipt

    @staticmethod
    def _receipt(kind, req, recorded_at, **extra):
        return {"schema": "veda.play-telemetry-receipt.v1", "kind": kind, "operation_id": req["operation_id"],
                "context": req["context"], "source": req["source"], "recorded_at": recorded_at,
                "runtime_authorized": False, "controller_authorized": False, "idempotent_replay": False,
                "recognition_performed": False, **extra}

    def record_decision(self, request, *, now=None, event_bound=None):
        """Persist intent BEFORE dispatch. Return value is not dispatch permission."""
        req, digest, captured = _request(request, "decision")
        if event_bound is not None:
            from .evidence_continuity import check_binding
            if event_bound.get('source') != req['source'] or event_bound.get('context') != req['context']:
                raise ValueError('event-bound decision source/context differs')
            check_binding(event_bound.get('binding'), event_bound.get('binding'), req['context'], req['source'])
        with self.database._transaction(), self.database._connection() as db:
            replay = self._replay(db, req, digest)
            if replay:
                return replay
            recorded = self._clock(captured, now, event_bound=event_bound is not None)
            self._context(db, req["context"], captured)
            self._chronology(db, req["context"], captured)
            self._state_context(db, req["state"], req["context"], req["source"])
            if self._pending(db, req["context"]["run_id"]):
                raise ValueError("a decision remains pending; reconcile it without repeating input")
            _image(req["source"])
            ident = self.database.record_decision(**req["context"], phase=req["phase"], state=req["state"],
                options=[req["action"]], recommendation=req["action"], reasoning=req["reasoning"],
                prediction=req.get("prediction", {}), screenshot_path=req["source"]["path"],
                source="play_telemetry:" + req["source"]["origin"], evidence_metadata={"source": req["source"]})
            event_id = db.execute("SELECT event_id FROM decisions WHERE id=?", (ident,)).fetchone()[0]
            receipt = self._receipt("decision", req, recorded, decision_id=ident, status="pending",
                                    must_not_repeat=True, requires_observed_outcome=True)
            return self._finish(db, event_id, req, digest, receipt,
                                extra=self._revision_evidence(db, req["context"]["run_id"]))

    def record_resume(self, request, *, now=None):
        """Explicit fresh pause→resume evidence; never implicitly unpause a run."""
        req, digest, captured = _request(request, "resume")
        with self.database._transaction(), self.database._connection() as db:
            replay = self._replay(db, req, digest)
            if replay:
                return replay
            recorded = self._clock(captured, now)
            previous = self._context(db, req["context"], captured, allow_paused=True)
            self._chronology(db, req["context"], captured)
            self._state_context(db, req["state"], req["context"], req["source"])
            if previous is None or previous[0] != "pause" or captured <= _time(previous[1]):
                raise ValueError("resume must follow a recorded pause")
            if self._pending(db, req["context"]["run_id"]):
                raise ValueError("pending input must be reconciled before resume")
            _image(req["source"])
            checkpoint = self.database.record_session_checkpoint(
                run_id=req["context"]["run_id"], floor_id=req["context"]["floor_id"], combat_id=req["context"]["combat_id"],
                kind="resume", boundary=req["boundary"], state=req["state"], payload={"source": req["source"], "note": req["evidence_note"]},
                screenshot_path=req["source"]["path"], source="play_telemetry:" + req["source"]["origin"],
                observed_at=req["source"]["captured_at"])
            event = self.database.record_event(**req["context"], kind="play_resume", phase=req["boundary"], state=req["state"],
                source="play_telemetry:" + req["source"]["origin"], screenshot_path=req["source"]["path"])
            receipt = self._receipt("resume", req, recorded, checkpoint_id=checkpoint, status="resumed")
            return self._finish(db, event, req, digest, receipt)

    @staticmethod
    def _unchanged_focus_review(original, req, proof):
        """A later reviewed no-progress frame resolves navigation, never a card.

        Identical pixels can be the truthful result of a focus probe. Keep this
        exception narrower than the general verified-action path.
        """
        if not isinstance(proof, dict) or proof.get('basis') not in {'fresh_verified_result', 'action_bound_result'}:
            return False
        action = original.get('action', {})
        shop = proof.get('shop_focus_transition')
        if isinstance(shop, dict) and set(shop) == {'from', 'button', 'to'}:
            before_state, after_state = original.get('state', {}), req['state']
            before_ui, after_ui = before_state.get('ui', {}), after_state.get('ui', {})
            def strip_ui(value):
                if isinstance(value, dict):
                    return {k: strip_ui(v) for k, v in value.items() if k not in {'navigation', 'shop_navigation', 'evidence'}}
                if isinstance(value, list):
                    return [strip_ui(v) for v in value]
                return value
            return (proof.get('decision_policy') == 'learning' and action.get('kind') == 'navigation'
                    and action.get('step_kind') == 'focus'
                    and before_ui.get('menu_family') in {'shop_stock', 'shop_remove'}
                    and shop['button'] in {'up', 'down', 'left', 'right'}
                    and shop['from'] == shop['to'] == before_ui.get('focused_id') == after_ui.get('focused_id')
                    and shop in after_ui.get('shop_navigation', [])
                    and strip_ui(before_ui) == strip_ui(after_ui)
                    and all(before_state.get(k) == after_state.get(k) for k in ('resources', 'facts'))
                    and req['source']['path'] != original['source']['path']
                    and not any(req.get(k) for k in ('inventory_events', 'inventory_baseline', 'zone_events', 'zone_baseline', 'transitions')))
        expected = action.get('expected', {}).get('kind')
        if (action.get('kind') != 'navigation'
                or not (expected in {'clear', 'focus_probe'}
                        or action.get('inspection') in {'clear_tooltip', 'inspect_focus'})):
            return False
        transition = proof.get('focus_transition')
        if not isinstance(transition, dict) or transition.get('returned_to_hand') is not False:
            return False
        before, after = transition.get('before'), transition.get('after')
        keys = {'domain', 'tooltip_kind', 'subject_id', 'focused_card_id', 'selected_card_id'}
        if not isinstance(before, dict) or not isinstance(after, dict) or set(before) != keys or set(after) != keys:
            return False
        if (after['domain'] not in {'player_status', 'relic', 'potion', 'enemy'}
                or after['tooltip_kind'] not in {after['domain'], 'none'}
                or not isinstance(after['subject_id'], str) or not after['subject_id'].strip()
                or after['focused_card_id'] is not None or after['selected_card_id'] is not None):
            return False
        same = transition.get('effect') == 'unchanged' and before == after
        observed_legacy = (transition.get('effect') == 'focus_observed' and before['domain'] == 'unknown'
                           and before['selected_card_id'] is None and before['focused_card_id'] is None)
        if not (same or observed_legacy):
            return False
        def facts(state):
            return {k: v for k, v in state.items() if k not in {'observed_at', 'ui'}}
        return (facts(original.get('state', {})) == facts(req['state'])
                and req['source']['path'] != original['source']['path']
                and not any(req.get(k) for k in ('inventory_events', 'inventory_baseline', 'zone_events',
                                                 'zone_baseline', 'transitions')))

    def _outcome_guard(self, db, req, captured, *, reviewed_inspection=False, focus_review=None):
        row = db.execute("SELECT d.*,e.payload_json FROM decisions d JOIN evidence_events e ON e.id=d.event_id WHERE d.id=?",
                         (req["decision_id"],)).fetchone()
        if row is None or row["status"] != "recommended":
            raise ValueError("exact unresolved decision is required")
        payload = json.loads(row["payload_json"])
        original = payload.get("request")
        if not isinstance(original, dict) or payload.get("receipt", {}).get("kind") != "decision":
            raise ValueError("decision was not prepared through this connector")
        if req["context"] != original["context"]:
            raise ValueError("outcome context conflicts with prepared decision")
        last = db.execute("SELECT payload_json FROM evidence_events WHERE kind='play_outcome' "
                          "AND json_extract(payload_json,'$.request.decision_id')=? ORDER BY rowid DESC LIMIT 1",
                          (req["decision_id"],)).fetchone()
        prior = json.loads(last[0]) if last else payload
        if captured <= _time(prior["request"]["source"]["captured_at"]):
            raise ValueError("outcome needs a later observation")
        if req["status"] == "verified" and req["source"]["sha256"] == original["source"]["sha256"]:
            action = original.get("action", {})
            before, after = original.get("state", {}), req["state"]
            inspection_only = (reviewed_inspection
                and action.get("kind") == "navigation" and action.get("step_kind") == "inspect"
                and before.get("ui", {}).get("menu_family") == "map_inspect"
                and after.get("ui", {}).get("screen") == "map"
                and after.get("ui", {}).get("phase") == "result"
                and after.get("ui", {}).get("map_view", {}).get("effect") == "unchanged"
                and req["source"]["path"] != original["source"]["path"]
                and all(_json(after.get(k)) == _json(before.get(k)) for k in ("resources", "facts"))
                and not any(req.get(k) for k in ("inventory_events", "inventory_baseline", "zone_events",
                                               "zone_baseline", "transitions")))
            if not inspection_only and not self._unchanged_focus_review(original, req, focus_review):
                raise ValueError("unchanged source bytes do not establish a performed action")
        pause_reconciled = False
        if self._revision(db, req["context"]["run_id"]) != prior["revision"]:
            pause_reconciled = self._only_intervening_pause(db, req, prior)
        if self._revision(db, req["context"]["run_id"]) != prior["revision"] and not pause_reconciled:
            raise ValueError("ledger/context changed since preparation; keep pending for explicit reconciliation")
        self._context(db, req["context"], captured, allow_paused=True)
        self._chronology(db, req["context"], captured)
        _image(req["source"])
        return original, pause_reconciled

    def record_outcome(self, request, *, now=None, verified_evidence=None):
        """Commit explicit observed facts atomically; uncertainty stays pending."""
        req, digest, captured = _request(request, "outcome")
        with self.database._transaction(), self.database._connection() as db:
            replay = self._replay(db, req, digest)
            if replay:
                return replay
            if verified_evidence is None:
                recorded = self._clock(captured, now)
            else:
                proof = _object(json.loads(_json(verified_evidence)), "durable verified evidence")
                clock = now or datetime.now(timezone.utc)
                if (req["status"] != "verified" or proof.get("outcome_sha256") != digest
                        or proof.get("source") != req["source"]
                        or proof.get("basis") not in {"fresh_verified_result", "action_bound_result", "reviewed_retained_verified_result"}
                        or _time(proof.get("reviewed_at")) > clock or captured > _time(proof["reviewed_at"])):
                    raise ValueError("durable verified evidence does not match this outcome")
                _text(proof.get("action_id"), "verified action", 128)
                if proof["basis"] in {"fresh_verified_result", "action_bound_result"}:
                    if proof['basis'] == 'action_bound_result':
                        if not _time(proof.get('attempted_at')) < captured:
                            raise ValueError('result must follow the exact attempted input')
                    self._clock(captured, _time(proof["reviewed_at"]), event_bound=proof['basis'] == 'action_bound_result')
                    review = _object(proof.get("review"), "saved verified source review")
                    _text(review.get("reviewer"), "source reviewer", 128)
                    _text(review.get("frame_id"), "source frame", 256)
                    if review.get("complete") is not True or review.get("image_sha256") != req["source"]["sha256"]:
                        raise ValueError("saved verification review belongs to a different source")
                else:
                    audit = _object(proof.get("metadata_repair"), "metadata repair audit")
                    original_request = _object(audit.get("original_outcome_request"), "original retained outcome")
                    repair = _object(audit.get("request"), "metadata repair request")
                    self._check_repair_content(original_request, req, repair, action_id=proof["action_id"])
                    if audit.get("repaired_outcome_sha256") != digest or audit.get("recorded_at") != proof["reviewed_at"]:
                        raise ValueError("metadata repair audit differs from durable outcome")
                recorded = clock.isoformat()
            original, pause_reconciled = self._outcome_guard(db, req, captured,
                reviewed_inspection=verified_evidence is not None
                    and verified_evidence.get("basis") in {"fresh_verified_result", "action_bound_result"},
                focus_review=verified_evidence)
            if verified_evidence is not None and original["operation_id"] != verified_evidence["action_id"]:
                raise ValueError("durable review belongs to a different input")
            ids = {"inventory": [], "zones": [], "inventory_baseline": None, "zone_baseline": None}
            context = dict(req["context"])
            if req["status"] == "verified":
                self._inventory(db, req, ids)
                self._zones(db, req, ids)
                context = self._transitions(db, req)
                self._zone_baseline(db, req, ids, context)
            elif req["status"] == "unknown" and context["combat_id"] and db.execute(
                    "SELECT 1 FROM combat_zone_bases WHERE combat_id=?", (context["combat_id"],)).fetchone():
                ident = self.database.record_combat_zone_event(combat_id=context["combat_id"], turn_id=context["turn_id"],
                    kind="unknown", reason=req["evidence_note"], source="play_telemetry:uncertain_outcome")
                db.execute("UPDATE combat_zone_events SET observed_at=? WHERE id=?", (req["source"]["captured_at"], ident))
                ids["zones"].append(ident)
            self._state_context(db, req["state"], context, req["source"])
            event = self.database.record_event(**req["context"], kind="play_outcome", phase="observed_outcome", state=req["state"],
                screenshot_path=req["source"]["path"], source="play_telemetry:" + req["source"]["origin"])
            if req["status"] != "unknown":
                actual = {"state": req["state"], "source": req["source"], "event_id": event,
                          "status": req["status"], "evidence_note": req["evidence_note"]}
                # Learning records describe an already verified result. They
                # never change observed state, resolve an unknown input, or
                # turn a forecast into a game fact.
                if verified_evidence is not None:
                    learning = {key: proof[key] for key in
                                ("decision_policy", "assessment", "observed_mismatches", "focus_transition") if key in proof}
                    if learning:
                        actual["learning"] = learning
                self.database.resolve_decision(decision_id=req["decision_id"], chosen_action=original["action"],
                    actual_outcome=actual,
                    status="resolved" if req["status"] == "verified" else "skipped")
                db.execute("UPDATE decisions SET resolved_at=? WHERE id=?", (req["source"]["captured_at"], req["decision_id"]))
            receipt = self._receipt("outcome", req, recorded, decision_id=req["decision_id"], event_id=event,
                status=req["status"], unresolved=req["status"] == "unknown", next_context=context, recorded_ids=ids,
                intervening_pause_reconciled=pause_reconciled,
                must_not_repeat=True, requires_new_observation=True,
                inventory_requires_inspection=req["status"] == "unknown",
                zone_coverage="unknown" if req["status"] == "unknown" else req.get("zone_coverage", "unknown"),
                zone_state_known=(self.database.combat_zone_state(combat_id=context["combat_id"])["known"]
                    if context["combat_id"] and db.execute("SELECT 1 FROM combat_zone_bases WHERE combat_id=?", (context["combat_id"],)).fetchone()
                    else None))
            return self._finish(db, event, req, digest, receipt,
                                extra={**self._revision_evidence(db, req["context"]["run_id"]),
                       **({"durable_verified_evidence": verified_evidence} if verified_evidence is not None else {})})

    @staticmethod
    def _check_repair_content(original, repaired, repair, *, action_id):
        if (repair.get("operation") != "repair_outcome_metadata" or repair.get("action_id") != action_id
                or repair.get("outcome_operation_id") != original.get("operation_id")
                or repair.get("expected_outcome_sha256") != outcome_request_digest(original)):
            raise ValueError("metadata repair identities or compare-and-swap differ")
        UUID(repair["repair_id"])
        review = _object(repair.get("review"), "retained evidence review")
        _text(review.get("reviewer"), "metadata reviewer", 128)
        _text(review.get("evidence_note"), "metadata review evidence")
        if (review.get("kind") != "reviewed_retained_outcome_metadata" or review.get("complete") is not True
                or review.get("source") != original.get("source")):
            raise ValueError("metadata repair needs explicit review of retained evidence")
        notes = repair.get("inventory_event_notes")
        if not isinstance(notes, list) or not 1 <= len(notes) <= 100:
            raise ValueError("bounded missing event notes required")
        projected = json.loads(_json(original))
        events, seen = projected.get("inventory_events", []), set()
        for note in notes:
            if (not isinstance(note, dict) or set(note) != {"index", "evidence_note"}
                    or type(note["index"]) is not int or not 0 <= note["index"] < len(events)
                    or note["index"] in seen or "evidence_note" in events[note["index"]]):
                raise ValueError("repair can only add absent notes to distinct existing events")
            _text(note["evidence_note"], "inventory event evidence")
            events[note["index"]]["evidence_note"] = note["evidence_note"]
            seen.add(note["index"])
        if _json(projected) != _json(repaired):
            raise ValueError("metadata repair cannot alter game effects or existing evidence")

    def check_outcome_metadata_repair(self, original, repaired, *, action_id):
        """Read-only proof that this exact outcome has not committed and stays current."""
        old, _, captured = _request(original, "outcome", deep=False)
        new = validate_outcome_request(repaired)
        def effects(value):
            value = json.loads(_json(value))
            for event in value.get("inventory_events", []):
                event.pop("evidence_note", None)
            return value
        if effects(old) != effects(new) or old["status"] != "verified":
            raise ValueError("metadata repair cannot change observed game effects")
        db = sqlite3.connect(self.database.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            if db.execute("SELECT 1 FROM evidence_events WHERE json_extract(payload_json,'$.play_operation_id')=?", (old["operation_id"],)).fetchone():
                raise ValueError("outcome operation already committed; only exact finalize replay is allowed")
            original_decision, _ = self._outcome_guard(db, new, captured)
            if original_decision["operation_id"] != action_id:
                raise ValueError("metadata repair belongs to a different input")
        finally:
            db.close()
        return {"uncommitted": True, "must_not_repeat": True, "controller_authorized": False}

    def _inventory(self, db, req, ids):
        context, source = req["context"], req["source"]
        if req.get("inventory_baseline") is not None and req.get("inventory_events"):
            raise ValueError("use observed inventory baseline or explicit events, not both")
        baseline = req.get("inventory_baseline")
        if baseline is not None:
            if not isinstance(baseline, dict) or set(baseline) != {"items", "coverage", "evidence"}:
                raise ValueError("inventory baseline needs items, coverage and category evidence")
            coverage = _object(baseline["coverage"], "inventory coverage")
            evidence = _object(baseline["evidence"], "inventory evidence")
            if set(evidence) != {k for k, v in coverage.items() if v != "unknown"}:
                raise ValueError("each observed inventory category needs evidence")
            for note in evidence.values():
                _text(note, "inventory category evidence")
            if not isinstance(baseline["items"], list) or len(baseline["items"]) > 300:
                raise ValueError("inventory baseline items must be bounded")
            for item in baseline["items"]:
                if not isinstance(item, dict) or coverage.get(item.get("kind")) == "unknown":
                    raise ValueError("unknown inventory category cannot assert items")
            ident = self.database.record_inventory_baseline(run_id=context["run_id"], floor_id=context["floor_id"],
                items=baseline["items"], coverage=coverage, source="play_telemetry:" + source["origin"], screenshot_path=source["path"])
            db.execute("UPDATE inventory_baselines SET observed_at=? WHERE id=?", (source["captured_at"], ident))
            ids["inventory_baseline"] = ident
        current = self.database.inventory_ledger(run_id=context["run_id"], include_history=False)["current"]
        counts = {k: Counter(v) for k, v in current.items()}
        for event in req.get("inventory_events", []):
            if not isinstance(event, dict) or set(event) - {"kind", "action", "item", "property", "related_item", "evidence_note"}:
                raise ValueError("invalid inventory event fields")
            _text(event.get("evidence_note"), "inventory event evidence")
            kind, action, name = event.get("kind"), event.get("action"), event.get("item")
            _text(kind, "inventory kind", 128)
            _text(action, "inventory action", 128)
            _text(name, "inventory item", 128)
            if event.get("property") is not None:
                _text(event["property"], "inventory property")
            if event.get("related_item") is not None:
                _text(event["related_item"], "related inventory item", 128)
                if action != "replaced":
                    raise ValueError("related_item is only valid for replacement")
            if kind not in counts:
                raise ValueError("invalid inventory kind")
            if action in ("removed", "consumed", "replaced"):
                if counts[kind][name] < 1:
                    raise ValueError("removed/consumed item is absent from confirmed inventory")
                counts[kind][name] -= 1
            if action == "replaced":
                _text(event.get("related_item"), "replacement item", 128)
                counts[kind][event["related_item"]] += 1
            if action == "acquired":
                counts[kind][name] += 1
            ident = self.database.record_inventory_event(run_id=context["run_id"], floor_id=context["floor_id"],
                item_kind=kind, action=action, item_name=name, property_text=event.get("property"), related_item_name=event.get("related_item"),
                source="play_telemetry:" + source["origin"], screenshot_path=source["path"])
            db.execute("UPDATE inventory_events SET observed_at=? WHERE id=?", (source["captured_at"], ident))
            ids["inventory"].append(ident)

    def _zone_baseline(self, db, req, ids, context):
        source = req["source"]
        baseline = req.get("zone_baseline")
        if baseline is not None:
            if not isinstance(baseline, dict) or set(baseline) != {"deck", "hand", "complete", "opening", "evidence_note"}:
                raise ValueError("zone baseline needs complete opening deck and hand proof")
            if (baseline["complete"] is not True or baseline["opening"] is not True
                    or req.get("zone_coverage") != "complete"):
                raise ValueError("partial or later hand cannot initialize opening zones")
            _text(baseline["evidence_note"], "zone baseline evidence")
            for key in ("deck", "hand"):
                if not isinstance(baseline[key], list) or len(baseline[key]) > 300:
                    raise ValueError("zone baseline must be bounded")
                for card in baseline[key]:
                    _text(card, "zone card", 128)
            turn = db.execute("SELECT turn_number FROM combat_turns WHERE id=?", (context["turn_id"],)).fetchone()
            if not context["combat_id"] or turn is None or turn[0] != 1:
                raise ValueError("opening zone baseline requires the observed first turn")
            self.database.start_combat_zones(combat_id=context["combat_id"], deck=baseline["deck"], hand=baseline["hand"],
                source="play_telemetry:" + source["origin"])
            db.execute("UPDATE combat_zone_bases SET recorded_at=? WHERE combat_id=?", (source["captured_at"], context["combat_id"]))
            ids["zone_baseline"] = context["combat_id"]

    def _zones(self, db, req, ids):
        context, source = req["context"], req["source"]
        events = list(req.get("zone_events", []))
        if (context["combat_id"] and req.get("zone_coverage", "unknown") != "complete"
                and db.execute("SELECT 1 FROM combat_zone_bases WHERE combat_id=?", (context["combat_id"],)).fetchone()):
            events.append({"kind": "unknown", "evidence_note": "Outcome did not establish all zone movements: " + req["evidence_note"]})
        for index, event in enumerate(events):
            if not isinstance(event, dict) or set(event) - {"kind", "card_name", "from_zone", "to_zone", "count", "evidence_note"}:
                raise ValueError("invalid zone event fields")
            _text(event.get("evidence_note"), "zone movement evidence")
            _text(event.get("kind"), "zone kind", 128)
            for key in ("card_name", "from_zone", "to_zone"):
                if event.get(key) is not None:
                    _text(event[key], key, 128)
            count = event.get("count", 1)
            if type(count) is not int or not 1 <= count <= 300:
                raise ValueError("zone count must be a bounded positive integer")
            ident = self.database.record_combat_zone_event(combat_id=context["combat_id"], turn_id=context["turn_id"],
                kind=event.get("kind"), card_name=event.get("card_name"), from_zone=event.get("from_zone"), to_zone=event.get("to_zone"),
                count=count, reason=event["evidence_note"], source="play_telemetry:" + source["origin"])
            # The legacy zone reader orders equal timestamps by id. Retain the
            # actual capture timestamp and encode sequence only in opaque IDs.
            ordered_id = str(UUID(bytes=hashlib.sha256(req["operation_id"].encode()).digest()[:12] + index.to_bytes(4, "big")))
            db.execute("UPDATE combat_zone_events SET id=?,observed_at=? WHERE id=?", (ordered_id, source["captured_at"], ident))
            ids["zones"].append(ordered_id)

    def _transitions(self, db, req):
        context = dict(req["context"])
        timestamp = req["source"]["captured_at"]
        for transition in req.get("transitions", []):
            if not isinstance(transition, dict):
                raise ValueError("transition must be an object")
            _text(transition.get("evidence_note"), "transition evidence")
            kind = transition.get("kind")
            _text(kind, "transition kind", 128)
            allowed = {"end_turn": {"closing_state"}, "start_turn": {"turn_number", "phase", "opening_state"},
                       "end_combat": {"outcome", "closing_state"},
                       "start_combat": {"opening_state", "encounter_name", "encounter_type"},
                       "advance_floor": {"act", "floor", "node_type", "previous_outcome", "previous_ending_state", "starting_state"}}
            if kind not in allowed or set(transition) != allowed[kind] | {"kind", "evidence_note"}:
                raise ValueError("unsupported transition or fields")
            summary = {"play_evidence": req["source"], "evidence_note": transition["evidence_note"]}
            if kind == "end_turn":
                _object(transition["closing_state"], "closing_state")
                self._state_context(db, transition["closing_state"], context, req["source"])
                if context["turn_id"] is None:
                    raise ValueError("no open turn to close")
                self.database.complete_combat_turn(turn_id=context["turn_id"], closing_state=transition["closing_state"], summary=summary)
                db.execute("UPDATE combat_turns SET closed_at=? WHERE id=?", (timestamp, context["turn_id"]))
                context["turn_id"] = None
            elif kind == "start_turn":
                _object(transition["opening_state"], "opening_state")
                self._state_context(db, transition["opening_state"], context, req["source"])
                if context["combat_id"] is None or context["turn_id"] is not None:
                    raise ValueError("start_turn requires an open combat and no open turn")
                prior = db.execute("SELECT COALESCE(MAX(turn_number),0) FROM combat_turns WHERE combat_id=?", (context["combat_id"],)).fetchone()[0]
                if type(transition["turn_number"]) is not int or transition["turn_number"] != prior + 1:
                    raise ValueError("next observed turn must have the next explicit number")
                _text(transition["phase"], "turn phase", 128)
                context["turn_id"] = self.database.start_combat_turn(combat_id=context["combat_id"], turn_number=transition["turn_number"],
                    phase=transition["phase"], opening_state=transition["opening_state"], summary=summary)
                db.execute("UPDATE combat_turns SET opened_at=? WHERE id=?", (timestamp, context["turn_id"]))
            elif kind == "end_combat":
                _object(transition["closing_state"], "closing_state")
                self._state_context(db, transition["closing_state"], context, req["source"])
                _text(transition["outcome"], "combat outcome", 128)
                if context["combat_id"] is None or context["turn_id"] is not None:
                    raise ValueError("end_combat requires explicit end_turn first")
                self.database.complete_combat(combat_id=context["combat_id"], outcome=transition["outcome"], closing_state=transition["closing_state"], summary=summary)
                db.execute("UPDATE combats SET closed_at=? WHERE id=?", (timestamp, context["combat_id"]))
                context["combat_id"] = None
            elif kind == "start_combat":
                _object(transition["opening_state"], "opening_state")
                self._state_context(db, transition["opening_state"], context, req["source"])
                if context["combat_id"] is not None:
                    raise ValueError("combat already open")
                _text(transition["encounter_name"], "encounter name", 128)
                _text(transition["encounter_type"], "encounter type", 128)
                context["combat_id"] = self.database.start_combat(**{k: context[k] for k in ("run_id", "floor_id")},
                    opening_state=transition["opening_state"], encounter_name=transition["encounter_name"], encounter_type=transition["encounter_type"], summary=summary)
                db.execute("UPDATE combats SET opened_at=? WHERE id=?", (timestamp, context["combat_id"]))
            elif kind == "advance_floor":
                if context["combat_id"] is not None:
                    raise ValueError("cannot advance floor before observed combat closure")
                for key in ("act", "floor"):
                    if type(transition[key]) is not int or not 1 <= transition[key] <= 100:
                        raise ValueError("floor/act needs an explicit bounded positive integer")
                _text(transition["node_type"], "node type", 128)
                _text(transition["previous_outcome"], "previous floor outcome", 128)
                _object(transition["previous_ending_state"], "previous ending state")
                self._state_context(db, transition["previous_ending_state"], context, req["source"])
                _object(transition["starting_state"], "starting state")
                old = db.execute("SELECT act,floor,outcome FROM floors WHERE id=?", (context["floor_id"],)).fetchone()
                if old[1] is not None and transition["floor"] <= old[1]:
                    raise ValueError("new floor must advance; missing intermediate floors are not reconstructed")
                # Departure must preserve an already reviewed floor conclusion.
                if old[2] is None:
                    self.database.complete_floor(floor_id=context["floor_id"], outcome=transition["previous_outcome"], ending_state=transition["previous_ending_state"], summary=summary)
                context["floor_id"] = self.database.record_floor(run_id=context["run_id"], act=transition["act"], floor=transition["floor"],
                    node_type=transition["node_type"], outcome=None, starting_state=transition["starting_state"], summary=summary)
                self._state_context(db, transition["starting_state"], context, req["source"])
        return context

    def recover(self, *, run_id):
        """Read pending actions without resending, resolving, or claiming freshness."""
        _text(run_id, "run_id", 128)
        self.database.initialize()
        with self.database._connection() as db:
            pending = self._pending(db, run_id)
            return {"schema": "veda.play-telemetry-recovery.v1", "run_id": run_id,
                "runtime_authorized": False, "controller_authorized": False, "must_not_repeat": bool(pending),
                "pending": [{"decision_id": row["id"], "action": json.loads(row["recommendation_json"]),
                    "context": {k: row[k] for k in CONTEXT_KEYS},
                    "source": json.loads(row["payload_json"]).get("request", {}).get("source"),
                    "dispatch_status": "unknown", "required": "inspect and reconcile; do not resend"} for row in pending]}
