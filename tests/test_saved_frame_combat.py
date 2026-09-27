"""Atomic offline combat evidence publication and optional-dependency behavior."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_native_ocr import observation, png_bytes
from veda.native_ocr import extract_hud
from veda.saved_frame_reader import read_saved_frame


class SavedFrameCombatTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = (Path(temp.name) / "saved.png").resolve()
        self.path.write_bytes(png_bytes())
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.viewport = [0, 0, 1000, 600]
        self.observations = [observation("17/91", [280, 15, 55, 15])]
        self.reader = Mock()
        self.reader.observe.return_value = {
            "ok": True, "image_path": str(self.path), "image_sha256": self.sha,
            "source_dimensions": [1000, 600], "frame_id": "saved", "observations": self.observations,
            "hud": extract_hud(self.observations, source_dimensions=[1000, 600]), "timing_ms": {},
        }
        self.combat = {"image_sha256": self.sha, "source_dimensions": [1000, 600],
            "viewport": self.viewport, "player_block": 0,
            "enemy_hp_candidates": [{"hp": 19, "max_hp": 30}], "incoming_damage": None,
            "runtime_authorized": False, "controller_authorized": False}

    def read(self, **kwargs):
        return read_saved_frame(self.path, viewport=self.viewport, reader=self.reader,
                                refine_energy=False, refine_cards=False, refine_combat=False, **kwargs)

    def test_source_bound_combat_published_with_hud_and_coverage(self):
        with patch("veda.combat_evidence.extract_combat_evidence", return_value=self.combat):
            result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(17, result["hud"]["hp"])
        self.assertEqual(0, result["combat_evidence"]["player_block"])
        self.assertEqual(1, result["coverage"]["enemy_health_candidate_count"])
        self.assertFalse(result["coverage"]["combat_ready"])
        self.assertFalse(result["runtime_authorization_eligible"])
        self.reader.observe_regions.assert_not_called()

    def test_no_derived_readings_survive_combat_source_or_viewport_mismatch(self):
        for key, value in (("image_sha256", "f" * 64), ("source_dimensions", [999, 600]),
                           ("viewport", [10, 10, 900, 550])):
            bad = {**self.combat, key: value}
            with self.subTest(key=key), patch("veda.combat_evidence.extract_combat_evidence", return_value=bad):
                result = self.read()
            self.assertFalse(result["ok"])
            self.assertIsNone(result["hud"]["hp"])
            self.assertEqual([], result["cards"]["card_candidates"])
            self.assertNotIn("combat_evidence", result)
            self.assertNotIn("coverage", result)

    def test_changed_pixels_after_combat_extraction_clear_every_derived_section(self):
        def replace(*args, **kwargs):
            self.path.write_bytes(png_bytes(900, 600))
            return self.combat
        with patch("veda.combat_evidence.extract_combat_evidence", side_effect=replace):
            result = self.read()
        self.assertFalse(result["ok"])
        self.assertIn("image_changed_during_processing", result["issues"])
        self.assertNotIn("combat_evidence", result)
        self.assertNotIn("native_evidence", result)
        self.assertNotIn("coverage", result)

    def test_optional_combat_dependency_failure_retains_only_other_partial_evidence(self):
        with patch("veda.combat_evidence.extract_combat_evidence", side_effect=ModuleNotFoundError("PIL")):
            result = self.read()
        self.assertTrue(result["ok"])
        self.assertEqual(17, result["hud"]["hp"])
        self.assertNotIn("combat_evidence", result)
        self.assertIn("combat_analysis_dependency_unavailable", result["issues"])
        self.assertNotIn("player_block", result["coverage"]["read_fields"])

    def test_unexpected_readiness_or_total_damage_claim_rejects_entire_state(self):
        for key, value in (("incoming_damage", 31), ("combat_ready", True),
                           ("runtime_authorized", True), ("controller_authorized", True),
                           ("runtime_ready", True), ("hand_complete", True), ("enemy_count", 1),
                           ("runtime_authorized", 1), ("runtime_authorized", 0)):
            with self.subTest(key=key), patch("veda.combat_evidence.extract_combat_evidence",
                    return_value={**self.combat, key: value}):
                result = self.read()
            self.assertFalse(result["ok"])
            self.assertNotIn("combat_evidence", result)
            self.assertIsNone(result["hud"]["hp"])

    def test_combat_can_be_disabled_without_extra_ocr(self):
        with patch("veda.combat_evidence.extract_combat_evidence") as extract:
            result = self.read(read_combat=False)
        self.assertTrue(result["ok"])
        extract.assert_not_called()
        self.reader.observe_regions.assert_not_called()
        self.assertNotIn("combat_evidence", result)


class FocusedCombatPublicationTests(unittest.TestCase):
    setUp = SavedFrameCombatTests.setUp

    def run_focused(self, *, bad_intent=False, refinement_failure=False):
        combat = {**self.combat}
        refinement = {"intent_regions": [
            {"ok": True, "preprocessing": "white_text", "observations": []},
            {"ok": False, "preprocessing": "original", "error": "native_ocr_failed", "observations": []}],
            "timing_ms": 1}
        intent = {"image_sha256": "f"*64 if bad_intent else self.sha,
                  "source_dimensions": [1000, 600], "viewport": self.viewport,
                  "attack_candidates": [{"damage_per_hit": 2, "hits": 3, "attack_total": 6}],
                  "incoming_damage": None, "supported_attack_subtotal": 6}
        with patch("veda.combat_refinement.refine_combat_reading",
                   return_value={"combat_evidence": combat, "refinement": refinement},
                   side_effect=ValueError("combat_refinement_source_mismatch") if refinement_failure else None) as refine, \
             patch("veda.intent_evidence.extract_intent_evidence", return_value=intent) as extract, \
             patch("veda.intent_evidence.merge_intent_evidence", return_value=intent) as merge:
            result = read_saved_frame(self.path, viewport=self.viewport, reader=self.reader,
                                      refine_cards=False, refine_energy=False)
        return result, refine, extract, merge

    def test_default_uses_focused_combat_and_only_successful_intent_regions(self):
        result, refine, extract, merge = self.run_focused()
        self.assertTrue(result["ok"], result["issues"])
        refine.assert_called_once()
        self.assertEqual(2, extract.call_count)  # Original and the single successful crop.
        self.assertEqual(["original", "white_text"],
                         [call.kwargs["ocr_preprocessing"] for call in extract.call_args_list])
        merge.assert_called_once()
        self.assertEqual("focused_combat", result["combat_reading_mode"])
        self.assertEqual(1, result["coverage"]["attack_candidate_count"])
        self.assertFalse(result["coverage"]["intent_coverage_complete"])
        self.assertIsNone(result["combat_evidence"]["incoming_damage"])

    def test_focused_combat_integrity_failure_clears_all_evidence(self):
        result, _, extract, _ = self.run_focused(refinement_failure=True)
        self.assertFalse(result["ok"])
        extract.assert_not_called()
        self.assertIsNone(result["hud"]["hp"])
        self.assertNotIn("combat_evidence", result)
        self.assertNotIn("coverage", result)

    def test_intent_identity_failure_clears_even_successful_block_evidence(self):
        result, _, _, _ = self.run_focused(bad_intent=True)
        self.assertFalse(result["ok"])
        self.assertIn("intent_source_mismatch", result["issues"])
        self.assertNotIn("combat_evidence", result)
        self.assertNotIn("coverage", result)
        self.assertIsNone(result["hud"]["hp"])


if __name__ == "__main__": unittest.main()
