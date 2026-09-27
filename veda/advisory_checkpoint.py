"""One bounded, source-bound pause receipt; never an execution authorization.

Uses the existing private ledger and transaction owner. A caller supplies
observations, not a request to discover or reconstruct historical game state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from .telemetry_database import TelemetryDatabase

SCHEMA = "veda.advisory-checkpoint.v1"
MAX_REQUEST_BYTES = 131072
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_AGE_SECONDS = 180
STATE_FIELDS = {"hp", "max_hp", "gold", "deck_size", "energy", "energy_max", "block", "floor", "act", "ascension"}


def _canonical(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("request must be finite JSON") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("checkpoint request exceeds byte limit")
    return encoded


def _time(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if result.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return result.astimezone(timezone.utc)


def _text(value, field, limit=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} requires nonempty bounded text")
    return value


def _check_image(source):
    path = Path(source["path"])
    if not path.is_absolute():
        raise ValueError("source path must be absolute")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ValueError("source image is unavailable") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("source image is empty or exceeds byte limit")
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
        raise ValueError("source must be a saved PNG or JPEG")
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("source hash changed or does not match")


def _validate(request):
    if not isinstance(request, dict) or request.get("schema") != SCHEMA:
        raise ValueError(f"request schema must be {SCHEMA}")
    allowed = {"schema", "operation_id", "run_id", "floor_id", "combat_id", "turn_id", "boundary", "paused_at", "source", "state", "inventory", "expectations", "resume_notes"}
    if set(request) - allowed:
        raise ValueError("unknown checkpoint request fields")
    for key in ("operation_id", "run_id", "floor_id"):
        _text(request.get(key), key, 128)
    try:
        UUID(request["operation_id"])
    except ValueError as exc:
        raise ValueError("operation_id must be a UUID") from exc
    if not isinstance(request.get("boundary"), str) or request["boundary"] not in {"map", "reward", "combat", "event", "controller", "other"}:
        raise ValueError("unsupported checkpoint boundary")
    if request["boundary"] == "combat" and not all(request.get(k) for k in ("combat_id", "turn_id")):
        raise ValueError("combat pause requires combat_id and turn_id")
    if request.get("turn_id") and not request.get("combat_id"):
        raise ValueError("turn_id requires combat_id")
    if request.get("combat_id") and request["boundary"] not in {"combat", "controller", "other"}:
        raise ValueError("an open combat cannot claim a noncombat screen boundary")
    for key in ("combat_id", "turn_id"):
        if request.get(key) is not None:
            _text(request[key], key, 128)
    source = request.get("source")
    if not isinstance(source, dict) or set(source) != {"path", "sha256", "captured_at", "origin", "evidence_note"}:
        raise ValueError("source needs path, sha256, captured_at, origin and evidence_note")
    _text(source["path"], "source path", 4096)
    if not isinstance(source["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        raise ValueError("source sha256 must be lowercase hexadecimal")
    if not isinstance(source["origin"], str) or source["origin"] not in {"reviewer", "reader"}:
        raise ValueError("source origin must be reviewer or reader")
    _text(source["evidence_note"], "source evidence note")
    captured, paused = _time(source["captured_at"], "captured_at"), _time(request.get("paused_at"), "paused_at")
    if captured > paused:
        raise ValueError("capture cannot follow pause time")
    named = re.search(r"ps5_observation_(\d{8}T\d{6}(?:\.\d+)?)Z", Path(source["path"]).name)
    if named:
        fmt = "%Y%m%dT%H%M%S.%f" if "." in named[1] else "%Y%m%dT%H%M%S"
        if datetime.strptime(named[1], fmt).replace(tzinfo=timezone.utc) != captured:
            raise ValueError("capture timestamp disagrees with source filename")
    state = request.get("state")
    if not isinstance(state, dict) or set(state) - STATE_FIELDS:
        raise ValueError("state must contain only supported observed numeric fields")
    for key, value in state.items():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"state {key} must be a nonnegative integer or null")
    if state.get("hp") is not None and state.get("max_hp") is not None and state["hp"] > state["max_hp"]:
        raise ValueError("HP exceeds maximum HP")
    if state.get("ascension") is not None and state["ascension"] > 20:
        raise ValueError("invalid Ascension")
    inventory = request.get("inventory")
    if inventory is not None:
        if not isinstance(inventory, dict) or set(inventory) != {"items", "coverage", "evidence"}:
            raise ValueError("inventory needs items, coverage and evidence")
        coverage, evidence = inventory["coverage"], inventory["evidence"]
        if not isinstance(coverage, dict) or set(coverage) != {"card", "relic", "potion"} or any(not isinstance(v, str) or v not in {"complete", "partial", "unknown"} for v in coverage.values()):
            raise ValueError("inventory coverage must describe all three categories")
        if not isinstance(evidence, dict) or set(evidence) != {k for k, v in coverage.items() if v != "unknown"}:
            raise ValueError("each observed inventory category needs its own evidence note")
        for value in evidence.values():
            _text(value, "inventory evidence")
        if not isinstance(inventory["items"], list) or len(inventory["items"]) > 300:
            raise ValueError("inventory items must be a bounded list")
        relics = set()
        for item in inventory["items"]:
            if not isinstance(item, dict) or set(item) - {"kind", "item", "property"} or not isinstance(item.get("kind"), str) or item["kind"] not in coverage:
                raise ValueError("invalid inventory item")
            _text(item.get("item"), "inventory item", 128)
            if item.get("property") is not None:
                _text(item["property"], "inventory property")
            if coverage[item["kind"]] == "unknown":
                raise ValueError("unknown category cannot assert inventory items")
            if item["kind"] == "relic":
                name = item["item"].strip().casefold()
                if name in relics:
                    raise ValueError("duplicate relic")
                relics.add(name)
        if coverage["card"] == "complete" and state.get("deck_size") is not None:
            if sum(i["kind"] == "card" for i in inventory["items"]) != state["deck_size"]:
                raise ValueError("complete card inventory disagrees with observed deck size")
    expectations = request.get("expectations", [])
    if not isinstance(expectations, list) or len(expectations) > 20:
        raise ValueError("expectations must be a bounded list")
    for item in expectations:
        if not isinstance(item, dict) or set(item) != {"claim", "basis"}:
            raise ValueError("unconfirmed expectations need claim and basis")
        _text(item["claim"], "expectation claim")
        _text(item["basis"], "expectation basis")
    notes = request.get("resume_notes", [])
    if not isinstance(notes, list) or len(notes) > 20:
        raise ValueError("resume_notes must be a bounded text list")
    for note in notes:
        _text(note, "resume note")
    return captured, paused


def record_advisory_checkpoint(database: TelemetryDatabase, request: dict, *, now: datetime | None = None) -> dict:
    """Atomically save a pause, observed inventory baseline and compact receipt.

    Reader/reviewer facts are caller assertions linked to exact bytes, not newly
    recognized by this API. ``now`` is an injectable test clock, not a CLI flag.
    Stale/future evidence is rejected; a committed retry returns a historical
    receipt without turning it into fresh advice.
    """
    raw = _canonical(request)
    request = json.loads(raw)  # Freeze caller-owned mutable input.
    captured, paused = _validate(request)
    digest = hashlib.sha256(raw).hexdigest()
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("clock must be timezone-aware")
    if not database.path.is_file():
        raise ValueError("checkpoint requires an existing run database")
    with database._transaction():
        with database._connection() as db:
            previous = db.execute("SELECT payload_json FROM session_checkpoints WHERE run_id=? AND json_extract(payload_json,'$.checkpoint_operation_id')=?", (request["run_id"], request["operation_id"])).fetchone()
            if previous:
                payload = json.loads(previous[0])
                if payload["request_sha256"] != digest:
                    raise ValueError("operation_id was already used with different content")
                return {**payload["receipt"], "idempotent_replay": True}
            if paused > clock or captured > clock:
                raise ValueError("future source or pause timestamp")
            if (clock - captured).total_seconds() > MAX_AGE_SECONDS:
                raise ValueError("source is stale; obtain a new observation or retain it separately as historical evidence")
            run = db.execute("SELECT status,ascension,started_at FROM runs WHERE id=?", (request["run_id"],)).fetchone()
            if run is None or run["status"] != "active":
                raise ValueError("checkpoint requires the exact active run")
            if captured < _time(run["started_at"], "run started_at"):
                raise ValueError("source predates this run")
            floor = db.execute("SELECT id,run_id,act,floor FROM floors WHERE run_id=? ORDER BY recorded_at DESC,rowid DESC LIMIT 1", (request["run_id"],)).fetchone()
            if floor is None or floor["id"] != request["floor_id"]:
                raise ValueError("checkpoint must identify the latest recorded floor of this run")
            for key, value in (("floor", floor["floor"]), ("act", floor["act"]), ("ascension", run["ascension"])):
                if request["state"].get(key) is not None and request["state"][key] != value:
                    raise ValueError(f"observed {key} conflicts with recorded context")
            database._validate_event_context(db, run_id=request["run_id"], floor_id=request["floor_id"], combat_id=request.get("combat_id"), turn_id=request.get("turn_id"))
            if request.get("combat_id"):
                combat = db.execute("SELECT id,closed_at FROM combats WHERE run_id=? ORDER BY opened_at DESC,rowid DESC LIMIT 1", (request["run_id"],)).fetchone()
                turn = db.execute("SELECT id,closed_at,opened_at FROM combat_turns WHERE combat_id=? ORDER BY turn_number DESC LIMIT 1", (request["combat_id"],)).fetchone()
                if combat["id"] != request["combat_id"] or combat["closed_at"] or turn is None or turn["closed_at"] or turn["id"] != request.get("turn_id"):
                    raise ValueError("combat checkpoint needs its latest open turn")
                if captured < _time(turn["opened_at"], "turn opened_at"):
                    raise ValueError("source predates this combat turn")
            elif db.execute("SELECT 1 FROM combats WHERE run_id=? AND closed_at IS NULL LIMIT 1", (request["run_id"],)).fetchone():
                raise ValueError("an open combat must be explicitly reconciled before a noncombat pause")
            last = db.execute("SELECT observed_at,kind FROM session_checkpoints WHERE run_id=? ORDER BY julianday(observed_at) DESC,rowid DESC LIMIT 1", (request["run_id"],)).fetchone()
            if last:
                previous_time = _time(last[0], "prior checkpoint time")
                if paused <= previous_time or captured < previous_time:
                    raise ValueError("source and pause must follow the previous checkpoint")
                if last["kind"] == "pause":
                    raise ValueError("run is already paused")
            if request.get("inventory") is not None:
                changes = [db.execute(f"SELECT observed_at FROM {table} WHERE run_id=? ORDER BY julianday(observed_at) DESC,rowid DESC LIMIT 1", (request["run_id"],)).fetchone() for table in ("inventory_events", "inventory_baselines")]
                if any(r is not None and _time(r[0], "inventory time") > captured for r in changes):
                    raise ValueError("inventory changed after the supplied observation")
        _check_image(request["source"])
        label = f"checkpoint:{request['source']['origin']}"
        inventory_id = None
        if request.get("inventory") is not None:
            inventory_id = database.record_inventory_baseline(run_id=request["run_id"], floor_id=request["floor_id"], items=request["inventory"]["items"], coverage=request["inventory"]["coverage"], source=label, screenshot_path=request["source"]["path"])
            with database._connection() as db:
                db.execute("UPDATE inventory_baselines SET observed_at=? WHERE id=?", (captured.isoformat(), inventory_id))
        payload = {"schema": SCHEMA, "checkpoint_operation_id": request["operation_id"], "request_sha256": digest,
                   "turn_id": request.get("turn_id"),
                   "source": request["source"], "inventory_evidence": request.get("inventory"),
                   "unconfirmed_expectations": request.get("expectations", []), "resume_notes": request.get("resume_notes", []),
                   "recorded_at": clock.isoformat(), "controller_authorized": False,
                   "runtime_authorized": False, "requires_new_observation_on_resume": True}
        checkpoint_id = database.record_session_checkpoint(run_id=request["run_id"], floor_id=request["floor_id"], combat_id=request.get("combat_id"), kind="pause", boundary=request["boundary"], state=request["state"], payload=payload, source=label, screenshot_path=request["source"]["path"], observed_at=paused.isoformat())
        receipt = {"schema": "veda.advisory-checkpoint-receipt.v1", "saved": True, "checkpoint_id": checkpoint_id,
                   "operation_id": request["operation_id"], "run_id": request["run_id"], "floor_id": request["floor_id"],
                   "combat_id": request.get("combat_id"), "turn_id": request.get("turn_id"),
                   "paused_at": paused.isoformat(), "captured_at": captured.isoformat(), "source_sha256": request["source"]["sha256"],
                   "inventory_baseline_id": inventory_id, "inventory_coverage": (request.get("inventory") or {}).get("coverage"),
                   "observed_state": request["state"], "resume_notes": request.get("resume_notes", []),
                   "unconfirmed_expectations": request.get("expectations", []), "idempotent_replay": False,
                   "controller_authorized": False, "runtime_authorized": False, "requires_new_observation_on_resume": True}
        payload["receipt"] = receipt
        with database._connection() as db:
            db.execute("UPDATE session_checkpoints SET payload_json=? WHERE id=?", (json.dumps(payload, sort_keys=True), checkpoint_id))
        _check_image(request["source"])  # A changed/deleted file rolls back all writes.
        return receipt
