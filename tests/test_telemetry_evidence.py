"""Focused regression coverage for Spire's review-evidence gate."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from veda.telemetry_database import TelemetryDatabase


class TelemetryEvidenceTests(unittest.TestCase):
    def test_floor_is_not_review_ready_until_evidence_and_outcomes_are_linked(self):
        with TemporaryDirectory() as directory:
            database = TelemetryDatabase(Path(directory) / "veda.sqlite3")
            run_id = database.start_or_resume_run(ascension=1)
            floor_id = database.record_floor(
                run_id=run_id, act=1, floor=1, node_type="enemy", outcome=None, starting_state={},
            )
            combat_id = database.start_combat(run_id=run_id, floor_id=floor_id, opening_state={})
            turn_id = database.start_combat_turn(
                combat_id=combat_id, turn_number=1, phase="combat", opening_state={},
            )
            decision_id = database.record_decision(
                run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id,
                phase="combat", state={}, options=[{"card": "Strike"}], recommendation={"card": "Strike"},
                reasoning="Visible safe attack.", prediction={"enemy_hp": 0},
            )

            incomplete = database.floor_completeness(floor_id=floor_id)

            database.start_combat_zones(
                combat_id=combat_id, deck=["Strike"], hand=["Strike"], source="visible opening hand",
            )
            database.resolve_decision(
                decision_id=decision_id, chosen_action={"card": "Strike"}, actual_outcome={"enemy_hp": 0},
            )
            database.complete_combat_turn(turn_id=turn_id, closing_state={})
            database.complete_combat(combat_id=combat_id, outcome="victory", closing_state={})
            database.complete_floor(floor_id=floor_id, outcome="victory", ending_state={})
            database.record_event(
                run_id=run_id, floor_id=floor_id, kind="floor_completion_evidence", phase="safe_boundary",
                state={}, screenshot_path="artifacts/observations/floor-1.png", source="passive-screen-capture",
            )
            complete = database.floor_completeness(floor_id=floor_id)

        self.assertFalse(incomplete["review_ready"])
        self.assertIn("at least one linked screenshot", incomplete["missing"])
        self.assertIn("1 unresolved decision(s)", incomplete["missing"])
        self.assertIn("combat-zone base missing for 1 combat(s)", incomplete["missing"])
        self.assertTrue(complete["review_ready"])
        self.assertEqual(complete["linked_screenshots"], 1)
        self.assertEqual(complete["decisions"]["resolved"], 1)


if __name__ == "__main__":
    unittest.main()
