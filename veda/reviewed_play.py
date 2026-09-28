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
from uuid import UUID, uuid4

from .choice_execution import (plan_choice_step, validate_choice_proposal, verify_choice_step,
                               _observation as validate_choice_observation)
from .combat_input import (CombatInputAdapter, RuntimeStop, _state_key, validate_combat_focus,
                           focus_transition, record_focus_recovery_attempt)
from .controller_state_machine import ControllerStateMachine
from .execution import ARM_PHRASE, Reading
from .routine_combat import routine_observation_reasons
from .play_telemetry import validate_outcome_request, outcome_request_digest
from .saved_frame_reader import _identity
from .play_timing import PlayTiming

MAX_BYTES = 1_000_000
MAX_AGE = 30
SCHEMA = "veda.reviewed-play.v1"


def verify_combat_observation(before, after, action, expected, checked, *, policy='strict'):
    """Separate proof of an observed action from its tactical forecast."""
    _require(policy in {'strict', 'learning'}, 'unknown decision policy')
    if expected['kind'] == 'focus_probe' or expected['kind'] == 'clear' and policy == 'learning':
        _require((after.floor_id, after.turn_id) == (before.floor_id, before.turn_id)
                 and _state_key(before) == _state_key(after), 'focus navigation changed observed gameplay state or context')
        transition = focus_transition(before.ui, after.ui, [card['id'] for card in after.context['state']['hand']])
        if before.image_sha256 == after.image_sha256:
            _require(not transition['returned_to_hand']
                     and transition['after']['domain'] in {'player_status', 'relic', 'potion', 'enemy'}
                     and transition['after']['subject_id'] is not None
                     and (transition['effect'] == 'unchanged'
                          or transition['effect'] == 'focus_observed' and transition['before']['domain'] == 'unknown'),
                     'identical pixels cannot prove a changed focus or return to hand')
        return {'logical_action_complete': False, 'focus_transition': transition,
                'observed_mismatches': [] if transition['returned_to_hand'] else [{
                    'field': 'focus_recovery', 'expected': 'hand', 'observed': transition['after']}]}
    if policy == 'strict' or expected['kind'] != 'advance':
        complete = CombatInputAdapter()._verify(before, after, action, expected, checked)
        return {'logical_action_complete': complete, 'observed_mismatches': []}
    _require((after.floor_id, after.turn_id) == (before.floor_id, before.turn_id),
             'floor or turn changed during card input')
    card, ui = expected['card'], after.ui
    unchanged = _state_key(before) == _state_key(after)
    if unchanged and ui.get('phase') in {'card_selected', 'targeting'} and ui.get('selected_card_id') == card['id']:
        _require(ui != before.ui, 'selection input produced no verified transition')
        return {'logical_action_complete': False, 'observed_mismatches': []}
    state = after.context.get('state', {})
    _require(after.context.get('fresh') and state.get('hand_complete') is True,
             'card resolution lacks a fresh complete hand')
    _require(not any(c.get('id') == card['id'] for c in state.get('hand', [])),
             'card play not verified; planned card remains in hand')
    _require(ui.get('screen_type') == 'combat' and ui.get('phase') in {'hand', 'tooltip'}
             and ui.get('selected_card_id') is None, 'card resolution needs a settled actual combat UI')
    mismatches = []
    def compare(field, prediction, actual):
        if prediction != actual:
            mismatches.append({'field': field, 'expected': deepcopy(prediction), 'observed': deepcopy(actual)})
    steps = checked.get('steps', [])
    if steps:
        if steps[0].get('energy_after') is not None:
            compare('energy', steps[0]['energy_after'], state.get('energy'))
        old_hp = before.context['state'].get('hp')
        loss = steps[0].get('reviewed_effect', {}).get('hp_loss', 0)
        if type(old_hp) in (int, float) and type(loss) in (int, float):
            compare('hp', old_hp - loss, state.get('hp'))
    forecast = checked.get('forecast')
    if forecast:
        compare('block', forecast.get('block', forecast.get('player_block')), state.get('block'))
        for enemy in forecast.get('enemies', []):
            current = next((e for e in state.get('enemies', []) if e.get('id') == enemy.get('id')), {})
            for key in ('hp', 'block'):
                compare('enemies.' + str(enemy.get('id')) + '.' + key, enemy.get(key), current.get(key))
    return {'logical_action_complete': True, 'observed_mismatches': mismatches}


def verify_choice_observation(proposal, before, after, *, policy='strict', now=None, historical=False):
    """Keep control/navigation proof exact; learn actual committed menu effects."""
    _require(policy in {'strict', 'learning'}, 'unknown decision policy')
    try:
        return {**verify_choice_step(proposal, before, after, now=now, historical=historical), 'observed_mismatches': []}
    except ValueError:
        if policy != 'learning' or proposal.get('step_kind') != 'commit':
            raise
    before = validate_choice_observation(before, None, None)
    validate_choice_proposal(proposal, before, now=_time(before['frame']['observed_at']))
    after = validate_choice_observation(after, now or datetime.now(timezone.utc), None if historical else proposal['max_age_seconds'])
    _require(after['frame']['frame_id'] != before['frame']['frame_id']
             and after['frame']['image_sha256'] != before['frame']['image_sha256']
             and _time(after['frame']['observed_at']) > _time(before['frame']['observed_at'])
             and before['context']['run_id'] == after['context']['run_id'],
             'committed outcome needs a distinct later source for the same run')
    outcome = after['review'].get('outcome', {})
    _require(outcome.get('action_id') == proposal['action_id']
             and outcome.get('before_frame_id') == before['frame']['frame_id']
             and outcome.get('before_sha256') == before['frame']['image_sha256']
             and outcome.get('choice_id') == proposal['choice']['choice_id']
             and outcome.get('option_ids') == proposal['choice']['option_ids']
             and isinstance(outcome.get('observed_result'), str) and outcome['observed_result'].strip(),
             'committed choice needs exact source-bound action and option review')
    # Match the ordinary commit proof: focus, selection bookkeeping, layout
    # labels and evidence rebinding alone are not an observed game result.
    def progress(observation):
        ui = observation['ui']
        return {k: observation[k] for k in ('context', 'resources', 'inventory_digest', 'facts')} | {
            'screen': ui['screen'], 'phase': ui['phase'],
            'options': [{k: option.get(k) for k in ('id', 'label', 'enabled', 'costs', 'role')}
                        for option in ui['options']]}
    _require(progress(before) != progress(after), 'no observed semantic choice result')
    return {'choice_complete': True, 'step_verified': True, 'observed_mismatches': [{
        'field': 'choice_postconditions', 'expected': deepcopy(proposal['choice']['postconditions']),
        'observed': {k: deepcopy(after[k]) for k in ('context', 'resources', 'inventory_digest', 'facts')}
                    | {'screen': after['ui']['screen'], 'phase': after['ui']['phase']}}]}


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
        known = {"connect_failed", "connect_permission_denied", "connect_socket_missing",
                 "connect_refused", "connect_timed_out", "command_timeout", "session_stopped", "controller_transport_failed",
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
                 mode="shadow", clock=None, decision_policy='strict'):
        _require(mode in {"shadow", "codex"}, "mode must be shadow or codex")
        _require(decision_policy in {'strict', 'learning'}, 'decision policy must be strict or learning')
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
        self.decision_policy = decision_policy
        self.transport_failed = False
        self.machine = ControllerStateMachine()
        self.controller = None
        self.armed = False
        self.closed = False
        self.poisoned = False
        self.cleanup = None
        self.timing = None
        self.timing_error = None
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
            _require(not self.state.get('pending') or self.state.get('decision_policy', 'strict') == decision_policy,
                     'finish the pending action under its recorded decision policy before changing policy')
            if self.state.get('decision_policy') != decision_policy:
                self.state['decision_policy'] = decision_policy
                self._save()
            # An adapter process never inherits another process's continuity.
            self.state['evidence_continuity'] = None
            self._save()
            try:
                self.timing = PlayTiming(self.directory / 'timing.json', run_id=run_id,
                    clock=self.clock, startup_observed=False,
                    monotonic_clock=(lambda: int(self.clock().timestamp() * 1_000_000_000)) if clock else None)
                if mode == 'codex' and self.timing.snapshot().get('pause') is not None:
                    self.timing.event('resume')
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
                self.timing_error = str(error)
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
        _require(observed <= self.clock(), "reviewed source is future dated")
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

    def _evidence_age(self, request):
        binding = request.get('evidence_binding')
        if binding is None:
            return MAX_AGE
        from .evidence_continuity import check_binding
        check_binding(binding, self.state.get('evidence_continuity'), request['context'], request['source'])
        return None

    def invalidate_evidence(self, reason):
        _require(isinstance(reason, str) and bool(reason.strip()), 'state change reason required')
        current = self.state.get('evidence_continuity')
        if current:
            current.update(id=str(uuid4()), since=self.clock().isoformat(), sequence=current['sequence'] + 1,
                           reason=reason)
            self._save()
        return {'status': 'evidence_invalidated', 'controller_input_sent': False,
                'required': 'Inspect the current screen; pending input must still be reconciled.'}

    def _before(self, request, *, check_plan_now=True, historical=False):
        _require(request.get('decision_policy', self.decision_policy) == self.decision_policy,
                 'request decision policy differs from active session')
        age_limit = None if historical else self._evidence_age(request)
        source = self._source(request["source"], fresh=age_limit is not None)
        context = request["context"]
        _require(set(context) == {"run_id", "floor_id", "combat_id", "turn_id"}
                 and context["run_id"] == self.state["run_id"], "exact current run context required")
        if request['kind'] == 'combat_inspection':
            from .combat_inspection import validate_inspection_observation
            observation = request['observation']
            frame = observation['frame']
            _require(observation['context'] == context
                     and frame['image_sha256'] == source['sha256']
                     and frame['observed_at'] == source['captured_at'],
                     'inspection context/frame differs from saved source')
            self._review(request['review'], source, frame['frame_id'])
            _require(request['review'] == observation['review'], 'inspection review differs')
            inventory = request['inventory']
            _require(all(inventory.get('coverage', {}).get(k) in
                         ({'complete', 'partial', 'unknown'} if self.decision_policy == 'learning' else {'complete'})
                         for k in ('relic', 'potion'))
                     and inventory_digest(inventory) == observation['inventory_digest'],
                     'reviewed inventory coverage and inspection digest must match the active policy')
            return validate_inspection_observation(observation, now=self.clock(), max_age_seconds=age_limit)
        if request["kind"] == "choice":
            observation = request["observation"]
            _require(observation["context"] == context, "choice context differs")
            frame = observation["frame"]
            _require(frame["image_sha256"] == source["sha256"]
                     and frame["observed_at"] == source["captured_at"], "choice frame differs from saved source")
            inventory = request["inventory"]
            title_continue = (observation["ui"]["screen"] == "title"
                              and request.get("choice", {}).get("kind") == "continue_run")
            _require((title_continue or all(inventory.get('coverage', {}).get(k) in
                         ({'complete', 'partial', 'unknown'} if self.decision_policy == 'learning' else {'complete'})
                         for k in ('relic', 'potion')))
                     and inventory_digest(inventory) == observation["inventory_digest"],
                     "reviewed inventory coverage and choice digest must match the active policy")
            validate_choice_observation(observation, self.clock(), age_limit)
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
        _require(reading.context.get('decision_policy', self.decision_policy) == self.decision_policy,
                 'reading decision policy differs from active session')
        if self.decision_policy == 'strict':
            reasons = routine_observation_reasons(reading.state, reading.context)
            _require(not reasons, 'incomplete reviewed combat: ' + '; '.join(reasons))
        else:
            from .decision_policy import assess_observation
            assessment = assess_observation(reading.state, reading.context, policy=self.decision_policy)
            _require(assessment['allowed'], 'incomplete reviewed combat: ' + '; '.join(assessment['hard_reasons']))
        validate_combat_focus(reading.ui, [card['id'] for card in reading.context['state']['hand']],
                              require_explicit=self.decision_policy == 'learning')
        if check_plan_now:
            self._assess(request, reading)
        return reading

    def _assess(self, request, reading):
        from .decision_policy import assess_combat
        plan = request['plan']
        _require(len(plan.get('steps', [])) == 1 and plan['steps'][0].get('kind') in {'card', 'end_turn'},
                 'exactly one card or End Turn; potions use the choice flow')
        assessment = assess_combat(reading.context, plan, policy=self.decision_policy, visual=reading.state)
        _require(assessment['allowed'], 'move rejected: ' + '; '.join(assessment['hard_reasons']))
        return assessment

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
        return {"schema": SCHEMA, "mode": self.mode, "decision_policy": self.decision_policy, "run_id": self.state["run_id"],
                "armed": self.armed, "pending": deepcopy(brief),
                "recovery": recovery, "automatic_recognition_complete": False,
                "runtime_authorized": False, "completed_inputs": self.state["completed"],
                "cleanup": deepcopy(self.cleanup),
                "last_bridge_preflight": deepcopy(self.state.get("last_bridge_preflight")),
                "timing": self.timing_summary()}

    def _timing_event(self, operation, **fields):
        # Measurement failures are surfaced, never mistaken for game failures
        # or allowed to conceal an already dispatched input.
        if self.timing is not None:
            try:
                return self.timing.event(operation, **fields)
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
                self.timing_error = str(error)

    def timing_summary(self, *, poll=False):
        try:
            result = (self.timing.poll() if poll else self.timing.summary()) if self.timing else {}
        except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
            self.timing_error = str(error)
            result = {}
        return {**result, **({'measurement_error': self.timing_error} if self.timing_error else {})}

    def _begin_timed_move(self, identifier):
        snapshot = self._timing_snapshot()
        if snapshot is not None and snapshot.get('move') is None:
            self._timing_event('begin_move', move_id=identifier, kind='reviewed_decision')

    def _timing_snapshot(self):
        try:
            return self.timing.snapshot() if self.timing else None
        except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
            self.timing_error = str(error)
            return None

    @staticmethod
    def _timed_floor_kind(request):
        context = request.get('reading', {}).get('context', {})
        kind = context.get('encounter_type')
        if request['kind'] == 'combat':
            return kind if kind in {'elite', 'boss'} else 'combat'
        if request['kind'] == 'combat_inspection':
            return 'combat'
        node = request.get('observation', {}).get('facts', {}).get('node_type')
        return node if node in {'elite', 'boss'} else 'combat' if node == 'enemy' else 'noncombat'

    def _check_bridge_status(self, *, retain_connection):
        """Probe from this process before spending the screenshot review window."""
        _require(self.factory is not None, "existing warm bridge adapter required")
        request_id = str(uuid4())
        exception_code = "bridge_client_initialization_failed"
        client = None
        try:
            client = self.factory()
            if retain_connection:
                self.controller = client
            exception_code = "bridge_status_exception"
            response = client.call({"action": "status", "request_id": request_id})
            problem = _bridge_readiness_problem(response, request_id)
        except Exception:
            problem = exception_code
        finally:
            if client is not None and not retain_connection:
                # Close only this probe's socket, never the running warm bridge.
                try:
                    client.close()
                except Exception:
                    problem = "bridge_probe_cleanup_failed"
        self.state["last_bridge_preflight"] = {"checked_at": self.clock().isoformat(),
            "request_id": request_id, "ready": problem is None, "reason": problem,
            "controller_input_sent": False}
        self._save()
        return problem

    def bridge_preflight(self):
        _require(not self.closed and not self.poisoned and self.mode == "codex",
                 "shadow/closed/failed sessions cannot probe the bridge")
        _require(not self.armed and not self.state["pending"], "bridge preflight requires an idle unarmed adapter")
        problem = self._check_bridge_status(retain_connection=False)
        return {"status": "bridge_access_ready" if problem is None else "bridge_access_blocked",
                "ready": problem is None, "reason": problem, "armed": False,
                "controller_input_sent": False, "requires_fresh_arm_review": True}

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
        check_binding = getattr(self.telemetry, 'check_run_binding', None)
        _require(callable(check_binding), 'run binding verification is required before arming')
        binding = check_binding(run_id=self.state['run_id'])
        _require(isinstance(binding, dict) and binding.get('allowed') is True
                 and binding.get('controller_authorized') is False and binding.get('runtime_authorized') is False
                 and binding == {'schema': 'veda.play-run-binding.v1', 'run_id': self.state['run_id'],
                 'run_status': 'active', 'ended_at': None, 'lifecycle_status': 'not_established', 'allowed': True,
                 'controller_authorized': False, 'runtime_authorized': False},
                 'run binding verification returned an incomplete or unsupported result')
        recovery = self.telemetry.recover(run_id=self.state["run_id"])
        _require(not recovery.get("pending"), "unresolved database decision requires reconciliation")
        problem = self._check_bridge_status(retain_connection=True)
        if problem is not None:
            self.transport_failed = True
            self.disarm()
            raise RuntimeStop(f"warm bridge is not ready ({problem}); no gameplay input sent") from None
        self.armed = True
        self.state['evidence_continuity'] = {'id': str(uuid4()), 'active': True,
            'run_id': self.state['run_id'], 'context': None, 'sequence': 0,
            'since': source['captured_at'], 'reason': 'armed from inspected game identity'}
        self._save()
        self.cleanup = None
        return {"status": "armed_codex_reviewed", "run_id": self.state["run_id"],
                "automatic_recognition_complete": False, "runtime_authorized": False}

    def prepare(self, request):
        _require(not self.closed and not self.poisoned and not self.state["pending"], "resolve pending input or reopen failed session")
        _require(self.state["completed"] < 10000, "session input limit reached")
        request = _copy(request)
        snapshot = self._timing_snapshot()
        if snapshot is not None and snapshot.get('floor') is None:
            self._timing_event('begin_floor', floor_id=request['context']['floor_id'],
                               kind=self._timed_floor_kind(request))
        self._begin_timed_move('decision-' + str(uuid4()))
        self._timing_event('phase', name='prepare')
        before = self._before(request, check_plan_now=False)
        age_limit = self._evidence_age(request)
        request['decision_policy'] = self.decision_policy
        if request['kind'] == 'combat':
            request['reading']['context']['decision_policy'] = self.decision_policy
        action_id = str(uuid4())
        if request['kind'] == 'combat_inspection':
            from .combat_inspection import plan_tooltip_clear
            proposal = plan_tooltip_clear(before, control_profile=request['control_profile'],
                now=self.clock(), max_age_seconds=age_limit, action_id=action_id, progress=self.state.get('combat_focus_progress'))
            command = proposal['command']
            semantic = {'kind': 'navigation', 'inspection': proposal['step_kind'], 'step_kind': 'inspect'}
        elif request["kind"] == "choice":
            proposal = plan_choice_step(before, request["choice"], now=self.clock(),
                                        max_age_seconds=age_limit, action_id=action_id)
            command = proposal["command"]
            if proposal["step_kind"] == "inspect":
                self._map_inspection_budget(before, command["buttons"][0])
            semantic = {"kind": "navigation" if proposal["step_kind"] != "commit" else "choice",
                        "choice": request["choice"], "step_kind": proposal["step_kind"]}
        else:
            assessment = self._assess(request, before)
            self.machine.reset()
            action = request["plan"]["steps"][0]
            step, expected = self._input(before, action)
            if expected['kind'] == 'focus_probe':
                record_focus_recovery_attempt(self.state.get('combat_focus_progress'), request['context'],
                                              expected['before_focus'], action_id, expected['direction'])
            command = {"action": "tap", "buttons": step["buttons"], "request_id": action_id}
            proposal = {"expected": expected, "checked": assessment['checked'], 'assessment': assessment}
            semantic = {"kind": "navigation" if expected["kind"] in {"clear", "card_focus", "target_focus", "focus_probe"}
                        else action["kind"], "requested_action": action, "expected": expected,
                        'decision_policy': self.decision_policy, 'assessment': assessment}
        request["source"] = self._source(request["source"], fresh=age_limit is not None, retain=True)
        self.state["pending"] = {"action_id": action_id, "status": "prepared", "request": request,
                                 "proposal": proposal, "command": command, "semantic": semantic}
        self._save()
        return {"status": "prepared", "action_id": action_id, "command": command,
                "mode": self.mode, "decision_policy": self.decision_policy,
                'assessment': proposal.get('assessment'), "controller_input_sent": False}

    def resume(self, request):
        _require(not self.state["pending"], "reconcile pending input before resume")
        value = _copy(request["telemetry"])
        _require(value["context"]["run_id"] == self.state["run_id"], "resume belongs to another run")
        value["source"] = self._source(value["source"], retain=True)
        self._review(request["review"], value["source"], request["frame_id"])
        return self.telemetry.record_resume(value, now=self.clock())

    def execute(self, request):
        """Prepare and send exactly one reviewed input without a model round trip.

        Both ordinary checks and durable pre-dispatch writes remain in place.
        This never verifies, batches, retries, arms or resolves an old input.
        """
        _require(self.armed and self.controller is not None and not self.closed
                 and not self.poisoned, "execute requires an already armed session")
        prepared = self.prepare(request)
        return self.send(prepared["action_id"])

    def cancel_prepared(self):
        _require(self.state["pending"] and self.state["pending"]["status"] == "prepared",
                 "only an input that never crossed dispatch can be cancelled")
        retained = self.state['pending']
        self.state["pending"] = None
        try:
            self._save()
        except BaseException:
            self.state['pending'] = retained
            raise
        return {"status": "cancelled_without_input"}

    def send(self, action_id):
        _require(self.armed and self.controller is not None and not self.closed and not self.poisoned, "session is not armed")
        pending = self.state["pending"]
        _require(pending and pending["action_id"] == action_id and pending["status"] == "prepared",
                 "input already attempted or proposal unknown; never repeat")
        request = pending["request"]
        self._timing_event('phase', name='dispatch')
        before = self._before(request, check_plan_now=False)
        if request.get('evidence_binding') is not None:
            # Transport readiness is independent of how long a settled turn lasts.
            probe_id = str(uuid4())
            try:
                status = self.controller.call({'action': 'status', 'request_id': probe_id})
                problem = _bridge_readiness_problem(status, probe_id)
            except Exception:
                problem = 'bridge_status_exception'
            if problem is not None:
                self.transport_failed = True
                self.disarm()
                raise RuntimeStop('bridge readiness lost; no gameplay input sent: ' + problem)
        if request['kind'] == 'combat_inspection':
            from .combat_inspection import validate_inspection_proposal, record_inspection_attempt
            validate_inspection_proposal(pending['proposal'], before, now=self.clock(),
                                         progress=self.state.get('combat_focus_progress'))
            inspection_progress = record_inspection_attempt(self.state.get('combat_focus_progress'), before, action_id)
        elif request["kind"] == "choice":
            validate_choice_proposal(pending["proposal"], before, now=self.clock())
            if pending["proposal"]["step_kind"] == "inspect":
                self._map_inspection_budget(before, pending["command"]["buttons"][0])
        else:
            assessment = self._assess(request, before)
            step, expected = self._input(before, request['plan']['steps'][0])
            _require(step['buttons'] == pending['command']['buttons'] and expected == pending['proposal']['expected'],
                     'reviewed atomic input changed since preparation')
            pending['proposal'].update(checked=assessment['checked'], assessment=assessment)
            pending['semantic'].update(decision_policy=self.decision_policy, assessment=assessment)
            if expected['kind'] == 'focus_probe':
                focus_progress = record_focus_recovery_attempt(self.state.get('combat_focus_progress'), request['context'],
                    expected['before_focus'], action_id, expected['direction'])
        state = before.context["state"] if request["kind"] == "combat" else {
            "resources": before["resources"], "facts": before["facts"], "ui": before["ui"]}
        decision = {"schema": "veda.play-telemetry.v1", "operation_id": action_id,
            "context": request["context"], "source": request["source"], "state": state,
            "phase": "combat" if request["kind"] == "combat" else before["ui"]["screen"],
            "action": pending["semantic"], "reasoning": request["reasoning"]}
        assessment = pending['proposal'].get('assessment', {})
        decision['prediction'] = {'decision_policy': self.decision_policy,
            'assessment': {k: deepcopy(assessment.get(k)) for k in
                ('warnings', 'hard_reasons', 'forecast_status', 'decision_under_uncertainty')},
            'forecast': deepcopy(assessment.get('forecast')), 'chosen_action': deepcopy(pending['semantic']),
            'chosen_reason': request['reasoning'], 'retrieved_case_ids': deepcopy(request.get('retrieved_case_ids', []))}
        # Mark ambiguity before the database call too: if the write commits but
        # its return/save fails, recovery must not submit that operation again.
        pending.update(status="preparing_dispatch", decision_request=decision,
                       preparation_started_at=self.clock().isoformat())
        self._save()
        entered_controller_call = False
        transport_confirmed = False
        try:
            if request.get('evidence_binding') is not None:
                receipt = self.telemetry.record_decision(decision, now=self.clock(),
                    event_bound={'binding': request['evidence_binding'], 'source': request['source'],
                                 'context': request['context']})
            else:
                receipt = self.telemetry.record_decision(decision, now=self.clock())
            pending.update(status="attempted", decision_id=receipt["decision_id"],
                           attempted_at=self.clock().isoformat())
            if request['kind'] == 'combat_inspection':
                self.state['combat_focus_progress'] = inspection_progress
            elif request['kind'] == 'combat' and pending['proposal']['expected']['kind'] == 'focus_probe':
                self.state['combat_focus_progress'] = focus_progress
            # Persist the input barrier before crossing the controller boundary.
            continuity = self.state.get('evidence_continuity')
            if continuity:
                continuity.update(sequence=continuity['sequence'] + 1, since=pending['attempted_at'],
                                  context=deepcopy(request['context']), reason='input attempted')
            self._save()
            self._source(request['source'], fresh=request.get('evidence_binding') is None)
            entered_controller_call = True
            response = self.controller.call(pending["command"])
            pending["response"] = response
            transport_confirmed = (isinstance(response, dict) and response.get('ok') is True
                and response.get('status', 'ok') == 'ok' and response.get('request_id') == action_id)
            self._save()
            _require(transport_confirmed,
                     "controller delivery uncertain; inspect and reconcile, never resend")
        except BaseException:
            if entered_controller_call and not transport_confirmed:
                self.transport_failed = True
                self.disarm()
            elif self.decision_policy == 'strict':
                self.disarm()
            elif not entered_controller_call and not self.poisoned and pending['status'] == 'attempted':
                # This process has not entered controller.call. Persist that
                # positive local fact for existing recover_unsent; never retry.
                pending.update(status='preparing_dispatch', pre_dispatch_failure={'controller_call_started': False})
                self._save()
            raise
        self._timing_event('phase', name='verification')
        return {"status": "awaiting_fresh_review", "action_id": action_id,
                "controller_input_sent": True, "game_outcome_verified": False}

    def verify(self, request):
        pending = self.state["pending"]
        _require(pending and pending["status"] == "attempted" and
                 request.get("action_id") == pending["action_id"], "no matching attempted input")
        after = _copy(request["after"])
        after["source"] = self._source(after["source"], fresh=False, retain=True)
        before = pending["request"]
        inspection = (before["kind"] == "choice" and pending["proposal"]["step_kind"] == "inspect"
                      or before['kind'] == 'combat_inspection'
                      or before['kind'] == 'combat' and pending['proposal']['expected']['kind'] in {'clear', 'focus_probe'})
        _require((after["source"]["sha256"] != before["source"]["sha256"]
                  or inspection and after["source"]["path"] != before["source"]["path"])
                 and _time(after["source"]["captured_at"]) > _time(pending["attempted_at"]),
                 "outcome needs a distinct image captured after dispatch")
        observed = self._before(after, check_plan_now=False, historical=True)
        observed_mismatches = []
        focus_result = None
        if before['kind'] == 'combat_inspection':
            from .combat_inspection import verify_tooltip_clear
            _require(after['kind'] == 'combat_inspection', 'tooltip result requires the same inspection contract')
            verified = verify_tooltip_clear(pending['proposal'], before['observation'], observed, now=self.clock(), historical=True)
            focus_result = verified.get('focus_transition')
            observed_mismatches = verified.get('observed_mismatches', [])
            complete = False
            state = {key: observed[key] for key in ('resources', 'facts', 'ui')}
        elif before["kind"] == "choice":
            _require(after["kind"] == "choice", "choice verification needs the reviewed choice-result contract")
            verified = verify_choice_observation(pending['proposal'], before['observation'], observed,
                                                 policy=self.decision_policy, now=self.clock(), historical=True)
            observed_mismatches = verified['observed_mismatches']
            complete = verified["choice_complete"]
            state = {"resources": observed["resources"], "facts": observed["facts"], "ui": observed["ui"]}
            if verified.get("matched_outcome_id") is not None:
                # Derived by matching the complete branch; never a caller-selected outcome.
                state["choice_outcome_id"] = verified["matched_outcome_id"]
        elif after["kind"] == "combat":
            original = Reading.from_dict(before["reading"])
            verified = verify_combat_observation(original, observed, before['plan']['steps'][0],
                pending['proposal']['expected'], pending['proposal']['checked'], policy=self.decision_policy)
            complete = verified['logical_action_complete']; observed_mismatches = verified['observed_mismatches']
            focus_result = verified.get('focus_transition')
            state = {**observed.context["state"], 'ui': deepcopy(observed.ui)}
        else:
            complete = self._combat_boundary(pending, after)
            observed_mismatches = deepcopy(getattr(self, '_boundary_mismatches', []))
            state = {"resources": observed["resources"], "facts": observed["facts"], "ui": observed["ui"]}
        changes = request.get("telemetry", {})
        if before['kind'] == 'combat_inspection':
            _require(changes in ({}, {'zone_coverage': 'complete'}),
                     'tooltip navigation cannot assert gameplay or lifecycle changes')
            # Verified equal facts/resources cannot move cards. Preserve any
            # prior known zones; this creates no new baseline or hidden facts.
            changes = {'zone_coverage': 'complete'}
        _require(set(changes) <= {"inventory_events", "inventory_baseline", "zone_events", "zone_baseline",
                                 "zone_coverage", "transitions"}, "unknown telemetry override")
        if (before['kind'] == 'choice' and before['observation']['ui'].get('menu_family') == 'map_nodes'
                and pending['proposal']['step_kind'] == 'commit'):
            from .map_transitions import validate_map_arrival
            validate_map_arrival(before, after, changes)
        self._transitions(before["context"], after["context"], changes.get("transitions", []))
        self._mutations(before, after, changes, complete)
        outcome = {"schema": "veda.play-telemetry.v1", "operation_id": request["operation_id"],
            "context": before["context"], "decision_id": pending["decision_id"],
            "source": after["source"], "state": state, "status": "verified",
            "evidence_note": after["source"]["evidence_note"], **changes}
        # Validate metadata before entering the immutable log-finalization state.
        # A rejected note/shape remains attempted and accepts a corrected fresh
        # review, without ever repeating the input.
        outcome = validate_outcome_request(outcome)
        verification = {"basis": "action_bound_result", "action_id": pending["action_id"],
            "attempted_at": pending["attempted_at"],
            "outcome_sha256": outcome_request_digest(outcome), "source": deepcopy(outcome["source"]),
            "reviewed_at": self.clock().isoformat(),
            "review": deepcopy(observed["review"] if before["kind"] == "choice" else after["review"]),
            'decision_policy': self.decision_policy, 'assessment': deepcopy(pending['proposal'].get('assessment', {})),
            'observed_mismatches': observed_mismatches}
        if focus_result is not None:
            verification['focus_transition'] = focus_result
        # Persist exact idempotent outcome before SQLite. If a crash follows its
        # commit, finalize() repeats the same write, never the controller input.
        pending.update(status="verified_pending_log", outcome_request=outcome, outcome_verification=verification,
                       after_context=after["context"], logical_action_complete=complete)
        self._save()
        return self.finalize()

    def _combat_boundary(self, pending, after):
        self._boundary_mismatches = []
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
            _require((getattr(self, 'decision_policy', 'strict') == 'learning'
                      or card.get("name") in {"Headbutt", "Headbutt+", "Warcry", "Warcry+", "True Grit+"})
                     and expected['kind'] == 'advance' and bool(card.get('id'))
                     and facts.get("selection_cause_card_id") == card.get("id")
                     and after["context"] == before["context"], "unconfirmed card-selection boundary")
            steps = pending['proposal']['checked'].get('steps', [])
            step = steps[0] if steps else {}
            old_hp = before['reading']['context']['state'].get('hp')
            loss = step.get('reviewed_effect', {}).get('hp_loss', 0)
            expected_resources = {'energy': step.get('energy_after'), 'hp':
                old_hp - loss if type(old_hp) in (int, float) and type(loss) in (int, float) else None}
            self._boundary_mismatches = [{'field': key, 'expected': value, 'observed': observation['resources'].get(key)}
                for key, value in expected_resources.items() if value is not None and observation['resources'].get(key) != value]
            _require(getattr(self, 'decision_policy', 'strict') == 'learning' or not self._boundary_mismatches,
                     'card-selection resources differ')
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
        try:
            self._save()
        except BaseException:
            self.state['pending'] = pending
            raise
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
        if self.decision_policy == 'strict':
            self.disarm()
        return {"status": "unresolved", "must_not_repeat": True, "inventory_requires_inspection": True}

    def repair_outcome_metadata(self, request):
        """Add reviewed missing event notes to an uncommitted legacy outcome only.

        No source time, game state, action, item, decision or operation ID can be
        replaced. The archived source stays archived; this grants no authority.
        """
        _require(not self.closed, "closed session cannot repair metadata")
        expected_keys = {"operation", "repair_id", "action_id", "outcome_operation_id",
                         "expected_outcome_sha256", "inventory_event_notes", "review"}
        _require(set(request) == expected_keys, "metadata repair accepts only bounded missing-note fields")
        _require(isinstance(request["repair_id"], str) and len(request["repair_id"]) <= 128,
                 "metadata repair ID must be a UUID")
        UUID(request["repair_id"])
        pending = self.state["pending"]
        _require(pending and pending["status"] == "verified_pending_log"
                 and pending["action_id"] == request["action_id"], "no matching verified result to repair")
        prior_repair = pending.get("metadata_repair")
        if prior_repair is not None:
            _require(prior_repair["request"] == request, "this outcome already has a different metadata repair")
            return {"status": "outcome_metadata_repaired", "idempotent_replay": True,
                    "must_not_repeat": True, "controller_input_sent": False, "requires_finalize": True}
        original = _copy(pending["outcome_request"])
        _require(original["context"] == pending["request"]["context"]
                 and original["context"]["run_id"] == self.state["run_id"],
                 "retained action, outcome and session context must match")
        _require(original["operation_id"] == request["outcome_operation_id"]
                 and outcome_request_digest(original) == request["expected_outcome_sha256"],
                 "pending outcome changed; metadata repair compare-and-swap failed")
        review = request["review"]
        _require(isinstance(review, dict) and set(review) == {"kind", "complete", "reviewer", "source", "evidence_note"}
                 and review["kind"] == "reviewed_retained_outcome_metadata" and review["complete"] is True
                 and isinstance(review["reviewer"], str) and 0 < len(review["reviewer"].strip()) <= 128
                 and isinstance(review["evidence_note"], str) and 0 < len(review["evidence_note"].strip()) <= 2048
                 and review["source"] == original["source"], "explicit review of the exact retained evidence is required")
        notes = request["inventory_event_notes"]
        _require(isinstance(notes, list) and 1 <= len(notes) <= 100, "bounded missing inventory-event notes required")
        repaired = _copy(original)
        events = repaired.get("inventory_events", [])
        indexes = set()
        for note in notes:
            _require(isinstance(note, dict) and set(note) == {"index", "evidence_note"}
                     and type(note["index"]) is int and 0 <= note["index"] < len(events)
                     and note["index"] not in indexes, "metadata repair needs distinct existing event indexes")
            index = note["index"]
            _require(isinstance(events[index], dict) and "evidence_note" not in events[index],
                     "metadata repair cannot replace an existing evidence note")
            events[index]["evidence_note"] = note["evidence_note"]
            indexes.add(index)
        repaired = validate_outcome_request(repaired)
        self.telemetry.check_outcome_metadata_repair(original, repaired, action_id=pending["action_id"])
        reviewed_at = self.clock().isoformat()
        pending["metadata_repair"] = {"request": _copy(request), "original_outcome_request": original,
            "repaired_outcome_sha256": outcome_request_digest(repaired), "recorded_at": reviewed_at}
        pending["outcome_request"] = repaired
        pending["outcome_verification"] = {"basis": "reviewed_retained_verified_result",
            "action_id": pending["action_id"], "outcome_sha256": outcome_request_digest(repaired),
            "source": deepcopy(repaired["source"]), "reviewed_at": reviewed_at,
            "metadata_repair": deepcopy(pending["metadata_repair"])}
        self._save()
        return {"status": "outcome_metadata_repaired", "idempotent_replay": False,
                "must_not_repeat": True, "controller_input_sent": False, "requires_finalize": True}

    def finalize(self):
        pending = self.state["pending"]
        _require(pending and pending["status"] == "verified_pending_log", "no reviewed result to finalize")
        retained = deepcopy(self.state)
        try:
            kwargs = {"now": self.clock()}
            if pending.get("outcome_verification") is not None:
                kwargs["verified_evidence"] = pending["outcome_verification"]
            receipt = self.telemetry.record_outcome(pending["outcome_request"], **kwargs)
            complete = pending["logical_action_complete"]
            focus_result = pending.get('outcome_verification', {}).get('focus_transition')
            if focus_result is not None:
                if pending['request']['kind'] == 'combat':
                    for row in self.state.get('combat_focus_progress', {}).get('attempts', []):
                        if row['action_id'] == pending['action_id']:
                            row['result'] = deepcopy(focus_result)
                elif (pending['request']['kind'] == 'combat_inspection'
                      and pending['proposal']['step_kind'] != 'clear_tooltip'):
                    from .combat_inspection import record_inspection_result
                    self.state['combat_focus_progress'] = record_inspection_result(
                        self.state.get('combat_focus_progress'), pending['action_id'], focus_result)
            if (pending["request"]["kind"] == "choice"
                    and pending["proposal"]["step_kind"] == "inspect"):
                before = pending["request"]["observation"]
                button = pending["command"]["buttons"][0]
                scope, progress = self._map_inspection_budget(before, button)
                view = pending["outcome_request"]["state"].get("ui", {}).get("map_view", {})
                failures = dict(progress.get("without_viewport_progress", {}))
                if view.get("effect") == "viewport_changed":
                    failures = {}
                failures[button] = (0 if view.get("effect") == "viewport_changed"
                                    else failures.get(button, 0) + 1)
                self.state["map_inspection_progress"] = {"scope": scope,
                    "total": progress.get("total", 0) + 1, "without_viewport_progress": failures}
            self.state["last_verified"] = {"action_id": pending["action_id"],
                "source": pending["outcome_request"]["source"], "receipt": receipt}
            step_kind = pending['semantic']['kind']
            self._timing_event('verified_input', action_id=pending['action_id'],
                               step_kind=step_kind, move_complete=bool(complete))
            if receipt['next_context']['floor_id'] != pending['request']['context']['floor_id']:
                snapshot = self._timing_snapshot()
                timed_floor = snapshot.get('floor') if snapshot else None
                next_floor = receipt['next_context']['floor_id']
                if not timed_floor or timed_floor['id'] != next_floor:
                    if timed_floor:
                        self._timing_event('complete_floor', floor_id=pending['request']['context']['floor_id'])
                    ending = pending['outcome_request']['state']
                    node = ending.get('facts', {}).get('node_type')
                    kind = node if node in {'elite', 'boss'} else 'combat' if node == 'enemy' else 'noncombat'
                    self._timing_event('begin_floor', floor_id=next_floor, kind=kind, observed_from_entry=True)
            continuity = self.state.get('evidence_continuity')
            if continuity:
                continuity['context'] = deepcopy(receipt['next_context'])
            self.state["pending"] = None
            self.state["completed"] += 1
            self._save()
            if complete:
                self._begin_timed_move('after-' + pending['action_id'])
            self._timing_event('phase', name='planning')
        except BaseException:
            # The SQLite receipt may already exist. Retain the same verified
            # pending operation in memory too, so a failed final save can only
            # finalize idempotently or be recovered from disk, never replayed.
            self.state = retained
            if self.decision_policy == 'strict':
                self.disarm()
            raise
        verified_state = pending['outcome_request']['state']
        terminal = None
        if verified_state.get('ui', {}).get('screen') == 'result':
            facts = verified_state.get('facts', {})
            terminal = facts.get('run_outcome') if facts.get('run_outcome') in {'victory', 'defeat'} else None
            if facts.get('combat_outcome') == 'loss':
                terminal = 'defeat'
        proof = pending.get('outcome_verification', {})
        if terminal is not None:
            self.close()
            return {'status': 'run_complete', 'reason': terminal, 'next_context': receipt['next_context'],
                    'armed': False, 'cleanup': deepcopy(self.cleanup), 'controller_input_sent': False,
                    'observed_mismatches': deepcopy(proof.get('observed_mismatches', []))}
        return {"status": "verified", "logical_action_complete": complete,
                "next_context": receipt["next_context"], "requires_fresh_review": True,
                'decision_policy': self.decision_policy, 'assessment': deepcopy(proof.get('assessment', {})),
                'observed_mismatches': deepcopy(proof.get('observed_mismatches', [])),
                'focus_transition': deepcopy(proof.get('focus_transition'))}

    def _map_inspection_budget(self, before, direction):
        """Bound nonprogressing map browsing across captures and adapter restarts."""
        scope = hashlib.sha256(json.dumps({k: before[k] for k in
            ("context", "inventory_digest", "resources", "facts")}, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        progress = self.state.get("map_inspection_progress", {})
        if progress.get("scope") != scope:
            progress = {}
        _require(progress.get("total", 0) < 64,
                 "map inspection limit reached; plan with reviewed coverage")
        _require(progress.get("without_viewport_progress", {}).get(direction, 0) < 2,
                 "map inspection made no viewport progress twice; re-plan using confirmed nodes")
        return scope, progress

    def disarm(self):
        self.armed = False
        save_error = None
        if self.state.get('evidence_continuity'):
            self.state['evidence_continuity']['active'] = False
            if not self.poisoned:
                try:
                    self._save()
                except BaseException as error:
                    save_error = error
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
        if save_error is not None:
            raise save_error

    def close(self):
        if not self.closed:
            try:
                self.disarm()
            finally:
                snapshot = self._timing_snapshot()
                if snapshot is not None and snapshot.get('pause') is None:
                    self._timing_event('pause', category='paused', reason='Reviewed adapter closed; no active play loop.')
                self.closed = True
                self.lock.close()

    def handle(self, request):
        op = request.get('operation') if isinstance(request, dict) else None
        if op in {'bridge_preflight', 'arm'}:
            self._timing_event('phase', name='preflight')
        if op in {'verify', 'finalize'}:
            self._timing_event('phase', name='verification' if op == 'verify' else 'telemetry')
        try:
            if op == 'timing':
                _require(set(request) == {'operation', 'event'} and isinstance(request['event'], dict),
                         'timing needs one explicit progress event')
                event = dict(request['event'])
                operation = event.pop('operation', None)
                _require(operation in {'begin_move', 'begin_floor', 'phase', 'pause', 'resume', 'stale_capture'},
                         'operator timing cannot assert verified input or completion')
                self._timing_event(operation, **event)
                return {'status': 'timing_recorded', 'timing': self.timing_summary(), 'controller_input_sent': False}
            result = self._handle(request)
            if op == 'arm':
                snapshot = self._timing_snapshot()
                if snapshot is not None and snapshot.get('pause') is not None:
                    self._timing_event('resume')
                self._begin_timed_move('after-arm-' + str(uuid4()))
                self._timing_event('phase', name='planning')
            return {**result, 'timing': self.timing_summary()}
        except BaseException as error:
            if isinstance(error, (ValueError, RuntimeError)):
                self._timing_event('failure', code=type(error).__name__, reason=str(error)[:500])
                if 'stale' in str(error).casefold() and isinstance(request, dict):
                    source = request.get('source', request.get('after', {}).get('source', {}))
                    identifier = source.get('captured_at', source.get('sha256', 'unidentified'))
                    self._timing_event('stale_capture', capture_id=identifier)
            if self.decision_policy == 'learning' and isinstance(error, Exception):
                return self.recoverable_error(error)
            raise

    def recoverable_error(self, error):
        """Report a local recovery step without another input or implicit replay."""
        if self.decision_policy != 'learning':
            self.disarm()
            return {'status': 'stopped_for_review', 'error': str(error), 'armed': False,
                    'cleanup': deepcopy(self.cleanup), 'timing': self.timing_summary()}
        if self.transport_failed:
            self.close()
            status, required = 'transport_stopped', 'Restore the hardware/network connection; inspect any pending outcome before rearming.'
        elif self.poisoned:
            status, required = 'storage_recovery_required', 'Recover durable session storage, reopen and reconcile the pending action; never resend it.'
        else:
            status, required = 'recoverable_review', 'Inspect a fresh frame, correct the reviewed request and continue in this armed session.'
        pending = self.state.get('pending')
        next_operation = 'prepare'
        if pending:
            state = pending.get('status')
            next_operation = {'prepared': 'cancel_prepared', 'preparing_dispatch': 'recover_unsent',
                              'attempted': 'verify', 'verified_pending_log': 'finalize'}.get(state, 'summary')
            required = {'prepared': 'Cancel the proven-unsent proposal, then review and prepare the next action.',
                'preparing_dispatch': 'Use recover_unsent for the durable pre-dispatch record; no input may be repeated.',
                'attempted': 'Inspect a fresh result and verify or reconcile this exact pending action; never resend it.',
                'verified_pending_log': 'Finalize the retained verified result; do not repeat the gameplay input.'}.get(state, required)
        delivery = False
        if pending and pending['status'] in {'attempted', 'verified_pending_log'}:
            response = pending.get('response', {})
            delivery = True if response.get('ok') is True else False if response.get('status') == 'not_sent' else None
        return {'status': status, 'error': str(error), 'armed': self.armed,
            'decision_policy': self.decision_policy, 'controller_input_sent': delivery, 'input_retried': False,
            'pending': None if pending is None else {'action_id': pending['action_id'], 'status': pending['status']},
            'must_not_repeat': bool(pending and pending['status'] != 'prepared'),
            'next_operation': next_operation, 'required': required,
            'cleanup': deepcopy(self.cleanup), 'timing': self.timing_summary()}

    def _handle(self, request):
        request = _copy(request)
        try:
            op = request.get("operation")
            _require(not self.poisoned or op in {"stop", "summary"}, "session storage failed; reopen and reconcile")
            if op == "summary":
                return self.summary()
            if op == 'invalidate_evidence':
                return self.invalidate_evidence(request.get('reason'))
            if op == "bridge_preflight":
                return self.bridge_preflight()
            if op == "arm":
                return self.arm(request)
            if op == "prepare":
                return self.prepare(request)
            if op == "execute":
                return self.execute(request)
            if op == "resume":
                return self.resume(request)
            if op == "send":
                return self.send(request["action_id"])
            if op == "verify":
                return self.verify(request)
            if op == "finalize":
                return self.finalize()
            if op == "repair_outcome_metadata":
                return self.repair_outcome_metadata(request)
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
            if self.decision_policy == 'strict':
                self.disarm()
            raise
