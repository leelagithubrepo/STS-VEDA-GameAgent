import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.veda_checkpoint import main
from veda.advisory_checkpoint import record_advisory_checkpoint
from veda.telemetry_database import TelemetryDatabase


class AdvisoryCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = TelemetryDatabase(self.root / "test.sqlite3")
        self.run = self.db.start_or_resume_run(ascension=2)
        self.floor = self.db.record_floor(run_id=self.run, act=2, floor=32, node_type="rest", outcome="completed")
        self.image = self.root / "saved.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nsource fixture bytes; pixel recognition is outside checkpoint scope")
        self.now = datetime.now(timezone.utc)
        self.request = {"schema": "veda.advisory-checkpoint.v1", "operation_id": str(uuid4()),
            "run_id": self.run, "floor_id": self.floor, "boundary": "map", "paused_at": self.now.isoformat(),
            "source": {"path": str(self.image), "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                "captured_at": self.now.isoformat(), "origin": "reviewer", "evidence_note": "Fixture observations only; no visual accuracy claim."},
            "state": {"hp": 35, "max_hp": 80, "floor": 32, "act": 2, "ascension": 2, "gold": 50, "deck_size": 20},
            "inventory": {"items": [{"kind": "potion", "item": "Explosive Potion"}],
                "coverage": {"card": "unknown", "relic": "unknown", "potion": "complete"},
                "evidence": {"potion": "All five slots inspected: one named potion, four empty."}},
            "expectations": [{"claim": "Collector is next", "basis": "Previous route observation; verify on resume."}],
            "resume_notes": ["Inspect the next decision before any input."]}

    def save(self, request=None, now=None):
        return record_advisory_checkpoint(self.db, request or self.request, now=now or self.now)

    def counts(self):
        with self.db._connection() as con:
            return tuple(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("session_checkpoints", "inventory_baselines", "inventory_baseline_items"))

    def test_atomic_pause_preserves_exact_source_times_expectations_and_coverage(self):
        result = self.save(now=self.now + timedelta(seconds=10))
        self.assertEqual((1, 1, 1), self.counts())
        self.assertFalse(result["controller_authorized"])
        self.assertFalse(result["runtime_authorized"])
        self.assertTrue(result["requires_new_observation_on_resume"])
        self.assertEqual(self.request["state"], result["observed_state"])
        with self.db._connection() as con:
            checkpoint = dict(con.execute("SELECT * FROM session_checkpoints").fetchone())
            baseline = dict(con.execute("SELECT * FROM inventory_baselines").fetchone())
        self.assertEqual(self.now.isoformat(), checkpoint["observed_at"])
        self.assertEqual(self.now.isoformat(), baseline["observed_at"])
        payload = json.loads(checkpoint["payload_json"])
        self.assertEqual(self.request["source"], payload["source"])
        self.assertEqual((self.now + timedelta(seconds=10)).isoformat(), payload["recorded_at"])
        self.assertNotIn("expectations", json.loads(checkpoint["state_json"]))
        self.assertEqual(self.request["expectations"], payload["unconfirmed_expectations"])
        self.assertEqual("unknown", result["inventory_coverage"]["card"])

    def test_same_operation_retries_historical_receipt_without_freshness_promotion(self):
        first = self.save()
        self.image.unlink()
        second = self.save(now=self.now + timedelta(days=2))
        self.assertEqual(first["checkpoint_id"], second["checkpoint_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertTrue(second["requires_new_observation_on_resume"])
        self.assertEqual((1, 1, 1), self.counts())

    def test_concurrent_identical_requests_have_one_committed_receipt(self):
        second_db = TelemetryDatabase(self.db.path)
        second_db.initialize()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(record_advisory_checkpoint, db, self.request, now=self.now)
                       for db in (self.db, second_db)]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(results[0]["checkpoint_id"], results[1]["checkpoint_id"])
        self.assertEqual([False, True], sorted(r["idempotent_replay"] for r in results))
        self.assertEqual((1, 1, 1), self.counts())

    def test_reusing_operation_with_changed_content_is_rejected(self):
        self.save()
        other = copy.deepcopy(self.request)
        other["state"]["hp"] = 36
        with self.assertRaisesRegex(ValueError, "different content"):
            self.save(other)
        self.assertEqual((1, 1, 1), self.counts())

    def test_failure_after_inventory_write_rolls_everything_back(self):
        with patch.object(self.db, "record_session_checkpoint", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.save()
        self.assertEqual((0, 0, 0), self.counts())

    def test_source_changed_after_writes_rolls_everything_back(self):
        original = self.db.record_session_checkpoint
        def change_source(**kwargs):
            ident = original(**kwargs)
            self.image.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
            return ident
        with patch.object(self.db, "record_session_checkpoint", side_effect=change_source):
            with self.assertRaisesRegex(ValueError, "hash changed"):
                self.save()
        self.assertEqual((0, 0, 0), self.counts())

    def test_stale_and_future_evidence_cannot_become_current_inventory(self):
        for delta in (181, -1):
            with self.subTest(delta=delta), self.assertRaisesRegex(ValueError, "stale|future"):
                self.save(now=self.now + timedelta(seconds=delta))
        self.assertEqual((0, 0, 0), self.counts())

    def test_cross_run_wrong_floor_and_closed_run_fail(self):
        other_run = self.db.start_or_resume_run(character_name="Silent")
        wrong = copy.deepcopy(self.request)
        wrong["run_id"] = other_run
        later = datetime.now(timezone.utc)
        wrong["source"]["captured_at"] = wrong["paused_at"] = later.isoformat()
        with self.assertRaisesRegex(ValueError, "latest recorded floor"):
            self.save(wrong, now=later)
        self.db.record_floor(run_id=self.run, act=2, floor=33, node_type="boss", outcome=None)
        with self.assertRaisesRegex(ValueError, "latest recorded floor"):
            self.save()
        with self.db._connection() as con:
            con.execute("UPDATE runs SET status='completed' WHERE id=?", (self.run,))
        with self.assertRaisesRegex(ValueError, "active run"):
            self.save()
        self.assertEqual((0, 0, 0), self.counts())

    def test_partial_inventory_does_not_erase_previously_known_items(self):
        self.db.record_inventory_baseline(run_id=self.run, floor_id=self.floor,
            items=[{"kind": "potion", "item": "Blood Potion"}],
            coverage={"card": "unknown", "relic": "unknown", "potion": "complete"}, source="prior fixture")
        later = datetime.now(timezone.utc)
        self.request["source"]["captured_at"] = self.request["paused_at"] = later.isoformat()
        self.request["inventory"]["coverage"]["potion"] = "partial"
        result = self.save(now=later)
        self.assertEqual("partial", result["inventory_coverage"]["potion"])
        self.assertEqual(["Blood Potion", "Explosive Potion"], self.db.inventory_ledger(run_id=self.run)["current"]["potion"])

    def test_newer_inventory_rejects_older_baseline(self):
        self.db.record_inventory_event(run_id=self.run, floor_id=self.floor, item_kind="potion", action="acquired", item_name="Blood Potion", source="newer fixture")
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            self.save()
        self.assertEqual((0, 0, 0), self.counts())

    def test_complete_empty_potions_explicitly_clears_but_unknown_cannot_assert_items(self):
        wrong = copy.deepcopy(self.request)
        wrong["inventory"]["coverage"]["potion"] = "unknown"
        wrong["inventory"]["evidence"] = {}
        with self.assertRaisesRegex(ValueError, "unknown category"):
            self.save(wrong)
        self.request["inventory"]["items"] = []
        self.request["inventory"]["evidence"]["potion"] = "All slots visibly empty."
        self.save()
        self.assertEqual([], self.db.inventory_ledger(run_id=self.run)["current"]["potion"])

    def test_inventory_coverage_needs_proof_and_count_consistency(self):
        self.request["inventory"]["evidence"] = {}
        with self.assertRaisesRegex(ValueError, "evidence note"):
            self.save()
        self.request["inventory"]["evidence"] = {"potion": "Visible", "card": "Visible"}
        self.request["inventory"]["coverage"]["card"] = "complete"
        with self.assertRaisesRegex(ValueError, "deck size"):
            self.save()

    def test_context_numbers_and_unknowns(self):
        self.request["state"]["floor"] = 31
        with self.assertRaisesRegex(ValueError, "floor conflicts"):
            self.save()
        self.request["state"] = {"hp": None, "floor": 32}
        self.request.pop("inventory")
        result = self.save()
        self.assertIsNone(result["observed_state"]["hp"])
        self.assertNotIn("energy", result["observed_state"])
        self.assertIsNone(result["inventory_baseline_id"])

    def test_explicit_null_inventory_and_malformed_nested_values(self):
        for path in (("boundary",), ("source", "origin"), ("inventory", "coverage", "potion")):
            wrong = copy.deepcopy(self.request)
            target = wrong
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = []
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.save(wrong)
        self.request["inventory"] = None
        self.assertIsNone(self.save()["inventory_baseline_id"])

    def test_open_combat_and_latest_turn_are_required(self):
        combat = self.db.start_combat(run_id=self.run, floor_id=self.floor, opening_state={})
        turn = self.db.start_combat_turn(combat_id=combat, turn_number=1, phase="combat", opening_state={})
        with self.assertRaisesRegex(ValueError, "open combat"):
            self.save()
        self.request.update(boundary="combat", combat_id=combat, turn_id=turn)
        self.db.start_combat_turn(combat_id=combat, turn_number=2, phase="combat", opening_state={})
        with self.assertRaisesRegex(ValueError, "latest open turn"):
            self.save()
        self.assertEqual((0, 0, 0), self.counts())

    def test_combat_pause_preserves_turn_binding_without_closing_it(self):
        combat = self.db.start_combat(run_id=self.run, floor_id=self.floor, opening_state={})
        turn = self.db.start_combat_turn(combat_id=combat, turn_number=1, phase="combat", opening_state={})
        self.request.update(boundary="combat", combat_id=combat, turn_id=turn)
        with self.assertRaisesRegex(ValueError, "predates this combat turn"):
            self.save()
        later = datetime.now(timezone.utc)
        self.request["source"]["captured_at"] = self.request["paused_at"] = later.isoformat()
        result = self.save(now=later)
        with self.db._connection() as con:
            self.assertIsNone(con.execute("SELECT closed_at FROM combat_turns WHERE id=?", (turn,)).fetchone()[0])
            payload = json.loads(con.execute("SELECT payload_json FROM session_checkpoints WHERE id=?", (result["checkpoint_id"],)).fetchone()[0])
        self.assertEqual(turn, payload["turn_id"])

    def test_filename_time_and_hash_must_match(self):
        timestamped = self.root / "ps5_observation_20260927T000000Z_test.png"
        timestamped.write_bytes(self.image.read_bytes())
        self.request["source"]["path"] = str(timestamped)
        with self.assertRaisesRegex(ValueError, "filename"):
            self.save()
        self.request["source"]["path"] = str(self.image)
        self.request["source"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash changed"):
            self.save()

    def test_second_pause_new_operation_is_rejected_without_inventory_leak(self):
        self.save()
        self.request["operation_id"] = str(uuid4())
        self.request["paused_at"] = (self.now + timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "already paused"):
            self.save(now=self.now + timedelta(seconds=2))
        self.assertEqual((1, 1, 1), self.counts())

    def test_source_before_recent_resume_is_rejected(self):
        self.db.record_session_checkpoint(run_id=self.run, floor_id=self.floor, kind="pause", boundary="map", state={}, observed_at=self.now.isoformat())
        resumed = self.now + timedelta(seconds=1)
        self.db.record_session_checkpoint(run_id=self.run, floor_id=self.floor, kind="resume", boundary="map", state={}, observed_at=resumed.isoformat())
        self.request["paused_at"] = (self.now + timedelta(seconds=2)).isoformat()
        with self.assertRaisesRegex(ValueError, "previous checkpoint"):
            self.save(now=self.now + timedelta(seconds=3))
        self.assertEqual((2, 0, 0), self.counts())

    def test_cli_compact_receipt_and_malformed_json(self):
        path = self.root / "request.json"
        path.write_text(json.dumps(self.request))
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(0, main(["--database", str(self.db.path), "--request", str(path)]))
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["saved"])
        self.assertNotIn("history", result)
        for raw in ('[]', '{"schema":1,"schema":2}', '{"number":NaN}'):
            path.write_text(raw)
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(2, main(["--database", str(self.db.path), "--request", str(path)]))
            self.assertFalse(json.loads(stdout.getvalue())["saved"])

    def test_cli_explicit_null_inventory_returns_compact_receipt(self):
        self.request["inventory"] = None
        path = self.root / "request.json"
        path.write_text(json.dumps(self.request))
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(0, main(["--database", str(self.db.path), "--request", str(path)]))
        self.assertIsNone(json.loads(stdout.getvalue())["inventory_baseline_id"])
        self.assertEqual((1, 0, 0), self.counts())


if __name__ == "__main__":
    unittest.main()
