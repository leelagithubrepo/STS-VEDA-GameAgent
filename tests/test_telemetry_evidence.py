"""Focused regression coverage for Spire's review-evidence gate."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_memory import _evidence_for_logging
from veda.telemetry_database import TelemetryDatabase


class TelemetryEvidenceTests(unittest.TestCase):
    def test_boss_preflight_preserves_unknowns_and_guards_high_stakes_advice(self):
        with TemporaryDirectory() as directory:
            database = TelemetryDatabase(Path(directory) / "veda.sqlite3")
            run_id = database.start_or_resume_run(ascension=1)
            floor_id = database.record_floor(
                run_id=run_id, act=2, floor=33, node_type="boss", outcome=None, starting_state={"hp": 60},
            )
            combat_id = database.start_combat(
                run_id=run_id, floor_id=floor_id, encounter_name="Bronze Automaton",
                encounter_type="boss", opening_state={"hp": 60},
            )
            turn_id = database.start_combat_turn(
                combat_id=combat_id, turn_number=1, phase="combat", opening_state={"hp": 60},
            )
            arguments = dict(
                run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id,
                phase="combat", state={"hp": 60}, options=[{"cards": ["Bash"]}],
                recommendation={"sequence": ["Bash"]}, reasoning="Visible turn-one setup.",
                prediction={"enemy_vulnerable": 2}, high_stakes=True, requires_boss_preflight=True,
            )
            with self.assertRaisesRegex(ValueError, "confirmed boss inventory preflight"):
                database.record_decision(**arguments)

            preflight_id = database.record_boss_inventory_preflight(
                run_id=run_id, floor_id=floor_id, boss_name="Bronze Automaton",
                relics=["Burning Blood"], potions=["Blood Potion"], key_cards=["Bash"],
                unknowns=["unread relic in fifth slot"], source="visible inventory", confidence=0.98,
            )
            snapshot = database.latest_boss_inventory_preflight(run_id=run_id, floor_id=floor_id)
            decision_id = database.record_decision(**arguments)
            with self.assertRaisesRegex(ValueError, "prior high-stakes sequence"):
                database.record_decision(**arguments)
            database.resolve_decision(
                decision_id=decision_id, chosen_action={"sequence": ["Bash"]}, actual_outcome={"hp": 55},
            )
            follow_up_id = database.record_decision(**arguments)

        self.assertEqual(preflight_id, snapshot["id"])
        self.assertEqual(["Burning Blood"], snapshot["state"]["relics"])
        self.assertEqual(["unread relic in fifth slot"], snapshot["payload"]["unknowns"])
        self.assertNotEqual(decision_id, follow_up_id)

    def test_run_card_is_compact_and_omits_telemetry_gap_diagnostics(self):
        with TemporaryDirectory() as directory:
            database = TelemetryDatabase(Path(directory) / "veda.sqlite3")
            run_id = database.start_or_resume_run(ascension=1)
            database.record_floor(
                run_id=run_id, act=2, floor=19, node_type="enemy", outcome="victory",
                starting_state={"hp": 52, "max_hp": 74, "gold": 130},
                ending_state={"hp": 47, "max_hp": 74, "gold": 145},
            )
            database.record_inventory_event(
                run_id=run_id, item_kind="potion", action="acquired", item_name="Blood Potion", source="visible reward",
            )
            card = database.run_card(run_id=run_id, next_priority="Choose the safest visible route.")

        self.assertEqual(47, card["health"]["hp"])
        self.assertEqual(74, card["health"]["max_hp"])
        self.assertEqual(145, card["resources"]["gold"])
        self.assertEqual(["Blood Potion"], card["resources"]["potions"])
        self.assertEqual("Choose the safest visible route.", card["next_priority"])
        self.assertNotIn("missing", card)

    def test_capture_denial_becomes_a_lower_confidence_written_fallback(self):
        parser = argparse.ArgumentParser()
        args = argparse.Namespace(capture=True, screenshot=None)

        with patch("scripts.veda_memory.capture_visible_ps5_feed", side_effect=RuntimeError("capture denied")):
            evidence = _evidence_for_logging(parser, args)

        self.assertIsNone(evidence.screenshot_path)
        self.assertEqual("written-observation-capture-unavailable", evidence.source)
        self.assertEqual(0.6, evidence.confidence_cap)
        self.assertEqual("written_fallback", evidence.metadata["evidence_mode"])

    def test_written_fallback_is_retained_but_not_presented_as_screenshot_evidence(self):
        with TemporaryDirectory() as directory:
            database = TelemetryDatabase(Path(directory) / "veda.sqlite3")
            run_id = database.start_or_resume_run(ascension=1)
            floor_id = database.record_floor(
                run_id=run_id, act=2, floor=18, node_type="enemy", outcome=None, starting_state={},
            )
            decision_id = database.record_decision(
                run_id=run_id, floor_id=floor_id, phase="reward", state={"hp": 50},
                options=[{"cards": ["Inflame"]}], recommendation={"card": "Inflame"},
                reasoning="Visible reward option.", prediction={"deck_size": 12},
                source="written-observation-capture-unavailable",
                evidence_metadata={"evidence_mode": "written_fallback", "capture_status": "unavailable"},
                confidence=0.6,
            )
            retrospective = database.floor_retrospective(floor_id=floor_id)
            completeness = database.floor_completeness(floor_id=floor_id)

        decision_evidence = next(event for event in retrospective["evidence"] if event["id"])
        self.assertEqual(decision_id, retrospective["decision_record_ids"][0])
        self.assertIsNone(decision_evidence["screenshot_path"])
        self.assertEqual("written-observation-capture-unavailable", decision_evidence["source"])
        self.assertEqual("written_fallback", decision_evidence["payload"]["evidence"]["evidence_mode"])
        self.assertFalse(completeness["review_ready"])
        self.assertIn("at least one linked screenshot", completeness["missing"])

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
