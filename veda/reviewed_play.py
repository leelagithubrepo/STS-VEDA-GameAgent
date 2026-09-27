"""Durable, one-input execution for the active Codex Orchestrator.

The named reviewer supplies pixel interpretation and strategy. This is not an
automatic recognizer, a calibration bypass, or a background game-playing agent.
Every input requires a new source-bound review; no unchecked button API exists.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .advisory import check_plan
from .choice_execution import (plan_choice_step, validate_choice_proposal, verify_choice_step,
                               _observation as validate_choice_observation)
from .combat_input import CombatInputAdapter, RuntimeStop
from .controller_state_machine import ControllerStateMachine
from .execution import ARM_PHRASE, Reading
from .routine_combat import routine_observation_reasons
from .saved_frame_reader import _identity

MAX_BYTES = 1_000_000
MAX_AGE = 30
SCHEMA = "veda.reviewed-play.v1"


def _copy(value):
    data = json.dumps(value, allow_nan=False)
    if len(data.encode()) > MAX_BYTES:
        raise ValueError("reviewed play request exceeds byte limit")
    return json.loads(data)


def _require(condition, message):
    if not condition:
        raise RuntimeStop(message)


def _time(value):
    result = datetime.fromisoformat(value)
    _require(result.tzinfo is not None, "capture timestamp needs a timezone")
    return result


def _bridge_readiness_problem(response, request_id):
    """Return only fixed diagnostic codes; never echo SDK/device identifiers."""
    if not isinstance(response, dict):
        return "invalid_status_response"
    if response.get("request_id") != request_id:
        return "status_request_mismatch"
    if response.get("ok") is not True or response.get("status") != "ok":
        known = {"connect_failed", "command_timeout", "session_stopped", "controller_transport_failed",
                 "response_unconfirmed", "client_closed", "reconciliation_required", "transport_unavailable"}
        error = response.get("error")
        return error if isinstance(error, str) and error in known else "status_not_confirmed"
    if response.get("action") != "status" or response.get("readiness_source") != "owned_live_transport":
        return "unsupported_readiness_contract"
    if response.get("session_ready") is not True:
        return "session_not_ready"
    health = response.get("health")
    if not isinstance(health, dict):
        return "transport_health_missing"
    if health.get("transport_ready") is not True:
        return "transport_not_ready"
    if health.get("refresh_running") is not True:
        return "refresh_not_running"
    if health.get("error_present") is not False or "error_code" not in health or health["error_code"] is not None:
        return "transport_health_fault_or_unknown"
    return None


def inventory_digest(inventory):
    return hashlib.sha256(json.dumps(_inventory_semantics(inventory), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _inventory_semantics(inventory):
    return {"current": {k: sorted(inventory.get("current", {}).get(k, [])) for k in ("card", "relic", "potion")},
            "coverage": {k: inventory.get("coverage", {}).get(k, "unknown") for k in ("card", "relic", "potion")},
            "properties": {k: v["property"] for k, v in inventory.get("properties", {}).items()}}


class ReviewedPlaySession(CombatInputAdapter):
    """One exclusive session, one pending input, no implicit input or retry.

    ``controller_factory`` opens the existing warm bridge only when arm() runs.
    Offline tests use a fake factory; shadow mode cannot arm or call it.
    """
    def __init__(self, directory, *, run_id, telemetry, controller_factory=None,
                 mode="shadow", clock=None):
        _require(mode in {"shadow", "codex"}, "mode must be shadow or codex")
        _require(isinstance(run_id, str) and run_id.strip(), "existing run ID required")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory / "session.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeStop("reviewed play session already has an owner")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.mode, self.telemetry, self.factory = mode, telemetry, controller_factory
        self.machine = ControllerStateMachine()
        self.controller = None
        self.armed = False
        self.closed = False
        self.poisoned = False
        self.cleanup = None
        self.path = self.directory / "state.json"
        try:
            if self.path.exists():
                _require(self.path.stat().st_size <= MAX_BYTES, "session state exceeds byte limit")
                self.state = json.loads(self.path.read_text())
                _require(self.state.get("schema") == SCHEMA and self.state.get("run_id") == run_id,
                         "session belongs to another schema or run")
            else:
                self.state = {"schema": SCHEMA, "run_id": run_id, "pending": None, "completed": 0}
                self._save()
        except BaseException:
            self.lock.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _save(self):
        data = json.dumps(_copy(self.state), sort_keys=True).encode()
        temporary = self.directory / (".state-" + uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            self.poisoned = True
            self.disarm()
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def _source(self, source, *, fresh=True, retain=False):
        source = _copy(source)
        _require(source.get("origin") == "reviewer" and bool(source.get("evidence_note")),
                 "explicit reviewed source and limitations required")
        observed = _time(source["captured_at"])
        if fresh:
            _require(0 <= (self.clock() - observed).total_seconds() <= MAX_AGE,
                     "reviewed image is stale or future dated; capture and inspect again")
        path = Path(source["path"]).resolve()
        digest, _ = _identity(path)
        _require(digest == source["sha256"], "reviewed source bytes changed")
        # Preserve original filenames: telemetry validates capture-name timestamps.
        if retain:
            directory = self.directory / "sources" / digest
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / path.name
            if not destination.exists():
                shutil.copyfile(path, destination)
                with destination.open("rb") as stream:
                    os.fsync(stream.fileno())
                for parent in (directory, directory.parent, self.directory):
                    descriptor = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            _require(_identity(destination)[0] == digest and _identity(path)[0] == digest,
                     "source changed during retention")
            source["path"] = str(destination)
        return source

    def _review(self, review, source, frame_id):
        _require(isinstance(review, dict) and review.get("complete") is True
                 and isinstance(review.get("reviewer"), str) and bool(review["reviewer"].strip())
                 and review.get("frame_id") == frame_id
                 and review.get("image_sha256") == source["sha256"],
                 "complete named review must bind the actual source")

    def _before(self, request, *, check_plan_now=True):
        source = self._source(request["source"])
        context = request["context"]
        _require(set(context) == {"run_id", "floor_id", "combat_id", "turn_id"}
                 and context["run_id"] == self.state["run_id"], "exact current run context required")
        if request["kind"] == "choice":
            observation = request["observation"]
            _require(observation["context"] == context, "choice context differs")
            frame = observation["frame"]
            _require(frame["image_sha256"] == source["sha256"]
                     and frame["observed_at"] == source["captured_at"], "choice frame differs from saved source")
            inventory = request["inventory"]
            title_continue = (observation["ui"]["screen"] == "title"
                              and request.get("choice", {}).get("kind") == "continue_run")
            _require((title_continue or all(inventory.get("coverage", {}).get(k) == "complete" for k in ("relic", "potion")))
                     and inventory_digest(inventory) == observation["inventory_digest"],
                     "complete reviewed inventory must match choice digest")
            validate_choice_observation(observation, self.clock(), MAX_AGE)
            return observation
        _require(request["kind"] == "combat", "unknown reviewed play kind")
        reading = Reading.from_dict(request["reading"])
        _require((reading.run_id, reading.floor_id, reading.turn_id) ==
                 (context["run_id"], context["floor_id"], context["turn_id"])
                 and context["combat_id"] is not None, "combat reading context differs")
        _require(reading.image_sha256 == source["sha256"]
                 and reading.context["state"]["observed_at"] == source["captured_at"],
                 "combat reading differs from saved source")
        self._review(request["review"], source, reading.frame_id)
        reasons = routine_observation_reasons(reading.state, reading.context)
        _require(not reasons, "incomplete reviewed combat: " + "; ".join(reasons))
        if check_plan_now:
            plan = request["plan"]
            _require(len(plan.get("steps", [])) == 1 and plan["steps"][0].get("kind") in {"card", "end_turn"},
                     "exactly one card or End Turn; potions use the choice flow")
            checked = check_plan(reading.context, plan)
            _require(checked["allowed"], "move rejected: " + "; ".join(checked["reasons"]))
        return reading

    def summary(self):
        recovery = self.telemetry.recover(run_id=self.state["run_id"])
        pending = self.state["pending"]
        brief = None if pending is None else {
            "action_id": pending["action_id"], "status": pending["status"],
            "command": pending["command"], "context": pending["request"]["context"],
            "source": pending["request"]["source"], "kind": pending["request"]["kind"],
            "decision_id": pending.get("decision_id"), "must_not_repeat": pending["status"] != "prepared"}
        # Durable full requests stay on disk. Repeated operator summaries must
        # not re-send whole hands, menu contracts or historical telemetry.
        recovery = {**recovery, "pending": [{k: row[k] for k in
            ("decision_id", "context", "source", "dispatch_status", "required")} for row in recovery["pending"]]}
        return {"schema": SCHEMA, "mode": self.mode, "run_id": self.state["run_id"],
                "armed": self.armed, "pending": deepcopy(brief),
                "recovery": recovery, "automatic_recognition_complete": False,
                "runtime_authorized": False, "completed_inputs": self.state["completed"],
                "cleanup": deepcopy(self.cleanup),
                "last_bridge_preflight": deepcopy(self.state.get("last_bridge_preflight"))}

    def arm(self, request):
        _require(not self.closed and not self.poisoned and self.mode == "codex", "shadow/closed/failed sessions cannot arm")
        _require(not self.armed and not self.state["pending"], "pending input or already armed")
        _require(request.get("phrase") == ARM_PHRASE and request.get("run_id") == self.state["run_id"],
                 "current-run user arming required")
        source = self._source(request["source"], retain=True)
        self._review(request["review"], source, request["frame_id"])
        _require(request.get("game") == "Slay the Spire" and request.get("screen") in
                 {"title_continue", "combat", "map", "reward", "rest", "event", "shop", "selection"}
                 and request.get("exclusive_client_confirmed") is True,
                 "review the game identity/screen and exclusive controller client")
        recovery = self.telemetry.recover(run_id=self.state["run_id"])
        _require(not recovery.get("pending"), "unresolved database decision requires reconciliation")
        _require(self.factory is not None, "existing warm bridge adapter required")
        request_id = str(uuid4())
        exception_code = "bridge_client_initialization_failed"
        try:
            self.controller = self.factory()
            exception_code = "bridge_status_exception"
            response = self.controller.call({"action": "status", "request_id": request_id})
            problem = _bridge_readiness_problem(response, request_id)
        except Exception:
            problem = exception_code
        self.state["last_bridge_preflight"] = {"checked_at": self.clock().isoformat(),
            "request_id": request_id, "ready": problem is None, "reason": problem,
            "controller_input_sent": False}
        self._save()
        if problem is not None:
            self.disarm()
            raise RuntimeStop(f"warm bridge is not ready ({problem}); no gameplay input sent") from None
        self.armed = True
        self.cleanup = None
        return {"status": "armed_codex_reviewed", "run_id": self.state["run_id"],
                "automatic_recognition_complete": False, "runtime_authorized": False}

    def prepare(self, request):
        _require(not self.closed and not self.poisoned and not self.state["pending"], "resolve pending input or reopen failed session")
        _require(self.state["completed"] < 10000, "session input limit reached")
        request = _copy(request)
        before = self._before(request)
        action_id = str(uuid4())
        if request["kind"] == "choice":
            proposal = plan_choice_step(before, request["choice"], now=self.clock(),
                                        max_age_seconds=MAX_AGE, action_id=action_id)
            command = proposal["command"]
            semantic = {"kind": "navigation" if proposal["step_kind"] != "commit" else "choice",
                        "choice": request["choice"], "step_kind": proposal["step_kind"]}
        else:
            self.machine.reset()
            action = request["plan"]["steps"][0]
            step, expected = self._input(before, action)
            command = {"action": "tap", "buttons": step["buttons"], "request_id": action_id}
            proposal = {"expected": expected, "checked": check_plan(before.context, request["plan"])}
            semantic = {"kind": "navigation" if expected["kind"] in {"clear", "card_focus", "target_focus"}
                        else action["kind"], "requested_action": action, "expected": expected}
        request["source"] = self._source(request["source"], retain=True)
        self.state["pending"] = {"action_id": action_id, "status": "prepared", "request": request,
                                 "proposal": proposal, "command": command, "semantic": semantic}
        self._save()
        return {"status": "prepared", "action_id": action_id, "command": command,
                "mode": self.mode, "controller_input_sent": False}

    def resume(self, request):
        _require(not self.state["pending"], "reconcile pending input before resume")
        value = _copy(request["telemetry"])
        _require(value["context"]["run_id"] == self.state["run_id"], "resume belongs to another run")
        value["source"] = self._source(value["source"], retain=True)
        self._review(request["review"], value["source"], request["frame_id"])
        return self.telemetry.record_resume(value, now=self.clock())

    def cancel_prepared(self):
        _require(self.state["pending"] and self.state["pending"]["status"] == "prepared",
                 "only an input that never crossed dispatch can be cancelled")
        self.state["pending"] = None
        self._save()
        return {"status": "cancelled_without_input"}

    def send(self, action_id):
        _require(self.armed and self.controller is not None and not self.closed and not self.poisoned, "session is not armed")
        pending = self.state["pending"]
        _require(pending and pending["action_id"] == action_id and pending["status"] == "prepared",
                 "input already attempted or proposal unknown; never repeat")
        request = pending["request"]
        before = self._before(request)
        if request["kind"] == "choice":
            validate_choice_proposal(pending["proposal"], before, now=self.clock())
        state = before.context["state"] if request["kind"] == "combat" else {
            "resources": before["resources"], "facts": before["facts"], "ui": before["ui"]}
        decision = {"schema": "veda.play-telemetry.v1", "operation_id": action_id,
            "context": request["context"], "source": request["source"], "state": state,
            "phase": "combat" if request["kind"] == "combat" else before["ui"]["screen"],
            "action": pending["semantic"], "reasoning": request["reasoning"]}
        # Mark ambiguity before the database call too: if the write commits but
        # its return/save fails, recovery must not submit that operation again.
        pending.update(status="preparing_dispatch", decision_request=decision,
                       preparation_started_at=self.clock().isoformat())
        self._save()
        try:
            receipt = self.telemetry.record_decision(decision, now=self.clock())
            pending.update(status="attempted", decision_id=receipt["decision_id"],
                           attempted_at=self.clock().isoformat())
            self._save()
            self._source(request["source"])
            response = self.controller.call(pending["command"])
            pending["response"] = response
            self._save()
            _require(response.get("ok") is True and response.get("status", "ok") == "ok"
                     and response.get("request_id") == action_id,
                     "controller delivery uncertain; inspect and reconcile, never resend")
        except BaseException:
            self.disarm()
            raise
        return {"status": "awaiting_fresh_review", "action_id": action_id,
                "controller_input_sent": True, "game_outcome_verified": False}

    def verify(self, request):
        pending = self.state["pending"]
        _require(pending and pending["status"] == "attempted" and
                 request.get("action_id") == pending["action_id"], "no matching attempted input")
        after = _copy(request["after"])
        after["source"] = self._source(after["source"], retain=True)
        before = pending["request"]
        _require(after["source"]["sha256"] != before["source"]["sha256"]
                 and _time(after["source"]["captured_at"]) > _time(pending["attempted_at"]),
                 "outcome needs a distinct image captured after dispatch")
        observed = self._before(after, check_plan_now=False)
        if before["kind"] == "choice":
            _require(after["kind"] == "choice", "choice verification needs the reviewed choice-result contract")
            verified = verify_choice_step(pending["proposal"], before["observation"], observed, now=self.clock())
            complete = verified["choice_complete"]
            state = {"resources": observed["resources"], "facts": observed["facts"], "ui": observed["ui"]}
        elif after["kind"] == "combat":
            original = Reading.from_dict(before["reading"])
            complete = self._verify(original, observed, before["plan"]["steps"][0],
                                    pending["proposal"]["expected"], pending["proposal"]["checked"])
            state = observed.context["state"]
        else:
            complete = self._combat_boundary(pending, after)
            state = {"resources": observed["resources"], "facts": observed["facts"], "ui": observed["ui"]}
        changes = request.get("telemetry", {})
        _require(set(changes) <= {"inventory_events", "inventory_baseline", "zone_events", "zone_baseline",
                                 "zone_coverage", "transitions"}, "unknown telemetry override")
        self._transitions(before["context"], after["context"], changes.get("transitions", []))
        self._mutations(before, after, changes, complete)
        outcome = {"schema": "veda.play-telemetry.v1", "operation_id": request["operation_id"],
            "context": before["context"], "decision_id": pending["decision_id"],
            "source": after["source"], "state": state, "status": "verified",
            "evidence_note": after["source"]["evidence_note"], **changes}
        # Persist exact idempotent outcome before SQLite. If a crash follows its
        # commit, finalize() repeats the same write, never the controller input.
        pending.update(status="verified_pending_log", outcome_request=outcome,
                       after_context=after["context"], logical_action_complete=complete)
        self._save()
        return self.finalize()

    def _combat_boundary(self, pending, after):
        before, observation = pending["request"], after["observation"]
        expected = pending["proposal"]["expected"]
        _require(expected["kind"] in {"advance", "end_turn"}, "navigation cannot close combat or open a card choice")
        review = observation["review"].get("outcome", {})
        _require(review.get("action_id") == pending["action_id"]
                 and review.get("before_frame_id") == before["reading"]["frame_id"]
                 and review.get("before_sha256") == before["source"]["sha256"]
                 and review.get("action") == before["plan"]["steps"][0]
                 and isinstance(review.get("observed_result"), str) and bool(review["observed_result"].strip()),
                 "combat boundary needs a named source-bound action outcome")
        screen, facts = observation["ui"]["screen"], observation["facts"]
        if screen == "selection":
            card = expected.get("card", {})
            _require(card.get("name") in {"Headbutt", "Headbutt+", "Warcry", "Warcry+", "True Grit+"}
                     and facts.get("selection_cause_card_id") == card.get("id")
                     and after["context"] == before["context"], "unconfirmed card-selection boundary")
            step = pending["proposal"]["checked"]["steps"][0]
            _require(observation["resources"].get("energy") == step["energy_after"]
                     and observation["resources"].get("hp") == before["reading"]["context"]["state"]["hp"]
                     - step.get("reviewed_effect", {}).get("hp_loss", 0), "card-selection resources differ")
        else:
            _require(screen in {"reward", "card_reward", "result"}
                     and facts.get("combat_outcome") in {"win", "loss"}
                     and (screen == "result" or facts["combat_outcome"] == "win")
                     and after["context"]["floor_id"] == before["context"]["floor_id"]
                     and after["context"]["combat_id"] is None and after["context"]["turn_id"] is None,
                     "unconfirmed combat-end boundary")
        return True

    def _mutations(self, before, after, changes, complete):
        def inventory(packet):
            return packet["reading"]["context"]["inventory"] if packet["kind"] == "combat" else packet["inventory"]
        old, new = inventory(before), inventory(after)
        old_semantic, new_semantic = _inventory_semantics(old), _inventory_semantics(new)
        mutations = {k: v for k, v in changes.items() if k != "zone_coverage" and v}
        if not complete:
            _require(not mutations, "navigation/selection cannot assert gameplay mutations")
            _require(old_semantic == new_semantic, "inventory changed during navigation/selection")
            return
        if not mutations:
            _require(old_semantic == new_semantic, "observed inventory change needs explicit telemetry")
            return
        source = after["source"]
        review = after.get("mutation_review", {})
        frame_id = after["reading"]["frame_id"] if after["kind"] == "combat" else after["observation"]["frame"]["frame_id"]
        self._review(review, source, frame_id)
        _require(review.get("changes") == changes,
                 "each inventory, zone and lifecycle change needs an exact source-bound review")
        # An event cannot contradict the inventory the reviewer just supplied.
        events = changes.get("inventory_events", [])
        if events:
            _require(old_semantic["coverage"] == new_semantic["coverage"], "inventory coverage change needs a reviewed baseline")
            projected = deepcopy(old.get("current", {}))
            properties = deepcopy(old_semantic["properties"])
            for event in events:
                items = projected.setdefault(event["kind"], [])
                if event["action"] in {"removed", "consumed", "replaced"}:
                    _require(event["item"] in items, "mutation removes an unobserved item")
                    items.remove(event["item"])
                if event["action"] == "acquired":
                    items.append(event["item"])
                if event["action"] == "replaced":
                    items.append(event["related_item"])
                if event["action"] == "property_confirmed":
                    properties[event["kind"] + ":" + event["item"].casefold()] = event["property"]
            _require(all(sorted(projected.get(k, [])) == sorted(new.get("current", {}).get(k, []))
                         for k in ("card", "relic", "potion")), "inventory events contradict reviewed result")
            _require(properties == new_semantic["properties"], "inventory properties contradict reviewed events")
        elif changes.get("inventory_baseline"):
            baseline = changes["inventory_baseline"]
            projected = deepcopy(old_semantic)
            for kind in ("card", "relic", "potion"):
                level = baseline["coverage"][kind]
                names = [x["item"] for x in baseline["items"] if x["kind"] == kind]
                if level == "complete":
                    projected["current"][kind] = sorted(names)
                    projected["coverage"][kind] = "complete"
                elif level == "partial":
                    merged = Counter(projected["current"][kind]) | Counter(names)
                    projected["current"][kind] = sorted(merged.elements())
                    if projected["coverage"][kind] == "unknown":
                        projected["coverage"][kind] = "partial"
                else:
                    _require(level == "unknown" and not names, "unknown baseline cannot assert items")
            for item in baseline["items"]:
                if item.get("property"):
                    projected["properties"][item["kind"] + ":" + item["item"].casefold()] = item["property"]
            _require(projected == new_semantic, "inventory baseline contradicts reviewed result")
        else:
            _require(old_semantic == new_semantic, "observed inventory change needs explicit telemetry")

    @staticmethod
    def _transitions(before, after, transitions):
        # The reviewer may name new observed contexts before SQLite allocates
        # their canonical IDs. Only explicit observed lifecycle changes permit
        # those identity changes; the receipt supplies IDs for the next input.
        kinds = [item["kind"] for item in transitions]
        _require(before["run_id"] == after["run_id"], "run changed after input")
        for key, events in (("floor_id", {"advance_floor"}),
                            ("combat_id", {"start_combat", "end_combat"}),
                            ("turn_id", {"start_turn", "end_turn"})):
            changed = before[key] != after[key]
            _require(changed == bool(set(kinds) & events), "observed context and lifecycle disagree: " + key)

    def recover_unsent(self, request):
        """A persisted pre-dispatch failure cannot have crossed controller.call.

        A saved attempted state, a bridge error, or a timeout can NEVER use this
        path. Those always need observed game reconciliation, with no resend.
        """
        pending = self.state["pending"]
        _require(pending and pending["status"] == "preparing_dispatch", "dispatch may have happened; inspect outcome")
        records = self.telemetry.recover(run_id=self.state["run_id"])["pending"]
        decision = pending["decision_request"]
        if records:
            _require(len(records) == 1 and records[0]["context"] == decision["context"]
                     and records[0]["action"] == decision["action"]
                     and records[0]["source"] == decision["source"], "unrelated pending database decision")
            # This retry only resolves a proven unsent input; it never calls
            # the bridge. Expired evidence requires a fresh explicit review.
            source = self._source(request["source"], retain=True)
            self._review(request["review"], source, request["frame_id"])
            outcome = {k: decision[k] for k in ("schema", "context", "source", "state")}
            outcome["source"] = source
            outcome["state"] = request.get("state", {})
            outcome.update(operation_id=str(uuid4()), decision_id=records[0]["decision_id"],
                           status="not_performed", evidence_note="Durable pre-dispatch state proves no controller call occurred.")
            self.telemetry.record_outcome(outcome, now=self.clock())
        self.state["pending"] = None
        self._save()
        return {"status": "reconciled_unsent", "controller_input_sent": False}

    def reconcile(self, request):
        """Record uncertainty or a transport-proven unsent command; never retry."""
        pending = self.state["pending"]
        _require(pending and pending["status"] == "attempted" and request.get("action_id") == pending["action_id"],
                 "no matching attempted input")
        status = request.get("status")
        _require(status in {"unknown", "not_performed"}, "use verify for observed performed outcomes")
        if status == "not_performed":
            response = pending.get("response", {})
            _require(response.get("status") == "not_sent" and response.get("ok") is False
                     and response.get("request_id") == pending["action_id"],
                     "an unchanged screen does not prove non-delivery")
        source = self._source(request["source"], retain=True)
        self._review(request["review"], source, request["frame_id"])
        _require(_time(source["captured_at"]) > _time(pending["attempted_at"]), "reconciliation needs a later capture")
        outcome = {"schema": "veda.play-telemetry.v1", "operation_id": request["operation_id"],
            "context": pending["request"]["context"], "decision_id": pending["decision_id"],
            "source": source, "state": request["state"], "status": status,
            "evidence_note": source["evidence_note"]}
        if status == "not_performed":
            pending.update(status="verified_pending_log", outcome_request=outcome,
                           after_context=outcome["context"], logical_action_complete=False)
            self._save()
            return self.finalize()
        receipt = self.telemetry.record_outcome(outcome, now=self.clock())
        pending["uncertain_outcome"] = receipt
        self._save()
        self.disarm()
        return {"status": "unresolved", "must_not_repeat": True, "inventory_requires_inspection": True}

    def finalize(self):
        pending = self.state["pending"]
        _require(pending and pending["status"] == "verified_pending_log", "no reviewed result to finalize")
        try:
            receipt = self.telemetry.record_outcome(pending["outcome_request"], now=self.clock())
            complete = pending["logical_action_complete"]
            self.state["last_verified"] = {"action_id": pending["action_id"],
                "source": pending["outcome_request"]["source"], "receipt": receipt}
            self.state["pending"] = None
            self.state["completed"] += 1
            self._save()
        except BaseException:
            self.disarm()
            raise
        return {"status": "verified", "logical_action_complete": complete,
                "next_context": receipt["next_context"], "requires_fresh_review": True}

    def disarm(self):
        self.armed = False
        controller, self.controller = self.controller, None
        if controller is not None:
            acknowledged = False
            try:
                request_id = str(uuid4())
                response = controller.call({"action": "close", "request_id": request_id})
                acknowledged = response.get("ok") is True and response.get("request_id") == request_id
            except Exception:
                pass
            finally:
                controller.close()
            if not acknowledged and self.factory is not None:
                # A transport-uncertain BridgeClient refuses subsequent calls.
                # A separate local socket can request CLOSE ONLY on the same
                # warm process. It never restarts Remote Play or retries input.
                cleanup_client = None
                try:
                    cleanup_client = self.factory()
                    request_id = str(uuid4())
                    response = cleanup_client.call({"action": "close", "request_id": request_id})
                    acknowledged = response.get("ok") is True and response.get("request_id") == request_id
                except Exception:
                    pass
                finally:
                    if cleanup_client is not None:
                        cleanup_client.close()
            self.cleanup = {"close_acknowledged": acknowledged, "hardware_release_verified": False,
                            "required": "Check the owned warm bridge exit/cleanup result before rearming."}

    def close(self):
        if not self.closed:
            try:
                self.disarm()
            finally:
                self.closed = True
                self.lock.close()

    def handle(self, request):
        request = _copy(request)
        try:
            op = request.get("operation")
            _require(not self.poisoned or op in {"stop", "summary"}, "session storage failed; reopen and reconcile")
            if op == "summary":
                return self.summary()
            if op == "arm":
                return self.arm(request)
            if op == "prepare":
                return self.prepare(request)
            if op == "resume":
                return self.resume(request)
            if op == "send":
                return self.send(request["action_id"])
            if op == "verify":
                return self.verify(request)
            if op == "finalize":
                return self.finalize()
            if op == "cancel_prepared":
                return self.cancel_prepared()
            if op == "recover_unsent":
                return self.recover_unsent(request)
            if op == "reconcile":
                return self.reconcile(request)
            if op == "stop":
                self.close()
                return {"status": "stopped", "cleanup": deepcopy(self.cleanup)}
            raise ValueError("unknown reviewed play operation")
        except BaseException:
            self.disarm()
            raise
