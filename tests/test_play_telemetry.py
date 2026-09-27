"""Synthetic declarations/temp SQLite only; no capture, recognition or input."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from veda.play_telemetry import PlayTelemetry, SCHEMA
from veda.telemetry_database import TelemetryDatabase


class PlayTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = TelemetryDatabase(self.root / "test.sqlite3")
        run = self.db.start_or_resume_run(ascension=2)
        floor = self.db.record_floor(run_id=run, act=2, floor=31, node_type="combat", outcome=None)
        combat = self.db.start_combat(run_id=run, floor_id=floor, opening_state={}, encounter_name="Cultist", encounter_type="normal")
        turn = self.db.start_combat_turn(combat_id=combat, turn_number=1, phase="player", opening_state={})
        self.context = {"run_id": run, "floor_id": floor, "combat_id": combat, "turn_id": turn}
        self.db.record_inventory_baseline(run_id=run, floor_id=floor, items=[{"kind": "potion", "item": "Fire Potion"}],
            coverage={"card": "unknown", "relic": "unknown", "potion": "complete"}, source="fixture")
        self.db.start_combat_zones(combat_id=combat, deck=["Strike", "Defend"], hand=["Strike"], source="fixture")
        self.now = datetime.now(timezone.utc) + timedelta(seconds=1)
        self.api = PlayTelemetry(self.db)
        self.request = {"schema": SCHEMA, "operation_id": str(uuid4()), "context": self.context,
            "source": self.source("before", 0), "state": {"energy": 1}, "phase": "combat",
            "action": {"kind": "potion", "name": "Fire Potion", "target": "enemy-0"}, "reasoning": "Synthetic explicit fixture only."}

    def source(self, name, seconds):
        path = self.root / (name + ".png")
        path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic fixture, not recognizable pixels: " + name.encode())
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "captured_at": (self.now + timedelta(seconds=seconds)).isoformat(),
            "origin": "reviewer", "evidence_note": "Fixture assertion, not recognition."}

    def prepare(self, request=None):
        return self.api.record_decision(request or self.request, now=self.now)

    def outcome(self, ident, **overrides):
        return {"schema": SCHEMA, "operation_id": str(uuid4()), "context": dict(self.context),
            "decision_id": ident, "source": self.source("after", 1), "state": {"energy": 1},
            "status": "verified", "evidence_note": "Observed effect, not inferred from requested action.",
            "zone_coverage": "complete", **overrides}

    def resolve(self, request):
        return self.api.record_outcome(request, now=self.now + timedelta(seconds=10))

    def table_count(self, table):
        with self.db._connection() as con:
            return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def status(self, ident):
        with self.db._connection() as con:
            return con.execute("SELECT status FROM decisions WHERE id=?", (ident,)).fetchone()[0]

    def test_prepared_decision_does_not_precommit_potion_or_zone_changes(self):
        first = self.prepare()
        self.assertEqual("pending", first["status"])
        self.assertEqual("recommended", self.status(first["decision_id"]))
        self.assertEqual(["Fire Potion"], self.db.inventory_ledger(run_id=self.context["run_id"])["current"]["potion"])
        self.assertEqual(0, self.table_count("inventory_events"))
        self.assertEqual(0, self.table_count("combat_zone_events"))
        self.assertFalse(first["controller_authorized"])
        self.assertFalse(first["runtime_authorized"])

    def test_verified_atomic_outcome_uses_observation_times_and_exact_source(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], inventory_events=[{"kind": "potion", "action": "consumed",
            "item": "Fire Potion", "evidence_note": "Potion slot visibly empty and impact observed."}])
        result = self.resolve(req)
        self.assertEqual("resolved", self.status(first["decision_id"]))
        self.assertFalse(result["unresolved"])
        self.assertEqual([], self.db.inventory_ledger(run_id=self.context["run_id"])["current"]["potion"])
        with self.db._connection() as con:
            self.assertEqual(req["source"]["captured_at"], con.execute("SELECT observed_at FROM inventory_events").fetchone()[0])
            outcome = con.execute("SELECT payload_json FROM evidence_events WHERE kind='play_outcome'").fetchone()[0]
        self.assertEqual(req, json.loads(outcome)["request"])

    def test_outcome_retries_after_crash_do_not_consume_twice_even_if_source_removed(self):
        prepared = self.prepare()
        req = self.outcome(prepared["decision_id"], inventory_events=[{"kind": "potion", "action": "consumed",
            "item": "Fire Potion", "evidence_note": "Observed consumption."}])
        first = self.resolve(req)
        Path(req["source"]["path"]).unlink()
        again = PlayTelemetry(TelemetryDatabase(self.db.path)).record_outcome(req, now=self.now + timedelta(days=1))
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(first["event_id"], again["event_id"])
        self.assertEqual(1, self.table_count("inventory_events"))

    def test_decision_retry_is_not_permission_to_redispatch(self):
        first = self.prepare()
        Path(self.request["source"]["path"]).unlink()
        replay = self.api.record_decision(self.request, now=self.now + timedelta(days=1))
        self.assertEqual(first["decision_id"], replay["decision_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertTrue(replay["must_not_repeat"])
        self.assertFalse(replay["controller_authorized"])

    def test_changed_operation_content_and_cross_method_reuse_rejected(self):
        first = self.prepare()
        wrong = copy.deepcopy(self.request)
        wrong["action"]["target"] = "enemy-1"
        with self.assertRaisesRegex(ValueError, "different content"):
            self.prepare(wrong)
        req = self.outcome(first["decision_id"], operation_id=self.request["operation_id"])
        with self.assertRaisesRegex(ValueError, "different content"):
            self.resolve(req)

    def test_unknown_outcome_stays_pending_invalidates_zones_and_does_not_consume(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], status="unknown")
        result = self.resolve(req)
        self.assertTrue(result["unresolved"])
        self.assertEqual("unknown", result["zone_coverage"])
        self.assertFalse(result["zone_state_known"])
        self.assertTrue(result["inventory_requires_inspection"])
        self.assertEqual("recommended", self.status(first["decision_id"]))
        self.assertFalse(self.db.combat_zone_state(combat_id=self.context["combat_id"])["known"])
        self.assertEqual(0, self.table_count("inventory_events"))
        other = copy.deepcopy(self.request)
        other["operation_id"] = str(uuid4())
        other["source"] = self.source("next", 2)
        with self.assertRaisesRegex(ValueError, "pending"):
            self.api.record_decision(other, now=self.now + timedelta(seconds=2))

    def test_unknown_can_be_reconciled_by_later_verified_outcome_not_by_retry(self):
        first = self.prepare()
        unknown = self.outcome(first["decision_id"], status="unknown")
        self.resolve(unknown)
        verified = self.outcome(first["decision_id"], source=self.source("later", 2), inventory_events=[
            {"kind": "potion", "action": "consumed", "item": "Fire Potion", "evidence_note": "Later complete inventory inspection."}])
        self.resolve(verified)
        self.assertEqual("resolved", self.status(first["decision_id"]))
        self.assertFalse(self.db.combat_zone_state(combat_id=self.context["combat_id"])["known"])

    def test_unknown_cannot_smuggle_confirmed_mutations(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], status="unknown", inventory_events=[{"kind": "potion", "action": "consumed", "item": "Fire Potion"}])
        with self.assertRaisesRegex(ValueError, "cannot commit"):
            self.resolve(req)

    def test_recovery_retains_pending_action_without_resolving_or_input(self):
        first = self.prepare()
        recovered = PlayTelemetry(TelemetryDatabase(self.db.path)).recover(run_id=self.context["run_id"])
        self.assertEqual(first["decision_id"], recovered["pending"][0]["decision_id"])
        self.assertEqual("unknown", recovered["pending"][0]["dispatch_status"])
        self.assertTrue(recovered["must_not_repeat"])
        self.assertEqual("recommended", self.status(first["decision_id"]))

    def test_not_performed_resolves_skipped_without_game_changes(self):
        first = self.prepare()
        self.resolve(self.outcome(first["decision_id"], status="not_performed"))
        self.assertEqual("skipped", self.status(first["decision_id"]))
        self.assertEqual(0, self.table_count("inventory_events"))
        self.assertEqual(0, self.table_count("combat_zone_events"))

    def test_failed_resolution_rolls_back_inventory_and_outcome_leaving_pending(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], inventory_events=[{"kind": "potion", "action": "consumed",
            "item": "Fire Potion", "evidence_note": "Observed."}])
        with patch.object(self.db, "resolve_decision", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.resolve(req)
        self.assertEqual(0, self.table_count("inventory_events"))
        self.assertEqual("recommended", self.status(first["decision_id"]))
        self.resolve(req)
        self.assertEqual(1, self.table_count("inventory_events"))

    def test_source_changes_after_writes_rollback_resolution_and_inventory(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], inventory_events=[{"kind": "potion", "action": "consumed",
            "item": "Fire Potion", "evidence_note": "Observed."}])
        original = self.db.resolve_decision
        def mutate(**kwargs):
            original(**kwargs)
            Path(req["source"]["path"]).write_bytes(b"\x89PNG\r\n\x1a\nchanged")
        with patch.object(self.db, "resolve_decision", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "hash changed"):
                self.resolve(req)
        self.assertEqual(0, self.table_count("inventory_events"))
        self.assertEqual("recommended", self.status(first["decision_id"]))

    def test_intervening_ledger_mutation_or_wrong_context_cannot_resolve(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"])
        wrong = copy.deepcopy(req)
        wrong["context"]["turn_id"] = "other"
        with self.assertRaisesRegex(ValueError, "context conflicts"):
            self.resolve(wrong)
        self.db.record_inventory_event(run_id=self.context["run_id"], item_kind="potion", action="acquired", item_name="Blood Potion", source="external")
        with self.assertRaisesRegex(ValueError, "ledger/context changed"):
            self.resolve(req)
        self.assertEqual("recommended", self.status(first["decision_id"]))

    def test_stale_future_same_frame_and_old_capture_rejected(self):
        for seconds in (-1, 181):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, "future|stale"):
                self.api.record_decision(self.request, now=self.now + timedelta(seconds=seconds))
        first = self.prepare()
        for source, message in ((self.request["source"], "later observation"),
                                ({**self.request["source"], "captured_at": (self.now + timedelta(seconds=1)).isoformat()}, "unchanged source")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.resolve(self.outcome(first["decision_id"], source=source))

    def test_consuming_absent_item_rejected_and_double_consumption_rollback(self):
        first = self.prepare()
        event = {"kind": "potion", "action": "consumed", "item": "Fire Potion", "evidence_note": "Observed."}
        with self.assertRaisesRegex(ValueError, "absent"):
            self.resolve(self.outcome(first["decision_id"], inventory_events=[event, event]))
        self.assertEqual(0, self.table_count("inventory_events"))

    def test_partial_inventory_does_not_erase_and_unknown_category_cannot_assert(self):
        first = self.prepare()
        baseline = {"items": [{"kind": "potion", "item": "Blood Potion"}],
            "coverage": {"card": "unknown", "relic": "unknown", "potion": "partial"}, "evidence": {"potion": "One visible slot only."}}
        self.resolve(self.outcome(first["decision_id"], inventory_baseline=baseline))
        self.assertCountEqual(["Fire Potion", "Blood Potion"], self.db.inventory_ledger(run_id=self.context["run_id"])["current"]["potion"])

    def test_zone_order_preserved_without_invented_timestamps(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], zone_events=[
            {"kind": "play", "card_name": "Strike", "from_zone": "hand", "to_zone": "discard", "evidence_note": "Observed play."},
            {"kind": "return", "card_name": "Strike", "from_zone": "discard", "to_zone": "hand", "evidence_note": "Observed return."}])
        self.resolve(req)
        zones = self.db.combat_zone_state(combat_id=self.context["combat_id"])
        self.assertTrue(zones["known"])
        self.assertEqual(["Strike"], zones["zones"]["hand"])
        with self.db._connection() as con:
            times = [r[0] for r in con.execute("SELECT observed_at FROM combat_zone_events")]
        self.assertEqual([req["source"]["captured_at"]] * 2, times)

    def test_omitted_zone_coverage_is_unknown_not_assumed_complete(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"])
        del req["zone_coverage"]
        self.resolve(req)
        self.assertFalse(self.db.combat_zone_state(combat_id=self.context["combat_id"])["known"])

    def test_end_turn_transition_is_explicit_and_atomic(self):
        first = self.prepare()
        transitions = [{"kind": "end_turn", "closing_state": {"energy": 0}, "evidence_note": "Previous turn visibly ended."},
            {"kind": "start_turn", "turn_number": 2, "phase": "player", "opening_state": {"energy": 3}, "evidence_note": "Turn counter and new hand observed."}]
        req = self.outcome(first["decision_id"], transitions=transitions)
        receipt = self.resolve(req)
        self.assertNotEqual(self.context["turn_id"], receipt["next_context"]["turn_id"])
        with self.db._connection() as con:
            turns = con.execute("SELECT turn_number,opened_at,closed_at FROM combat_turns ORDER BY turn_number").fetchall()
        self.assertEqual(2, len(turns))
        self.assertEqual(req["source"]["captured_at"], turns[0][2])
        self.assertEqual(req["source"]["captured_at"], turns[1][1])

    def test_floor_transition_requires_combat_closure_and_no_invented_intermediate_floors(self):
        first = self.prepare()
        transitions = [{"kind": "end_turn", "closing_state": {}, "evidence_note": "Turn ended."},
            {"kind": "end_combat", "outcome": "victory", "closing_state": {"hp": 40}, "evidence_note": "Reward screen confirms victory."},
            {"kind": "advance_floor", "act": 2, "floor": 33, "node_type": "boss", "previous_outcome": "completed",
             "previous_ending_state": {"hp": 40}, "starting_state": {"hp": 55}, "evidence_note": "Later floor explicitly observed; intervening floor unlogged."}]
        req = self.outcome(first["decision_id"], transitions=transitions)
        receipt = self.resolve(req)
        self.assertIsNone(receipt["next_context"]["combat_id"])
        self.assertEqual(2, self.table_count("floors"))
        self.assertNotEqual(self.context["floor_id"], receipt["next_context"]["floor_id"])

    def test_bad_transition_rolls_back_previous_turn_close(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], transitions=[
            {"kind": "end_turn", "closing_state": {}, "evidence_note": "Ended."},
            {"kind": "start_turn", "turn_number": 99, "phase": "player", "opening_state": {}, "evidence_note": "Wrong."}])
        with self.assertRaisesRegex(ValueError, "next explicit number"):
            self.resolve(req)
        with self.db._connection() as con:
            self.assertIsNone(con.execute("SELECT closed_at FROM combat_turns WHERE id=?", (self.context["turn_id"],)).fetchone()[0])

    def test_resume_is_explicit_source_bound_and_idempotent(self):
        self.db.record_session_checkpoint(run_id=self.context["run_id"], floor_id=self.context["floor_id"], combat_id=self.context["combat_id"],
            kind="pause", boundary="combat", state={}, source="fixture", observed_at=self.now.isoformat())
        with self.assertRaisesRegex(ValueError, "paused"):
            self.prepare()
        req = {"schema": SCHEMA, "operation_id": str(uuid4()), "context": self.context, "source": self.source("resume", 1),
            "state": {}, "boundary": "combat", "evidence_note": "User has explicitly resumed this run; fresh view confirmed."}
        result = self.api.record_resume(req, now=self.now + timedelta(seconds=1))
        self.assertEqual("resumed", result["status"])
        self.assertTrue(self.api.record_resume(req, now=self.now + timedelta(days=1))["idempotent_replay"])
        self.assertEqual(2, self.table_count("session_checkpoints"))

    def test_concurrent_outcome_retry_commits_once(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], inventory_events=[{"kind": "potion", "action": "consumed", "item": "Fire Potion", "evidence_note": "Observed."}])
        other = TelemetryDatabase(self.db.path)
        other.initialize()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(api.record_outcome, req, now=self.now + timedelta(seconds=2)) for api in (self.api, PlayTelemetry(other))]
            results = [f.result(timeout=5) for f in futures]
        self.assertEqual([False, True], sorted(r["idempotent_replay"] for r in results))
        self.assertEqual(1, self.table_count("inventory_events"))

    def test_request_bounds_and_nonfinite_reject_before_writes(self):
        for state in ({"hp": float("nan")}, {"text": "x" * 262145}):
            with self.subTest(state=str(state)[:40]), self.assertRaises(ValueError):
                self.prepare({**self.request, "state": state})
        self.assertEqual(0, self.table_count("decisions"))

    def test_new_combat_and_confirmed_opening_zones_share_atomic_outcome(self):
        self.db.complete_combat_turn(turn_id=self.context["turn_id"], closing_state={})
        self.db.complete_combat(combat_id=self.context["combat_id"], outcome="victory", closing_state={})
        self.context.update(combat_id=None, turn_id=None)
        self.request.update(phase="map", action={"kind": "navigation"})
        prepared = self.prepare()
        req = self.outcome(prepared["decision_id"], transitions=[
            {"kind": "advance_floor", "act": 2, "floor": 32, "node_type": "combat", "previous_outcome": "completed",
             "previous_ending_state": {}, "starting_state": {}, "evidence_note": "Floor number observed."},
            {"kind": "start_combat", "opening_state": {}, "encounter_name": "Cultist", "encounter_type": "normal", "evidence_note": "Encounter inspected."},
            {"kind": "start_turn", "turn_number": 1, "phase": "player", "opening_state": {}, "evidence_note": "Opening hand and turn inspected."}],
            zone_baseline={"deck": ["Strike", "Defend"], "hand": ["Defend"], "complete": True, "opening": True, "evidence_note": "Entire deck and opening hand inspected."})
        result = self.resolve(req)
        self.assertTrue(result["zone_state_known"])
        new_context = result["next_context"]
        self.assertEqual(["Defend"], self.db.combat_zone_state(combat_id=new_context["combat_id"])["zones"]["hand"])
        following = {**self.request, "operation_id": str(uuid4()), "context": new_context, "phase": "combat",
                     "source": self.source("following", 2)}
        self.assertEqual("pending", self.api.record_decision(following, now=self.now + timedelta(seconds=2))["status"])

    def test_complete_unknown_inventory_baseline_cannot_assert_items(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], inventory_baseline={"items": [{"kind": "potion", "item": "Fire Potion"}],
            "coverage": {"card": "unknown", "relic": "unknown", "potion": "unknown"}, "evidence": {}})
        with self.assertRaisesRegex(ValueError, "unknown inventory"):
            self.resolve(req)
        self.assertEqual(1, self.table_count("inventory_baselines"))

    def test_complete_empty_inventory_is_explicit_and_does_not_fill_other_categories(self):
        first = self.prepare()
        req = self.outcome(first["decision_id"], inventory_baseline={"items": [],
            "coverage": {"card": "unknown", "relic": "unknown", "potion": "complete"}, "evidence": {"potion": "All slots inspected empty."}})
        self.resolve(req)
        inventory = self.db.inventory_ledger(run_id=self.context["run_id"])
        self.assertEqual([], inventory["current"]["potion"])
        self.assertEqual("unknown", inventory["coverage"]["card"])

    def test_malformed_event_fields_fail_before_commit(self):
        first = self.prepare()
        for key, bad in (("inventory_events", {"kind": [], "action": "consumed", "item": "Fire Potion", "evidence_note": "Observed."}),
                         ("zone_events", {"kind": [], "card_name": "Strike", "from_zone": "hand", "to_zone": "discard", "evidence_note": "Observed."}),
                         ("transitions", {"kind": [], "evidence_note": "Observed."})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.resolve(self.outcome(first["decision_id"], **{key: [bad]}))
        self.assertEqual("recommended", self.status(first["decision_id"]))
        self.assertEqual(0, self.table_count("inventory_events"))

    def test_observed_context_fields_cannot_conflict_even_if_snapshot_is_partial(self):
        for state in ({"floor": 32}, {"ascension": 1}, {"observed_at": (self.now - timedelta(seconds=1)).isoformat()}):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "conflicts|disagrees"):
                self.prepare({**self.request, "state": state})
        self.assertEqual(0, self.table_count("decisions"))

    def test_intervening_pause_can_be_reconciled_without_implicitly_resuming(self):
        first = self.prepare()
        self.db.record_session_checkpoint(run_id=self.context["run_id"], floor_id=self.context["floor_id"], combat_id=self.context["combat_id"],
            kind="pause", boundary="combat", state={}, source="fixture", observed_at=(self.now + timedelta(seconds=1)).isoformat())
        req = self.outcome(first["decision_id"], source=self.source("post_pause", 2))
        result = self.resolve(req)
        self.assertTrue(result["intervening_pause_reconciled"])
        self.assertEqual("resolved", self.status(first["decision_id"]))
        following = {**self.request, "operation_id": str(uuid4()), "source": self.source("following", 3)}
        with self.assertRaisesRegex(ValueError, "paused"):
            self.api.record_decision(following, now=self.now + timedelta(seconds=3))
        resume = {"schema": SCHEMA, "operation_id": str(uuid4()), "context": self.context, "source": following["source"],
            "state": {}, "boundary": "combat", "evidence_note": "Explicit user resume and fresh state."}
        self.assertEqual("resumed", self.api.record_resume(resume, now=self.now + timedelta(seconds=3))["status"])

    def test_pause_exception_does_not_hide_an_inventory_change(self):
        first = self.prepare()
        self.db.record_session_checkpoint(run_id=self.context["run_id"], floor_id=self.context["floor_id"], combat_id=self.context["combat_id"],
            kind="pause", boundary="combat", state={}, source="fixture", observed_at=(self.now + timedelta(seconds=1)).isoformat())
        self.db.record_inventory_event(run_id=self.context["run_id"], item_kind="potion", action="acquired", item_name="Blood Potion", source="external")
        with self.assertRaisesRegex(ValueError, "ledger/context changed"):
            self.resolve(self.outcome(first["decision_id"], source=self.source("post_pause", 2)))
        self.assertEqual("recommended", self.status(first["decision_id"]))

    def test_transition_state_context_conflict_rolls_back_entire_outcome(self):
        first = self.prepare()
        bad_transitions = [
            [{"kind": "end_turn", "closing_state": {"floor": 999}, "evidence_note": "Conflicting declaration."}],
            [{"kind": "end_turn", "closing_state": {}, "evidence_note": "Ended."},
             {"kind": "start_turn", "turn_number": 2, "phase": "player", "opening_state": {"ascension": 1}, "evidence_note": "Conflicting declaration."}],
            [{"kind": "end_turn", "closing_state": {}, "evidence_note": "Ended."},
             {"kind": "end_combat", "outcome": "victory", "closing_state": {}, "evidence_note": "Won."},
             {"kind": "advance_floor", "act": 2, "floor": 32, "node_type": "rest", "previous_outcome": "completed",
              "previous_ending_state": {}, "starting_state": {"floor": 31}, "evidence_note": "Conflicting declaration."}]]
        for transitions in bad_transitions:
            with self.subTest(transitions=transitions), self.assertRaisesRegex(ValueError, "conflicts"):
                self.resolve(self.outcome(first["decision_id"], transitions=transitions))
            self.assertEqual("recommended", self.status(first["decision_id"]))
            self.assertEqual(1, self.table_count("floors"))
            self.assertEqual(1, self.table_count("combat_turns"))
            with self.db._connection() as con:
                self.assertIsNone(con.execute("SELECT closed_at FROM combat_turns WHERE id=?", (self.context["turn_id"],)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
