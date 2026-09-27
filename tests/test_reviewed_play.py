"""Real temporary SQLite/PNG files, explicit synthetic facts, fake controller.

No capture, hardware, model or actual-run writes. These are connector contract
tests and make no claim that the fixture facts were recognized from pixels.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4
import zlib

from tests.test_execution import context, reading
from tests.test_choice_execution import observation as choice_observation, choice as choice_goal
from veda.execution import ARM_PHRASE
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import ReviewedPlaySession, RuntimeStop, inventory_digest
from veda.telemetry_database import TelemetryDatabase


def png_bytes(color):
    """A small valid RGB PNG built locally without an imaging dependency."""
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    width = height = 8
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + bytes(color) * width) * height)) + chunk(b"IEND", b""))


class FakeController:
    def __init__(self, *, ready=True, uncertain=False, before_tap=None):
        self.ready, self.uncertain, self.before_tap = ready, uncertain, before_tap
        self.calls = []
        self.closed = False

    @property
    def inputs(self):
        return [c for c in self.calls if c["action"] == "tap"]

    def call(self, command):
        self.calls.append(deepcopy(command))
        if command["action"] == "status":
            return {"ok": True, "on": True, "session_ready": self.ready, "request_id": command["request_id"]}
        if command["action"] == "tap":
            if self.before_tap:
                self.before_tap(command)
            if self.uncertain:
                return {"ok": False, "status": "unknown_outcome", "request_id": command["request_id"]}
        return {"ok": True, "status": "ok", "request_id": command["request_id"]}

    def close(self):
        self.closed = True


class ReviewedPlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = TelemetryDatabase(self.root / "synthetic.sqlite3")
        run = self.db.start_or_resume_run(ascension=0)
        floor = self.db.record_floor(run_id=run, act=1, floor=1, node_type="combat", outcome=None)
        combat = self.db.start_combat(run_id=run, floor_id=floor, opening_state={}, encounter_name="Cultist", encounter_type="enemy")
        turn = self.db.start_combat_turn(combat_id=combat, turn_number=1, phase="player", opening_state={})
        self.context_ids = {"run_id": run, "floor_id": floor, "combat_id": combat, "turn_id": turn}
        self.db.record_inventory_baseline(run_id=run, floor_id=floor, items=[],
            coverage={"card": "unknown", "relic": "complete", "potion": "complete"}, source="synthetic fixture")
        self.telemetry = PlayTelemetry(self.db)
        # The shared advisory validator intentionally uses wall time; keep all
        # synthetic observations in the recent past and date only this temp DB.
        self.base_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        earlier = (self.base_time - timedelta(seconds=1)).isoformat()
        with self.db._connection() as con:
            con.execute("UPDATE runs SET started_at=?", (earlier,))
            con.execute("UPDATE combats SET opened_at=?", (earlier,))
            con.execute("UPDATE combat_turns SET opened_at=?", (earlier,))
            con.execute("UPDATE inventory_baselines SET observed_at=?", (earlier,))
        self.now = self.base_time
        self.controller = FakeController()
        self.factories = 0
        self.session = None
        self.addCleanup(self.close_session)
        self.before = self.combat_request(0)

    def close_session(self):
        if self.session:
            self.session.close()

    def factory(self):
        self.factories += 1
        return self.controller

    def create(self, mode="codex"):
        self.session = ReviewedPlaySession(self.root / "session", run_id=self.context_ids["run_id"],
            telemetry=self.telemetry, mode=mode, controller_factory=self.factory, clock=lambda: self.now)
        return self.session

    def source(self, number):
        path = self.root / f"fixture-{number}.png"
        if not path.exists():
            path.write_bytes(png_bytes((number % 255, 20, 60)))
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "captured_at": (self.base_time + timedelta(seconds=number)).isoformat(),
            "origin": "reviewer", "evidence_note": "Synthetic reviewed declaration; image pixels do not encode game facts."}

    def combat_request(self, number, *, focus="d", game=None, ui_override=None):
        source = self.source(number)
        c = deepcopy(game) if game else context()
        c["state"]["observed_at"] = source["captured_at"]
        ui = {"screen_type": "combat", "phase": "hand", "hand_order": [c["id"] for c in c["state"]["hand"]],
              "focused_card_id": focus, "selected_card_id": None, "focused_target_id": None, **(ui_override or {})}
        data = reading(c, ui, turn=self.context_ids["turn_id"])
        data.update(run_id=self.context_ids["run_id"], floor_id=self.context_ids["floor_id"], frame_id=f"frame-{number}", image_sha256=source["sha256"])
        return {"operation": "prepare", "kind": "combat", "source": source, "context": dict(self.context_ids),
            "reading": data, "review": {"complete": True, "reviewer": "synthetic fixture only", "frame_id": f"frame-{number}", "image_sha256": source["sha256"]},
            "plan": {"steps": [{"kind": "card", "card_id": "s", "target": "enemy"}]},
            "reasoning": "Exercise only reviewed navigation/SQLite contracts."}

    def arm_request(self):
        return {"operation": "arm", "phrase": ARM_PHRASE, "run_id": self.context_ids["run_id"],
                "source": self.before["source"], "review": self.before["review"], "frame_id": "frame-0",
                "game": "Slay the Spire", "screen": "combat", "exclusive_client_confirmed": True}

    def arm(self):
        return self.session.handle(self.arm_request())

    def prepare(self):
        return self.session.handle(self.before)

    def send(self, prepared):
        return self.session.handle({"operation": "send", "action_id": prepared["action_id"]})

    def verify_navigation(self, prepared, number=1):
        self.now = self.base_time + timedelta(seconds=number)
        after = self.combat_request(number, focus="s")
        return self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "after": after, "telemetry": {"zone_coverage": "complete"}})

    def decisions(self):
        with self.db._connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM decisions ORDER BY rowid")]

    def choice_request(self, number, *, wanted="0"):
        source = self.source(number)
        obs = choice_observation()
        obs["context"] = dict(self.context_ids)
        obs["frame"] = {"frame_id": f"frame-{number}", "image_sha256": source["sha256"], "observed_at": source["captured_at"]}
        obs["review"].update(frame_id=f"frame-{number}", image_sha256=source["sha256"])
        inventory = context()["inventory"]
        obs["inventory_digest"] = inventory_digest(inventory)
        return {"operation": "prepare", "kind": "choice", "source": source, "context": dict(self.context_ids),
            "observation": obs, "inventory": inventory, "choice": choice_goal(obs, [wanted]),
            "review": dict(obs["review"]), "reasoning": "Synthetic reviewed choice; not game recognition."}

    def choice_after(self, prepared, number=1, *, navigation=False):
        after = self.choice_request(number)
        before = self.session.state["pending"]["request"]
        obs = after["observation"]
        obs["facts"] = deepcopy(before["observation"]["facts"])
        if navigation:
            obs["ui"]["focused_id"] = "1"
        else:
            post = before["choice"]["postconditions"]
            obs["facts"].update(post["facts"])
            obs["ui"].update(screen=post["screen"], phase=post["phase"], choice_id="result-1", options=[], order=[],
                focused_id=None, selected_ids=[], pending_ids=[], required_count=0, navigation=[])
            obs["review"]["outcome"] = {"action_id": prepared["action_id"], "before_frame_id": before["observation"]["frame"]["frame_id"],
                "before_sha256": before["source"]["sha256"], "choice_id": before["choice"]["choice_id"],
                "option_ids": before["choice"]["option_ids"], "observed_result": "Synthetic explicit result, independently reviewed fixture."}
        return after

    def test_shadow_prepares_and_summarizes_without_controller_or_database_decision(self):
        session = self.create("shadow")
        self.assertFalse(session.summary()["armed"])
        prepared = self.prepare()
        self.assertEqual("prepared", prepared["status"])
        self.assertFalse(prepared["controller_input_sent"])
        with self.assertRaisesRegex(RuntimeStop, "shadow"):
            self.arm()
        with self.assertRaisesRegex(RuntimeStop, "not armed"):
            self.send(prepared)
        self.assertEqual(0, self.factories)
        self.assertEqual([], self.decisions())
        self.assertEqual([], self.controller.calls)

    def test_current_run_arm_opens_status_only_and_needs_ready_bridge(self):
        self.create()
        result = self.arm()
        self.assertEqual("armed_codex_reviewed", result["status"])
        self.assertEqual(["status"], [c["action"] for c in self.controller.calls])
        self.assertEqual([], self.controller.inputs)
        self.assertFalse(result["runtime_authorized"])

    def test_unready_bridge_or_bad_arming_has_no_input(self):
        self.controller.ready = False
        self.create()
        with self.assertRaisesRegex(RuntimeStop, "not ready"):
            self.arm()
        self.assertFalse(self.session.armed)
        self.assertEqual([], self.controller.inputs)
        self.assertTrue(self.controller.closed)
        bad = self.arm_request(); bad["phrase"] = "yes"
        with self.assertRaisesRegex(RuntimeStop, "arming"):
            self.session.handle(bad)
        self.assertEqual(1, self.factories)

    def test_real_pending_decision_and_durable_attempt_exist_before_fake_input(self):
        self.create(); self.arm()
        observed = []
        def verify_boundary(command):
            pending = self.telemetry.recover(run_id=self.context_ids["run_id"])["pending"]
            saved = json.loads(self.session.path.read_text())["pending"]
            observed.append((len(pending), saved["status"], saved["action_id"] == command["request_id"]))
        self.controller.before_tap = verify_boundary
        prepared = self.prepare()
        self.assertEqual([], self.decisions())
        self.send(prepared)
        self.assertEqual([(1, "attempted", True)], observed)
        self.assertEqual(["left"], self.controller.inputs[0]["buttons"])
        self.assertEqual("recommended", self.decisions()[0]["status"])

    def test_fresh_navigation_verifies_and_resolves_sqlite_once(self):
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        result = self.verify_navigation(prepared)
        self.assertEqual("verified", result["status"])
        self.assertFalse(result["logical_action_complete"])
        self.assertEqual("resolved", self.decisions()[0]["status"])
        self.assertIsNone(self.session.summary()["pending"])
        self.assertEqual(1, self.session.summary()["completed_inputs"])
        self.assertEqual(1, len(self.controller.inputs))
        with self.assertRaisesRegex(RuntimeStop, "never repeat"):
            self.send(prepared)
        self.assertEqual(1, len(self.controller.inputs))

    def test_unknown_delivery_remains_pending_on_restart_and_cannot_rearm_or_repeat(self):
        self.controller.uncertain = True
        self.create(); self.arm(); prepared = self.prepare()
        with self.assertRaisesRegex(RuntimeStop, "delivery uncertain"):
            self.send(prepared)
        self.assertEqual(1, len(self.controller.inputs))
        self.assertFalse(self.session.armed)
        self.assertEqual("attempted", self.session.state["pending"]["status"])
        self.session.close(); self.create()
        with self.assertRaisesRegex(RuntimeStop, "pending"):
            self.arm()
        with self.assertRaisesRegex(RuntimeStop, "not armed"):
            self.send(prepared)
        self.assertEqual(1, len(self.controller.inputs))
        self.assertEqual(1, len(self.telemetry.recover(run_id=self.context_ids["run_id"])["pending"]))
        self.assertEqual("verified", self.verify_navigation(prepared)["status"])

    def test_stale_or_changed_retained_source_stops_before_decision_and_input(self):
        self.create(); self.arm(); prepared = self.prepare()
        self.now += timedelta(seconds=31)
        with self.assertRaisesRegex(RuntimeStop, "stale"):
            self.send(prepared)
        self.assertEqual([], self.controller.inputs)
        self.assertEqual([], self.decisions())

    def test_source_retention_is_real_and_mutated_retained_bytes_cannot_send(self):
        self.create(); self.arm(); prepared = self.prepare()
        retained = Path(self.session.state["pending"]["request"]["source"]["path"])
        self.assertTrue(retained.is_file())
        self.assertNotEqual(retained, Path(self.before["source"]["path"]))
        retained.write_bytes(png_bytes((80, 80, 80)))
        with self.assertRaisesRegex(RuntimeStop, "bytes changed"):
            self.send(prepared)
        self.assertEqual([], self.controller.inputs)
        self.assertEqual([], self.decisions())

    def test_navigation_unexpected_game_effect_stays_unresolved(self):
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        game = context(); game["state"]["hp"] = 39
        after = self.combat_request(1, focus="s", game=game)
        with self.assertRaisesRegex(RuntimeStop, "navigation verification mismatch"):
            self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after})
        self.assertFalse(self.session.armed)
        self.assertEqual("recommended", self.decisions()[0]["status"])
        self.assertEqual(1, len(self.controller.inputs))

    def test_after_capture_must_follow_dispatch(self):
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        after = self.combat_request(1, focus="s")
        after["source"]["captured_at"] = self.base_time.isoformat()
        after["reading"]["context"]["state"]["observed_at"] = self.base_time.isoformat()
        with self.assertRaisesRegex(RuntimeStop, "after dispatch"):
            self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after})
        self.assertEqual("recommended", self.decisions()[0]["status"])

    def test_database_failure_before_commit_never_sends_and_can_recover_unsent(self):
        self.create(); self.arm(); prepared = self.prepare()
        with patch.object(self.telemetry, "record_decision", side_effect=OSError("synthetic database failure")):
            with self.assertRaisesRegex(OSError, "database failure"):
                self.send(prepared)
        self.assertEqual([], self.controller.inputs)
        self.assertEqual("preparing_dispatch", self.session.state["pending"]["status"])
        self.assertEqual("reconciled_unsent", self.session.handle({"operation": "recover_unsent"})["status"])
        self.assertIsNone(self.session.state["pending"])
        self.assertEqual([], self.decisions())

    def test_database_outcome_commit_then_return_failure_finalizes_without_repeat(self):
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        original = self.telemetry.record_outcome
        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic response lost after commit")
        with patch.object(self.telemetry, "record_outcome", side_effect=commit_then_fail):
            with self.assertRaisesRegex(OSError, "response lost"):
                self.verify_navigation(prepared)
        self.assertEqual("resolved", self.decisions()[0]["status"])
        self.assertEqual("verified_pending_log", self.session.state["pending"]["status"])
        self.session.close(); self.create()
        self.now += timedelta(days=1)  # Historical idempotent receipt; never permission to input.
        final = self.session.handle({"operation": "finalize"})
        self.assertEqual("verified", final["status"])
        self.assertEqual(1, self.session.summary()["completed_inputs"])
        with self.db._connection() as con:
            self.assertEqual(1, con.execute("SELECT COUNT(*) FROM evidence_events WHERE kind='play_outcome'").fetchone()[0])
        self.assertEqual(1, len(self.controller.inputs))

    def test_session_save_failure_after_sqlite_commit_recovers_from_disk_exactly_once(self):
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        original = self.session._save
        def fail_final_save():
            if self.session.state["pending"] is None:
                raise OSError("synthetic final state save failure")
            original()
        with patch.object(self.session, "_save", side_effect=fail_final_save):
            with self.assertRaisesRegex(OSError, "final state"):
                self.verify_navigation(prepared)
        disk = json.loads(self.session.path.read_text())
        self.assertEqual("verified_pending_log", disk["pending"]["status"])
        self.assertEqual("resolved", self.decisions()[0]["status"])
        self.session.close(); self.create()
        self.assertEqual("verified", self.session.handle({"operation": "finalize"})["status"])
        self.assertEqual(1, self.session.summary()["completed_inputs"])
        self.assertEqual(1, len(self.controller.inputs))

    def test_exclusive_session_lock_and_wrong_run_reopen_fail(self):
        self.create()
        with self.assertRaisesRegex(RuntimeStop, "owner"):
            ReviewedPlaySession(self.root / "session", run_id=self.context_ids["run_id"], telemetry=self.telemetry)
        self.session.close()
        with self.assertRaisesRegex(RuntimeStop, "another schema or run"):
            ReviewedPlaySession(self.root / "session", run_id="different-run", telemetry=self.telemetry)

    def test_committed_choice_is_verified_and_logged_with_no_arbitrary_input(self):
        self.before = self.choice_request(0)
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.assertEqual(["cross"], self.controller.inputs[0]["buttons"])
        self.now += timedelta(seconds=1)
        result = self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "after": self.choice_after(prepared)})
        self.assertTrue(result["logical_action_complete"])
        self.assertEqual("resolved", self.decisions()[0]["status"])
        self.assertEqual(1, len(self.controller.inputs))

    def test_choice_navigation_cannot_smuggle_inventory_mutation(self):
        self.before = self.choice_request(0, wanted="2")
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        after = self.choice_after(prepared, navigation=True)
        mutations = {"inventory_events": [{"kind": "potion", "action": "acquired", "item": "Fire Potion", "evidence_note": "Not a navigation effect."}]}
        with self.assertRaisesRegex(RuntimeStop, "cannot assert gameplay"):
            self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
                                 "after": after, "telemetry": mutations})
        self.assertEqual("recommended", self.decisions()[0]["status"])
        with self.db._connection() as con:
            self.assertEqual(0, con.execute("SELECT COUNT(*) FROM inventory_events").fetchone()[0])

    def test_changed_inventory_without_matching_telemetry_cannot_resolve_commit(self):
        self.before = self.choice_request(0)
        expected = deepcopy(self.before["inventory"])
        expected["current"]["potion"] = ["Fire Potion"]
        self.before["choice"]["postconditions"]["inventory_digest"] = inventory_digest(expected)
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        after = self.choice_after(prepared)
        after["inventory"] = expected
        after["observation"]["inventory_digest"] = inventory_digest(expected)
        with self.assertRaisesRegex(RuntimeStop, "change needs explicit telemetry"):
            self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after})
        self.assertEqual("recommended", self.decisions()[0]["status"])

    def test_corresponding_source_reviewed_inventory_event_commits_with_choice(self):
        self.before = self.choice_request(0)
        expected = deepcopy(self.before["inventory"])
        expected["current"]["potion"] = ["Fire Potion"]
        self.before["choice"]["postconditions"]["inventory_digest"] = inventory_digest(expected)
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        after = self.choice_after(prepared)
        after["inventory"] = expected
        after["observation"]["inventory_digest"] = inventory_digest(expected)
        changes = {"inventory_events": [{"kind": "potion", "action": "acquired", "item": "Fire Potion", "evidence_note": "Observed in reviewed inventory."}]}
        after["mutation_review"] = {**after["review"], "changes": changes}
        result = self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after, "telemetry": changes})
        self.assertEqual("verified", result["status"])
        self.assertEqual(["Fire Potion"], self.db.inventory_ledger(run_id=self.context_ids["run_id"])["current"]["potion"])

    def test_unknown_reconciliation_persists_uncertainty_without_resending(self):
        self.controller.uncertain = True
        self.create(); self.arm(); prepared = self.prepare()
        with self.assertRaises(RuntimeStop):
            self.send(prepared)
        self.now += timedelta(seconds=1)
        fresh = self.combat_request(1)
        result = self.session.handle({"operation": "reconcile", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "status": "unknown", "source": fresh["source"], "review": fresh["review"], "frame_id": "frame-1", "state": {}})
        self.assertEqual("unresolved", result["status"])
        self.assertTrue(result["must_not_repeat"])
        self.assertEqual("recommended", self.decisions()[0]["status"])
        self.assertEqual(1, len(self.controller.inputs))

    def test_unchanged_screen_cannot_claim_not_performed_after_uncertain_dispatch(self):
        self.controller.uncertain = True
        self.create(); self.arm(); prepared = self.prepare()
        with self.assertRaises(RuntimeStop):
            self.send(prepared)
        self.now += timedelta(seconds=1)
        fresh = self.combat_request(1)
        with self.assertRaisesRegex(RuntimeStop, "does not prove non-delivery"):
            self.session.handle({"operation": "reconcile", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
                "status": "not_performed", "source": fresh["source"], "review": fresh["review"], "frame_id": "frame-1", "state": {}})
        self.assertEqual("recommended", self.decisions()[0]["status"])

    def test_decision_commit_then_return_failure_needs_fresh_review_before_unsent_resolution(self):
        self.create(); self.arm(); prepared = self.prepare()
        original = self.telemetry.record_decision
        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic prepare return lost")
        with patch.object(self.telemetry, "record_decision", side_effect=commit_then_fail):
            with self.assertRaisesRegex(OSError, "return lost"):
                self.send(prepared)
        self.assertEqual([], self.controller.inputs)
        self.assertEqual("recommended", self.decisions()[0]["status"])
        self.session.close(); self.create()
        self.now += timedelta(seconds=1)
        fresh = self.combat_request(1)
        result = self.session.handle({"operation": "recover_unsent", "source": fresh["source"], "review": fresh["review"],
            "frame_id": "frame-1", "state": {}})
        self.assertEqual("reconciled_unsent", result["status"])
        self.assertEqual("skipped", self.decisions()[0]["status"])
        self.assertEqual([], self.controller.inputs)

    def test_unknown_result_can_later_verify_without_repeating_input(self):
        self.controller.uncertain = True
        self.create(); self.arm(); prepared = self.prepare()
        with self.assertRaises(RuntimeStop):
            self.send(prepared)
        self.now += timedelta(seconds=1)
        fresh = self.combat_request(1)
        self.session.handle({"operation": "reconcile", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "status": "unknown", "source": fresh["source"], "review": fresh["review"], "frame_id": "frame-1", "state": {}})
        self.assertEqual("verified", self.verify_navigation(prepared, number=2)["status"])
        self.assertEqual("resolved", self.decisions()[0]["status"])
        self.assertEqual(1, len(self.controller.inputs))

    def test_observed_end_turn_records_transition_and_returns_canonical_turn_id(self):
        self.before = self.combat_request(0, focus=None)
        self.before["plan"] = {"steps": [{"kind": "end_turn"}]}
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.assertEqual(["triangle"], self.controller.inputs[0]["buttons"])
        self.now += timedelta(seconds=1)
        game = context(); game["state"].update(hp=34, energy=3)
        after = self.combat_request(1, game=game)
        after["context"]["turn_id"] = after["reading"]["turn_id"] = "reviewed-next-turn"
        changes = {"transitions": [
            {"kind": "end_turn", "closing_state": {"energy": 1}, "evidence_note": "Previous turn ended."},
            {"kind": "start_turn", "turn_number": 2, "phase": "player", "opening_state": {"hp": 34, "energy": 3}, "evidence_note": "New turn and state observed."}]}
        after["mutation_review"] = {**after["review"], "changes": changes}
        result = self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "after": after, "telemetry": changes})
        self.assertTrue(result["logical_action_complete"])
        canonical = result["next_context"]["turn_id"]
        self.assertNotEqual("reviewed-next-turn", canonical)
        self.assertNotEqual(self.context_ids["turn_id"], canonical)
        with self.db._connection() as con:
            self.assertEqual(2, con.execute("SELECT turn_number FROM combat_turns WHERE id=?", (canonical,)).fetchone()[0])

    def test_combat_resolution_checks_effect_and_logs_exact_observed_card_movement(self):
        self.before = self.combat_request(0, focus="s", ui_override={"phase": "targeting", "selected_card_id": "s",
            "focused_target_id": "enemy", "target_order": ["enemy"]})
        self.db.start_combat_zones(combat_id=self.context_ids["combat_id"], deck=["Strike", "Defend"], hand=["Strike", "Defend"], source="synthetic opening")
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        game = context()
        game["state"]["hand"] = game["state"]["hand"][1:]
        game["state"]["energy"] = 0
        game["state"]["enemies"][0]["hp"] = 6
        after = self.combat_request(1, focus="d", game=game)
        changes = {"zone_coverage": "complete", "zone_events": [
            {"kind": "play", "card_name": "Strike", "from_zone": "hand", "to_zone": "discard", "evidence_note": "Selected copy left hand and discard inspected."}]}
        after["mutation_review"] = {**after["review"], "changes": changes}
        result = self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "after": after, "telemetry": changes})
        self.assertTrue(result["logical_action_complete"])
        zones = self.db.combat_zone_state(combat_id=self.context_ids["combat_id"])
        self.assertTrue(zones["known"])
        self.assertEqual(["Strike"], zones["zones"]["discard"])
        self.assertEqual(["Defend"], zones["zones"]["hand"])

    def terminal_after(self, prepared, *, floor_jump=False):
        after = self.choice_request(1)
        after["context"].update(combat_id=None, turn_id=None)
        if floor_jump:
            after["context"]["floor_id"] = "unobserved-next-floor"
        obs = after["observation"]
        obs["context"] = deepcopy(after["context"])
        obs["ui"].update(screen="reward", phase="choose")
        obs["facts"] = {"combat_outcome": "win"}
        obs["resources"] = {"hp": 40, "gold": 100, "energy": 0}
        obs["review"]["outcome"] = {"action_id": prepared["action_id"], "before_frame_id": "frame-0",
            "before_sha256": self.before["source"]["sha256"], "action": self.before["plan"]["steps"][0], "observed_result": "Reward screen confirms combat win."}
        changes = {"transitions": [{"kind": "end_turn", "closing_state": {}, "evidence_note": "Combat turn ended."},
            {"kind": "end_combat", "outcome": "victory", "closing_state": {"hp": 40}, "evidence_note": "Victory reward observed."}]}
        if floor_jump:
            changes["transitions"].append({"kind": "advance_floor", "act": 1, "floor": 2, "node_type": "rest",
                "previous_outcome": "completed", "previous_ending_state": {}, "starting_state": {}, "evidence_note": "Unrelated jump."})
        after["mutation_review"] = {**after["review"], "changes": changes}
        return after, changes

    def lethal_prepare(self):
        game = context(); game["state"]["enemies"][0]["hp"] = 6
        self.before = self.combat_request(0, focus="s", game=game, ui_override={"phase": "targeting", "selected_card_id": "s",
            "focused_target_id": "enemy", "target_order": ["enemy"]})
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        return prepared

    def test_combat_to_reward_closes_actual_rows_and_returns_same_floor_context(self):
        prepared = self.lethal_prepare()
        after, changes = self.terminal_after(prepared)
        result = self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "after": after, "telemetry": changes})
        self.assertEqual({**self.context_ids, "combat_id": None, "turn_id": None}, result["next_context"])
        self.assertTrue(result["logical_action_complete"])
        with self.db._connection() as con:
            self.assertIsNotNone(con.execute("SELECT closed_at FROM combats").fetchone()[0])
            self.assertIsNotNone(con.execute("SELECT closed_at FROM combat_turns").fetchone()[0])
            self.assertEqual(1, con.execute("SELECT COUNT(*) FROM floors").fetchone()[0])

    def test_combat_reward_cannot_claim_an_unobserved_floor_jump(self):
        prepared = self.lethal_prepare()
        after, changes = self.terminal_after(prepared, floor_jump=True)
        with self.assertRaisesRegex(RuntimeStop, "combat-end boundary"):
            self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
                "after": after, "telemetry": changes})
        self.assertEqual("recommended", self.decisions()[0]["status"])
        with self.db._connection() as con:
            self.assertIsNone(con.execute("SELECT closed_at FROM combats").fetchone()[0])

    def selection_boundary(self, name, cost, card_type, extra):
        game = context()
        game["state"]["hand"][0].update(name=name, cost=cost, type=card_type, upgraded=name.endswith("+"), title_color="green" if name.endswith("+") else "white")
        game["state"]["piles"]["discard"] = ["Strike"]
        self.before = self.combat_request(0, focus="s", game=game, ui_override={"phase": "targeting" if card_type == "Attack" else "card_selected",
            "selected_card_id": "s", "focused_target_id": "enemy" if card_type == "Attack" else None, "target_order": ["enemy"]})
        self.before["plan"]["steps"][0].update(extra)
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        after = self.choice_request(1)
        obs = after["observation"]
        obs["facts"] = {"selection_cause_card_id": "s"}
        obs["resources"] = {"hp": 40, "energy": 1 - cost}
        obs["review"]["outcome"] = {"action_id": prepared["action_id"], "before_frame_id": "frame-0",
            "before_sha256": self.before["source"]["sha256"], "action": self.before["plan"]["steps"][0], "observed_result": "Card caused the visible selection prompt."}
        for mutation in (lambda o: o["facts"].update(selection_cause_card_id="wrong"),
                         lambda o: o["resources"].update(energy=9),
                         lambda o: o["review"]["outcome"].update(action_id="wrong")):
            bad = deepcopy(after); mutation(bad["observation"])
            with self.assertRaises(RuntimeStop):
                self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": bad})
        result = self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after})
        self.assertTrue(result["logical_action_complete"])
        self.assertEqual(self.context_ids, result["next_context"])
        self.assertEqual("resolved", self.decisions()[0]["status"])
        self.assertEqual(1, len(self.controller.inputs))

    def test_headbutt_selection_checks_cause_energy_and_source_correlation(self):
        self.selection_boundary("Headbutt", 1, "Attack", {"return_card": "Strike"})

    def test_warcry_selection_checks_cause_energy_and_source_correlation(self):
        self.selection_boundary("Warcry", 0, "Skill", {})

    def test_true_grit_plus_selection_checks_cause_energy_and_source_correlation(self):
        self.selection_boundary("True Grit+", 1, "Skill", {"exhaust_card_id": "d"})

    def metadata_change_without_telemetry(self, field, value):
        self.before = self.choice_request(0)
        expected = deepcopy(self.before["inventory"])
        expected[field] = value
        self.before["choice"]["postconditions"]["inventory_digest"] = inventory_digest(expected)
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        after = self.choice_after(prepared)
        after["inventory"] = expected
        after["observation"]["inventory_digest"] = inventory_digest(expected)
        with self.assertRaisesRegex(RuntimeStop, "inventory change needs explicit telemetry"):
            self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after})
        self.assertEqual("recommended", self.decisions()[0]["status"])

    def test_coverage_change_without_baseline_is_not_incidental_metadata(self):
        self.metadata_change_without_telemetry("coverage", {"relic": "complete", "potion": "complete", "card": "complete"})

    def test_property_change_without_event_is_not_incidental_metadata(self):
        self.metadata_change_without_telemetry("properties", {"relic:test": {"property": "Different effect", "source": "fixture"}})

    def test_transport_proven_not_sent_can_resolve_skipped_with_fresh_review(self):
        original = self.controller.call
        def no_delivery(command):
            response = original(command)
            return {"ok": False, "status": "not_sent", "request_id": command["request_id"]} if command["action"] == "tap" else response
        self.controller.call = no_delivery
        self.create(); self.arm(); prepared = self.prepare()
        with self.assertRaisesRegex(RuntimeStop, "delivery uncertain"):
            self.send(prepared)
        self.now += timedelta(seconds=1)
        fresh = self.combat_request(1)
        result = self.session.handle({"operation": "reconcile", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
            "status": "not_performed", "source": fresh["source"], "review": fresh["review"], "frame_id": "frame-1", "state": {}})
        self.assertEqual("verified", result["status"])
        self.assertFalse(result["logical_action_complete"])
        self.assertEqual("skipped", self.decisions()[0]["status"])
        self.assertEqual(1, len(self.controller.inputs))

    def cleanup_fallback(self, *, second_failure=False):
        primary = self.controller
        primary.uncertain = True
        cleanup = FakeController()
        first_call = primary.call
        def broken_after_input(command):
            if command["action"] == "close":
                primary.calls.append(deepcopy(command))
                raise OSError("original socket is unusable")
            return first_call(command)
        primary.call = broken_after_input
        if second_failure:
            def broken_cleanup(command):
                cleanup.calls.append(deepcopy(command))
                raise OSError("cleanup socket also unavailable")
            cleanup.call = broken_cleanup
        self.create()
        clients = iter((primary, cleanup))
        factory_calls = []
        def fresh_local_client():
            factory_calls.append(True)
            return next(clients)
        self.session.factory = fresh_local_client
        self.arm(); prepared = self.prepare()
        with self.assertRaisesRegex(RuntimeStop, "delivery uncertain"):
            self.send(prepared)
        self.assertEqual(2, len(factory_calls))
        self.assertEqual(["close"], [c["action"] for c in cleanup.calls])
        self.assertEqual([], cleanup.inputs)
        self.assertEqual(1, len(primary.inputs))
        self.assertTrue(primary.closed)
        self.assertTrue(cleanup.closed)
        report = self.session.summary()["cleanup"]
        self.assertEqual(not second_failure, report["close_acknowledged"])
        self.assertFalse(report["hardware_release_verified"])
        self.assertEqual("recommended", self.decisions()[0]["status"])
        self.session.handle({"operation": "stop"})
        self.assertEqual(2, len(factory_calls), "cleanup is bounded; stop cannot retry gameplay or reopen again")

    def test_uncertain_transport_cleanup_opens_one_close_only_client(self):
        self.cleanup_fallback()

    def test_failed_cleanup_reports_unknown_hardware_release_without_retry(self):
        self.cleanup_fallback(second_failure=True)

    def test_combat_boundary_rejects_incomplete_outer_review_and_malformed_ui(self):
        prepared = self.lethal_prepare()
        after, changes = self.terminal_after(prepared)
        for mutation in (lambda o: o["review"].update(complete=False),
                         lambda o: o["review"].pop("reviewer"),
                         lambda o: o["ui"].update(order=["2", "1", "0"]),
                         lambda o: o["ui"].update(options=None)):
            bad = deepcopy(after); mutation(bad["observation"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.session.handle({"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()),
                    "after": bad, "telemetry": changes})
            self.assertEqual("recommended", self.decisions()[0]["status"])
        with self.db._connection() as con:
            self.assertIsNone(con.execute("SELECT closed_at FROM combats").fetchone()[0])

    def baseline_choice(self, *, level, observed_cards, after_cards, accepted):
        original_cards = ["Strike", "Defend"]
        baseline_id = self.db.record_inventory_baseline(run_id=self.context_ids["run_id"], floor_id=self.context_ids["floor_id"],
            items=[{"kind": "card", "item": name} for name in original_cards],
            coverage={"card": "partial", "relic": "unknown", "potion": "unknown"}, source="synthetic prior partial inventory")
        with self.db._connection() as con:
            con.execute("UPDATE inventory_baselines SET observed_at=? WHERE id=?", ((self.base_time - timedelta(seconds=1)).isoformat(), baseline_id))
        self.before = self.choice_request(0)
        old = self.before["inventory"]
        old["current"]["card"] = original_cards
        old["coverage"]["card"] = "partial"
        self.before["observation"]["inventory_digest"] = inventory_digest(old)
        expected = deepcopy(old); expected["current"]["card"] = after_cards
        self.before["choice"]["postconditions"]["inventory_digest"] = inventory_digest(expected)
        self.create(); self.arm(); prepared = self.prepare(); self.send(prepared)
        self.now += timedelta(seconds=1)
        after = self.choice_after(prepared)
        after["inventory"] = expected
        after["observation"]["inventory_digest"] = inventory_digest(expected)
        baseline = {"items": [{"kind": "card", "item": name} for name in observed_cards],
            "coverage": {"card": level, "relic": "unknown", "potion": "unknown"},
            "evidence": {} if level == "unknown" else {"card": "Only a partial inspected card page, no deletions inferred."}}
        changes = {"inventory_baseline": baseline}
        after["mutation_review"] = {**after["review"], "changes": changes}
        request = {"operation": "verify", "action_id": prepared["action_id"], "operation_id": str(uuid4()), "after": after, "telemetry": changes}
        if accepted:
            self.assertEqual("verified", self.session.handle(request)["status"])
            self.assertEqual("resolved", self.decisions()[0]["status"])
            self.assertCountEqual(after_cards, self.db.inventory_ledger(run_id=self.context_ids["run_id"])["current"]["card"])
        else:
            with self.assertRaisesRegex(RuntimeStop, "baseline"):
                self.session.handle(request)
            self.assertEqual("recommended", self.decisions()[0]["status"])
            self.assertCountEqual(original_cards, self.db.inventory_ledger(run_id=self.context_ids["run_id"])["current"]["card"])

    def test_partial_baseline_preserves_unseen_cards_and_maximum_observed_multiplicity(self):
        self.baseline_choice(level="partial", observed_cards=["Strike", "Strike"], after_cards=["Defend", "Strike", "Strike"], accepted=True)

    def test_partial_baseline_cannot_invent_an_additional_card_copy(self):
        self.baseline_choice(level="partial", observed_cards=["Strike"], after_cards=["Defend", "Strike", "Strike"], accepted=False)

    def test_unknown_baseline_cannot_support_a_new_inventory_item(self):
        self.baseline_choice(level="unknown", observed_cards=[], after_cards=["Defend", "Strike", "Bash"], accepted=False)

    def test_unknown_baseline_cannot_smuggle_observed_items(self):
        self.baseline_choice(level="unknown", observed_cards=["Bash"], after_cards=["Defend", "Strike", "Bash"], accepted=False)


if __name__ == "__main__":
    unittest.main()
