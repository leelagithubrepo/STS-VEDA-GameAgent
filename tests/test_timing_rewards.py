from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from veda.telemetry_database import TelemetryDatabase


class TimingAndRewardBatchTests(unittest.TestCase):
    def test_run_timing_fails_closed_for_unfinished_floor(self):
        with TemporaryDirectory() as directory:
            db = TelemetryDatabase(Path(directory) / "veda.sqlite3")
            run_id = db.start_or_resume_run(ascension=2)
            floor_id = db.record_floor(run_id=run_id, act=2, floor=21, node_type="enemy", outcome=None, starting_state={})
            report = db.run_timing_report(run_id=run_id, target_seconds=1500)
        self.assertEqual([floor_id], report["missing_timing_floor_ids"])
        self.assertEqual([], report["over_target_floor_ids"])
        self.assertIsNone(report["mean_seconds"])

    def test_reward_batch_uses_one_evidence_path_for_all_items(self):
        with TemporaryDirectory() as directory:
            db = TelemetryDatabase(Path(directory) / "veda.sqlite3")
            run_id = db.start_or_resume_run(ascension=2)
            floor_id = db.record_floor(run_id=run_id, act=2, floor=21, node_type="enemy", outcome=None, starting_state={})
            ids = db.record_inventory_events_batch(
                run_id=run_id, floor_id=floor_id, screenshot_path="reward.png",
                source="visible reward batch", confidence=0.99,
                events=[
                    {"kind": "relic", "action": "acquired", "item": "Red Mask", "property": "Applies 1 Weak"},
                    {"kind": "potion", "action": "acquired", "item": "Regen Potion"},
                ],
            )
            with db._connection() as conn:
                rows = conn.execute("SELECT screenshot_path, source FROM inventory_events WHERE id IN (?, ?)", ids).fetchall()
        self.assertEqual(2, len(ids))
        self.assertEqual({"reward.png"}, {row["screenshot_path"] for row in rows})
        self.assertEqual({"visible reward batch"}, {row["source"] for row in rows})


if __name__ == "__main__":
    unittest.main()
