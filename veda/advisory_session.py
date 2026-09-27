"""Persistent, compact checked advice. No capture, model, controller or SQLite I/O.

The session verifies new image bytes once on ingestion, retains immutable copies,
and checks only evidence supporting current facts for each decision. Historical
events are replayed once at startup, not after every card. Reviewer declarations
remain declarations, never automatic recognition or runtime authorization.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .evidence_advisory import _evaluate_snapshot, verify_sources
from .evidence_ledger import EvidenceLedger
from .saved_frame_reader import _identity

SCHEMA = "veda.advisory-session.v1"
MAX_REQUEST_BYTES = 1_000_000
MAX_STATE_BYTES = 8_000_000
MAX_FRAME_BYTES = 32_000_000
MAX_REQUESTS = 256
MAX_SOURCE_BYTES = 512_000_000


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _read(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError("session input exceeds its byte limit")
    return data


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AdvisorySession:
    def __init__(self, directory, *, context=None, resume=False, mode="review"):
        if mode not in ("review", "replay"):
            raise ValueError("session mode must be review or replay")
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (self.directory/"session.lock").open("a")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.mode = mode
            self.sources = {}
            self.pending = None
            self.last_action = None
            self.receipts = {}
            self.closed = False
            self.poisoned = False
            self.needs_reconciliation = resume
            state_path = self.directory/"session.json"
            if resume:
                saved = json.loads(_read(state_path, MAX_STATE_BYTES))
                if (saved.get("schema") != SCHEMA or saved.get("mode") != mode
                        or saved.get("controller_authorized") is not False
                        or saved.get("runtime_authorized") is not False):
                    raise ValueError("invalid session or changed review/replay mode")
                self.ledger = EvidenceLedger.from_dict(saved["journal"])
                if context is not None and context != self.ledger.context:
                    raise ValueError("resume context differs from stored run/floor/combat/turn")
                self.sources = saved["sources"]
                if not isinstance(self.sources, dict) or len(self.sources) > MAX_REQUESTS:
                    raise ValueError("invalid bounded session sources")
                for digest, name in self.sources.items():
                    if not re.fullmatch(r"[0-9a-f]{64}", digest) or name != f"sources/{digest}.png":
                        raise ValueError("invalid retained source path")
                verify_sources(saved["journal"], self._source_paths())
                self.pending, self.last_action = saved.get("pending"), saved.get("last_action")
                self.receipts = saved["receipts"]
                if not isinstance(self.receipts, dict) or len(self.receipts) > MAX_REQUESTS:
                    raise ValueError("invalid bounded session receipts")
                if self.pending is not None:
                    if (not isinstance(self.pending, dict) or not isinstance(self.pending.get("action_id"), str)
                            or self.pending.get("epoch") != self.ledger.epoch
                            or self.pending.get("context") != self.ledger.context
                            or self.pending.get("frame") != self.ledger.frame
                            or not isinstance(self.pending.get("plan"), dict)):
                        raise ValueError("invalid pending action binding")
            else:
                if state_path.exists():
                    raise ValueError("session exists; use explicit resume")
                self.ledger = EvidenceLedger(**(context or {}))
                self._save()
        except BaseException:
            self._lock.close()
            raise

    def _source_paths(self):
        return {digest: str(self.directory/name) for digest, name in self.sources.items()}

    def _save(self):
        data = _encoded({"schema": SCHEMA, "mode": self.mode, "journal": self.ledger.dump(),
                         "sources": self.sources, "pending": self.pending,
                         "last_action": self.last_action, "receipts": self.receipts,
                         "controller_authorized": False, "runtime_authorized": False})
        if len(data) > MAX_STATE_BYTES:
            raise ValueError("session storage bound reached; preserve this session before starting another")
        temporary = self.directory/"session.json.tmp"
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory/"session.json")
            _sync_directory(self.directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _ingest(self, source, digest):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("a SHA256-bound source is required")
        raw = _read(source, MAX_FRAME_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("offered image differs from observation hash")
        name = f"sources/{digest}.png"
        target = self.directory/name
        target.parent.mkdir(exist_ok=True)
        _sync_directory(self.directory)
        if not target.exists():
            stored = sum(p.stat().st_size for p in target.parent.iterdir() if p.is_file())
            if stored+len(raw) > MAX_SOURCE_BYTES:
                raise ValueError("session image-storage bound reached")
            with target.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(target.parent)
        actual, _ = _identity(target)
        if actual != digest:
            raise ValueError("retained source changed")
        self.sources[digest] = name

    def _verify_current(self, snapshot):
        digests = set()
        if snapshot["frame"]:
            digests.add(snapshot["frame"]["image_sha256"])
        for name, section in snapshot["sections"].items():
            if section["current"]:
                for proof in section["evidence"]:
                    if name == "inventory" or (proof["epoch"] == snapshot["epoch"]
                                               and proof["context"] == snapshot["context"]):
                        digests.add(proof["frame"]["image_sha256"])
        for digest in sorted(digests):
            if digest not in self.sources or _identity(self.directory/self.sources[digest])[0] != digest:
                raise ValueError("current evidence source is missing or changed")
        return sorted(digests)

    def _compact(self):
        snapshot = self.ledger.snapshot()
        sections = snapshot["sections"]
        def known(name):
            section = sections[name]
            return deepcopy(section["data"]) if section["current"] else None
        inventory = known("inventory")
        if inventory:
            inventory = {"coverage": inventory["coverage"],
                         "relics": inventory["current"]["relic"],
                         "potions": inventory["current"]["potion"],
                         "deck_count": len(inventory["current"]["card"])}
        frame = snapshot["frame"]
        age = None if frame is None else (datetime.now(timezone.utc)-datetime.fromisoformat(frame["observed_at"])).total_seconds()
        result = {"schema": SCHEMA, "mode": self.mode, "context": snapshot["context"],
                "epoch": snapshot["epoch"], "frame": frame, "capture_age_seconds": age,
                "player": known("player"), "hand": known("hand"), "enemies": known("enemies"),
                "statuses": known("statuses"), "ui": known("ui"), "inventory": inventory,
                "pile_coverage": (known("piles") or {}).get("coverage"),
                "missing": snapshot["inspection_requirements"],
                "pending": deepcopy(self.pending), "last_action": deepcopy(self.last_action),
                "controller_authorized": False, "runtime_authorized": False,
                "automatic_recognition_complete": False,
                "requires_resynchronization": self.needs_reconciliation,
                "restart_required": self.poisoned,
                "freshness_note": "Stored facts are historical until a fresh source-bound observation passes the checker."}
        if self.poisoned or self.needs_reconciliation:
            for key in ("player", "hand", "enemies", "statuses", "ui", "inventory", "pile_coverage"):
                result[key] = None
            result["freshness_note"] = "Resynchronize after restart or persistence uncertainty; old facts cannot support another action."
        return result

    def handle(self, request):
        if self.closed:
            raise ValueError("session is closed")
        if not isinstance(request, dict) or len(_encoded(request)) > MAX_REQUEST_BYTES:
            raise ValueError("bounded request object required")
        operation = request.get("operation")
        if self.poisoned:
            raise ValueError("session persistence is uncertain; close, resume, and resynchronize before continuing")
        if operation == "summary":
            self._verify_current(self.ledger.snapshot())
            return self._compact()
        ident = request.get("request_id")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 120:
            raise ValueError("mutating/check requests need a bounded unique request_id")
        digest = hashlib.sha256(_encoded(request)).hexdigest()
        if ident in self.receipts:
            receipt = self.receipts[ident]
            if receipt["request_sha256"] != digest:
                raise ValueError("request ID reused with different contents")
            self._verify_current(self.ledger.snapshot())
            # A retry confirms prior processing, never reissues an old recommendation.
            return {"request_id": ident, "already_recorded": True,
                    "original_operation": receipt["operation"], "summary": self._compact(),
                    "advisory_ready": False,
                    "controller_authorized": False, "runtime_authorized": False}
        if len(self.receipts) >= MAX_REQUESTS:
            raise ValueError("session request bound reached; preserve this session before starting another")
        before = (deepcopy(self.ledger), deepcopy(self.sources), deepcopy(self.pending),
                  deepcopy(self.last_action), deepcopy(self.receipts), self.needs_reconciliation)
        began = time.monotonic()
        persisting = False
        try:
            extra = self._perform(request)
            self.receipts[ident] = {"request_sha256": digest, "operation": operation}
            evaluated = time.monotonic()
            persisting = True
            self._save()
            return {"request_id": ident, **extra, "summary": self._compact(),
                    "timing_ms": {"evaluate": (evaluated-began)*1000,
                                  "persist": (time.monotonic()-evaluated)*1000},
                    "controller_authorized": False, "runtime_authorized": False}
        except BaseException:
            self.ledger, self.sources, self.pending, self.last_action, self.receipts, self.needs_reconciliation = before
            if persisting:
                # A reported physical input cannot be undone by rolling back memory.
                # Every restart also requires resynchronization, even if disk kept
                # the older file or replacement succeeded before an I/O exception.
                self.poisoned = self.needs_reconciliation = True
            raise

    def _perform(self, request):
        operation = request["operation"]
        if self.needs_reconciliation and operation != "unlogged_input":
            raise ValueError("resumed sessions need unlogged_input resynchronization, then fresh observations and inventory")
        if operation == "observe":
            offered = request["observation"]
            if self.pending is not None:
                raise ValueError("report whether the pending action was performed before observing its outcome")
            self._ingest(request["source"], offered["image_sha256"])
            self.ledger.observe(offered)
            if request.get("outcome_review") is not None:
                review = request["outcome_review"]
                action = self.last_action
                if (not isinstance(review, dict) or not action or action.get("status") != "user_reported"
                        or action.get("outcome_verified") is not False
                        or review.get("action_id") != action.get("action_id")
                        or type(review.get("matches_expected")) is not bool
                        or any(not isinstance(review.get(k), str) or not review[k].strip()
                               for k in ("reviewer", "evidence", "actual_action"))):
                    raise ValueError("outcome review needs the exact reported action and named inspected evidence")
                if (offered["frame_id"] == action["frame"]["frame_id"]
                        or datetime.fromisoformat(offered["observed_at"]) <= datetime.fromisoformat(action["frame"]["observed_at"])):
                    raise ValueError("outcome needs a later independent frame")
                before_context, after_context = action["context"], offered["context"]
                if any(before_context[k] != after_context[k] for k in ("run_id", "floor_id", "combat_id")):
                    raise ValueError("outcome belongs to a different run, floor or combat")
                turn_changed = before_context["turn_id"] != after_context["turn_id"]
                is_end_turn = action["plan"]["steps"][0].get("kind") == "end_turn"
                if turn_changed and (not is_end_turn or after_context["turn_id"] is None):
                    raise ValueError("unexpected turn change needs explicit unlogged-input reconciliation")
                if is_end_turn and review["matches_expected"] and (not turn_changed or after_context["turn_id"] is None):
                    raise ValueError("successful End Turn outcome needs a distinct observed turn")
                self.last_action.update(outcome_verified=True, outcome_review=deepcopy(review),
                                        after_frame=deepcopy(self.ledger.frame),
                                        verification_basis="reviewer declaration bound to before/after image hashes")
                if not review["matches_expected"]:
                    self.ledger.invalidate(kind="unknown_input", reason="reported action did not match observed outcome; reconcile current state")
        elif operation == "inventory":
            if self.pending is not None:
                raise ValueError("resolve the pending action before changing inventory evidence")
            offered = request["receipt"]
            self._ingest(request["source"], offered["image_sha256"])
            self.ledger.record_inventory_event(offered)
        elif operation == "advise":
            if self.pending is not None:
                raise ValueError("an action is pending; do not recommend another or silently repeat it")
            if self.last_action and self.last_action.get("status") == "user_reported" and self.last_action.get("outcome_verified") is not True:
                raise ValueError("inspect and reconcile the reported action outcome before another recommendation")
            plan = request["plan"]
            if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or len(plan["steps"]) != 1:
                raise ValueError("this session publishes exactly one checked action")
            if self.mode == "review" and request.get("as_of") is not None:
                raise ValueError("historical clocks cannot refresh a review session")
            snapshot = self.ledger.snapshot()
            sources = self._verify_current(snapshot)
            result = _evaluate_snapshot(snapshot, plan=plan, mode=self.mode, as_of=request.get("as_of"))
            if self._verify_current(snapshot) != sources:
                raise ValueError("source identity changed during advice")
            if result["advisory_ready"]:
                self.pending = {"action_id": request["request_id"], "plan": deepcopy(plan),
                                "context": deepcopy(self.ledger.context), "epoch": self.ledger.epoch,
                                "frame": deepcopy(self.ledger.frame), "status": "recommended_only"}
            return {"advisory_ready": result["advisory_ready"], "checked_plan": result["checked_plan"],
                    "blockers": result["context"]["unknowns"], "inspections": result["inspections"]["requests"],
                    "rules": result["context"]["rules"], "verified_source_count": len(sources),
                    "historical_replay_only": self.mode == "replay"}
        elif operation == "action_reported":
            if self.pending is None or request.get("action_id") != self.pending["action_id"]:
                raise ValueError("the exact pending action ID is required")
            if type(request.get("performed")) is not bool or not isinstance(request.get("evidence"), str) or not request["evidence"].strip():
                raise ValueError("explicit performed/skipped report and evidence are required")
            self.last_action = {**deepcopy(self.pending), "status": "user_reported" if request["performed"] else "skipped",
                                "evidence": request["evidence"], "outcome_verified": False}
            if request["performed"]:
                kind = "inventory_change" if self.pending["plan"]["steps"][0].get("kind") == "potion" else "input"
                self.ledger.invalidate(kind=kind, reason="reported action "+self.pending["action_id"]+"; inspect outcome")
            self.pending = None
        elif operation == "unlogged_input":
            self.ledger.invalidate(kind="unknown_input", reason=request["reason"])
            self.last_action = {"status": "unknown_input", "evidence": request["reason"], "outcome_verified": False,
                                "superseded_pending": deepcopy(self.pending)}
            self.pending = None
            self.needs_reconciliation = False
        elif operation == "change_context":
            if self.pending is not None:
                raise ValueError("resolve the pending action before changing floor/combat/turn")
            self.ledger.change_context(**request["context"], reason=request["reason"])
        else:
            raise ValueError("unknown advisory session operation")
        return {"recorded_operation": operation}

    def close(self):
        if not self.closed:
            self.closed = True
            self._lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
