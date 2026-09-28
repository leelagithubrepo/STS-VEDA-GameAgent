"""Learning-memory retrieval against synthetic temporary SQLite evidence only."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.veda_play_lessons import main
from veda.play_lessons import MAX_OUTPUT_BYTES, read_play_lessons
from veda.telemetry_database import TelemetryDatabase


def add_verified_case(db, context, *, number=1, action=None, prediction=None, learning=None,
                      before=None, after=None, screen="combat"):
    """Synthetic fixture helper; caller must supply a temporary database."""
    start = datetime(2026, 9, 20, tzinfo=timezone.utc) + timedelta(minutes=number)
    before = before or {"hp": 30, "energy": 3, "block": 0,
                        "hand": [{"id": "strike-copy", "name": "Strike"}],
                        "enemies": [{"id": "cultist", "name": "Cultist", "hp": 40, "block": 0}]}
    after = after or {"hp": 30, "energy": 2, "block": 0,
                      "enemies": [{"id": "cultist", "name": "Cultist", "hp": 34, "block": 0}]}
    action = action or {"kind": "card", "requested_action": {"kind": "card", "card_id": "strike-copy", "target": "cultist"}}
    prediction = prediction if prediction is not None else {"forecast": {"player_hp": 30}, "assessment": {"warnings": []}}
    decision_id = db.record_decision(**context, phase=screen, state=before, options=[action], recommendation=action,
                                    reasoning="Synthetic reason.", prediction=prediction,
                                    screenshot_path="/synthetic/before.png", source="synthetic")
    source = {"path": "/synthetic/after.png", "sha256": "a" * 64,
              "captured_at": (start + timedelta(seconds=1)).isoformat(), "origin": "reviewer"}
    request = {"context": context, "decision_id": decision_id, "status": "verified", "source": source, "state": after}
    outcome_id = db.record_event(**context, kind="play_outcome", phase="observed_outcome", state=after,
                                 screenshot_path=source["path"], source="play_telemetry:reviewer")
    receipt = {"context": context, "decision_id": decision_id, "event_id": outcome_id, "status": "verified",
               "unresolved": False, "source": source, "kind": "outcome"}
    actual = {"state": after, "source": source, "event_id": outcome_id, "status": "verified",
              "evidence_note": "Synthetic observed result."}
    if learning is not None:
        actual["learning"] = learning
    db.resolve_decision(decision_id=decision_id, chosen_action=action, actual_outcome=actual)
    with db._connection() as con:
        con.execute("UPDATE evidence_events SET payload_json=?,observed_at=? WHERE id=?",
                    (json.dumps({"request": request, "receipt": receipt}), source["captured_at"], outcome_id))
        con.execute("UPDATE evidence_events SET observed_at=? WHERE id=(SELECT event_id FROM decisions WHERE id=?)",
                    (start.isoformat(), decision_id))
    return decision_id, outcome_id


class PlayLessonsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "lessons.sqlite3"
        self.db = TelemetryDatabase(self.path)
        self.context = self.new_context()

    def new_context(self, enemy="Cultist", ascension=2):
        run = self.db.start_new_run(ascension=ascension)
        floor = self.db.record_floor(run_id=run, act=1, floor=1, node_type="combat", outcome=None)
        combat = self.db.start_combat(run_id=run, floor_id=floor, encounter_name=enemy,
                                     encounter_type="normal", opening_state={})
        turn = self.db.start_combat_turn(combat_id=combat, turn_number=1, phase="player", opening_state={})
        return {"run_id": run, "floor_id": floor, "combat_id": combat, "turn_id": turn}

    def add(self, **kwargs):
        return add_verified_case(self.db, self.context, **kwargs)

    def read(self, **kwargs):
        return read_play_lessons(self.path, **kwargs)

    def mutate_actual(self, decision, mutate):
        with self.db._connection() as con:
            value = json.loads(con.execute("SELECT actual_outcome_json FROM decisions WHERE id=?", (decision,)).fetchone()[0])
            mutate(value)
            con.execute("UPDATE decisions SET actual_outcome_json=? WHERE id=?", (json.dumps(value), decision))

    def test_verified_retrieval_is_read_only_and_carries_exact_evidence_not_authority(self):
        decision, outcome = self.add()
        before = self.path.read_bytes()
        with patch.object(TelemetryDatabase, "initialize", side_effect=AssertionError("reader must not migrate")):
            result = self.read(enemy="cultist", card="STRIKE")
        self.assertEqual(before, self.path.read_bytes())
        case = result["cases"][0]
        self.assertEqual((decision, outcome), (case["case_id"], case["outcome_event_id"]))
        self.assertEqual("Strike", case["action"]["card_name"])
        self.assertEqual(2, case["context"]["ascension"])
        self.assertEqual("a" * 64, case["evidence"]["outcome_source"]["sha256"])
        self.assertTrue(result["historical_only"])
        for key in ("live", "runtime_authorized", "controller_authorized", "model_weights_updated"):
            self.assertIs(result[key], False)
        self.assertIn("does not prove", result["limitations"][0])

    def test_unknown_pending_skipped_and_unlinked_results_do_not_become_cases(self):
        good, _ = self.add(number=1)
        for number, status in ((2, "recommended"), (3, "skipped")):
            decision, _ = self.add(number=number)
            with self.db._connection() as con:
                con.execute("UPDATE decisions SET status=? WHERE id=?", (status, decision))
        unknown, _ = self.add(number=4)
        self.mutate_actual(unknown, lambda actual: actual.update(status="unknown"))
        legacy, _ = self.add(number=5)
        self.mutate_actual(legacy, lambda actual: actual.pop("event_id"))
        result = self.read()
        self.assertEqual([good], [case["case_id"] for case in result["cases"]])
        self.assertEqual(2, result["search"]["excluded"]["unconfirmed_or_malformed"])

    def test_conflicting_link_source_state_context_and_receipt_are_excluded(self):
        for number, field in enumerate(("state", "source", "context", "receipt", "time"), start=1):
            with self.subTest(field=field):
                decision, event = self.add(number=number)
                with self.db._connection() as con:
                    row = con.execute("SELECT payload_json FROM evidence_events WHERE id=?", (event,)).fetchone()
                    payload = json.loads(row[0])
                    if field == "state":
                        payload["request"]["state"]["hp"] = 99
                    elif field == "source":
                        payload["request"]["source"]["sha256"] = "b" * 64
                    elif field == "context":
                        payload["receipt"]["context"]["run_id"] = str(uuid4())
                    elif field == "receipt":
                        payload["receipt"]["event_id"] = str(uuid4())
                    else:
                        con.execute("UPDATE evidence_events SET observed_at='2020-01-01T00:00:00+00:00' WHERE id=?", (event,))
                    con.execute("UPDATE evidence_events SET payload_json=? WHERE id=?", (json.dumps(payload), event))
        self.assertEqual([], self.read()["cases"])

    def test_relevant_mismatches_rank_first_and_filters_are_conjunctive(self):
        mismatch, _ = self.add(number=1, learning={"decision_policy": "learning", "observed_mismatches": [
            {"field": "energy", "expected": 1, "observed": 2}], "assessment": {"warnings": ["Unmodeled relic"]}})
        recent, _ = self.add(number=2, learning={"observed_mismatches": []})
        self.add(number=3, action={"kind": "card", "name": "Defend"}, learning={"observed_mismatches": [
            {"field": "block", "expected": 5, "observed": 7}]})
        result = self.read(screen="combat", enemy="Cultist", card="Strike", action="card")
        self.assertEqual([mismatch, recent], [case["case_id"] for case in result["cases"]])
        self.assertEqual("mismatch", result["cases"][0]["comparison"]["status"])
        self.assertEqual(["Unmodeled relic"], result["cases"][0]["uncertainties"])
        self.assertEqual([], self.read(enemy="Cult")["cases"], "complete names, not accidental substring matches")

    def test_navigation_is_not_a_completed_strategic_action(self):
        card, _ = self.add(number=1)
        focus, _ = self.add(number=2, action={"kind": "navigation", "requested_action": {
            "kind": "card", "card_id": "strike-copy"}, "expected": {"kind": "card_focus"}})
        self.assertEqual([card], [case["case_id"] for case in self.read()["cases"]])
        self.assertEqual([focus], [case["case_id"] for case in self.read(action="navigation")["cases"]])

    def test_cross_run_search_and_exact_run_filter_keep_ascension_context(self):
        first, _ = self.add(number=1)
        other_context = self.new_context(ascension=20)
        second, _ = add_verified_case(self.db, other_context, number=2)
        self.assertEqual([second, first], [case["case_id"] for case in self.read(enemy="Cultist")["cases"]])
        exact = self.read(run_id=self.context["run_id"])
        self.assertEqual([first], [case["case_id"] for case in exact["cases"]])
        self.assertEqual(20, self.read()["cases"][0]["context"]["ascension"])
        self.assertEqual([], self.read(run_id="' OR 1=1 --")["cases"])

    def test_legacy_notes_remain_notes_without_inventing_mismatch(self):
        decision, _ = self.add(prediction={})
        self.mutate_actual(decision, lambda actual: actual.update(evidence_note='Mismatch: expected 5 observed 9.'))
        case = self.read()["cases"][0]
        self.assertEqual("unavailable", case["comparison"]["status"])
        self.assertEqual([], case["comparison"]["mismatches"])
        self.assertIn("expected 5", case["evidence"]["note"])
        self.assertGreaterEqual(len(case["uncertainties"]), 2)

    def test_durable_evidence_fallback_supports_precanonical_records(self):
        _, event = self.add()
        with self.db._connection() as con:
            payload = json.loads(con.execute("SELECT payload_json FROM evidence_events WHERE id=?", (event,)).fetchone()[0])
            payload["durable_verified_evidence"] = {"observed_mismatches": [{"field": "hp", "expected": 30, "observed": 28}]}
            con.execute("UPDATE evidence_events SET payload_json=? WHERE id=?", (json.dumps(payload), event))
        self.assertEqual("mismatch", self.read()["cases"][0]["comparison"]["status"])

    def test_candidate_and_output_bounds_are_reported(self):
        for number in range(1, 5):
            self.add(number=number)
        with patch("veda.play_lessons.MAX_CANDIDATES", 3):
            result = self.read(limit=2)
        self.assertEqual(3, result["search"]["candidates_examined"])
        self.assertTrue(result["search"]["candidate_window_truncated"])
        self.assertTrue(result["search"]["output_truncated"])
        self.assertEqual(2, len(result["cases"]))
        self.assertLessEqual(len(json.dumps(result).encode()), MAX_OUTPUT_BYTES)
        with patch("veda.play_lessons.MAX_OUTPUT_BYTES", 2000):
            compact = self.read(limit=4)
            self.assertTrue(compact["search"]["output_truncated"])
            self.assertLessEqual(len(json.dumps(compact).encode()), 2000)

    def test_malformed_and_nonfinite_json_are_excluded_without_crashing(self):
        for number, raw in enumerate(('[1]', '{bad', '{"hp":1e999}', '{"hp":2,"hp":3}'), start=1):
            decision, _ = self.add(number=number)
            with self.db._connection() as con:
                con.execute("UPDATE decisions SET prediction_json=? WHERE id=?", (raw, decision))
        self.assertEqual([], self.read()["cases"])

    def test_real_telemetry_receipt_is_retrievable(self):
        from tests.test_play_telemetry import PlayTelemetryTests
        fixture = PlayTelemetryTests(methodName="runTest")
        fixture.setUp()
        try:
            decision = fixture.prepare()["decision_id"]
            fixture.resolve(fixture.outcome(decision))
            result = read_play_lessons(fixture.db.path, enemy="Cultist", action="potion")
            self.assertEqual([decision], [case["case_id"] for case in result["cases"]])
        finally:
            fixture.doCleanups()

    def test_cli_and_missing_database_never_create_or_authorize(self):
        self.add()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main(["--database", str(self.path), "--enemy", "Cultist", "--limit", "1"]))
        self.assertEqual(1, len(json.loads(output.getvalue())["cases"]))
        missing = Path(self.temp.name) / "missing.sqlite3"
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(2, main(["--database", str(missing)]))
        self.assertFalse(missing.exists())
        self.assertFalse(json.loads(output.getvalue())["controller_authorized"])
        for limit in (0, 11, True):
            with self.assertRaises(ValueError):
                self.read(limit=limit)


if __name__ == "__main__":
    unittest.main()
