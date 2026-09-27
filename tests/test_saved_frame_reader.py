"""Coordinator boundaries, using generated PNGs and a fake native reader."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.read_saved_frame import main
from tests.test_native_ocr import png_bytes, observation
from veda.native_ocr import SCHEMA, extract_hud
from veda.saved_frame_reader import read_saved_frame


class SavedFrameReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "saved.png"
        self.image.write_bytes(png_bytes())
        self.digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.native = {
            "ok": True, "image_path": str(self.image.resolve()), "image_sha256": self.digest,
            "source_dimensions": [1000, 600], "frame_id": "saved", "observations": [],
            "hud": {"hp": 17, "max_hp": 91, "energy": 0, "energy_max": 3},
            "timing_ms": {"total": 3},
        }
        self.native.update(schema=SCHEMA, parent_image_sha256=self.digest,
                           parent_frame_id="saved", runtime_authorization_eligible=False)
        self.native["observations"] = [observation("17/91", [280, 15, 55, 15]),
                                       observation("0/3", [70, 490, 40, 20])]
        self.native["hud"] = extract_hud(self.native["observations"], source_dimensions=[1000, 600])
        self.reader = Mock()
        self.reader.observe.return_value = self.native
        self.cards = {"card_candidates": [{"name": None, "upgraded": None, "current_cost": None}],
                      "image_sha256": self.digest, "source_dimensions": [1000, 600],
                      "hand_complete": None}

    def read(self, **kwargs):
        with patch("veda.card_regions.detect_card_regions", return_value=self.cards):
            return read_saved_frame(self.image, reader=self.reader,
                                    viewport=kwargs.pop("viewport", [0, 0, 1000, 600]), refine_cards=False,
                                    refine_energy=False, read_combat=False, **kwargs)

    def test_zero_energy_preserved_without_authorizing_or_certifying_full_state(self):
        result = self.read(expected_sha256=self.digest)
        self.assertTrue(result["ok"])
        self.assertEqual(0, result["hud"]["energy"])
        self.assertTrue(result["partial"])
        for key in ("runtime_authorized", "controller_authorized", "runtime_authorization_eligible"):
            self.assertFalse(result[key])
        self.assertIsNone(result["captured_at"])
        self.assertIsNone(result["cards"]["hand_complete"])
        self.assertIsNone(result["cards"]["card_candidates"][0]["name"])
        self.assertIn("controller_focus", result["unavailable_fields"])

    def test_explicit_valid_viewport_required_before_helper_call(self):
        for viewport in (None, [], [-1, 0, 1000, 600], [0, 0, 1001, 600],
                         [0, 0, 0, 600], [0, 0, float("nan"), 600], [False, 0, 1000, 600]):
            with self.subTest(viewport=viewport):
                result = self.read(viewport=viewport)
                self.assertFalse(result["ok"])
                self.assertIn("invalid_explicit_viewport", result["issues"])
        self.reader.observe.assert_not_called()

    def test_unexpected_source_rejected_before_reader_starts(self):
        for digest in ("0" * 64, "not-a-hash"):
            result = self.read(expected_sha256=digest)
            self.assertFalse(result["ok"])
            self.assertIsNone(result["hud"]["hp"])
        self.reader.observe.assert_not_called()

    def test_native_failure_and_identity_mismatch_clear_all_readings(self):
        for key, value in (("ok", False), ("image_path", "wrong.png"),
                           ("image_sha256", "f" * 64), ("frame_id", "wrong-frame"),
                           ("source_dimensions", [1000, 700])):
            with self.subTest(key=key):
                self.reader.observe.return_value = {**self.native, key: value}
                result = self.read()
                self.assertFalse(result["ok"])
                self.assertIsNone(result["hud"]["hp"])
                self.assertEqual([], result["cards"]["card_candidates"])

    def test_replaced_image_during_card_analysis_rejects_both_hud_and_cards(self):
        def change_image(*args, **kwargs):
            self.image.write_bytes(png_bytes(900, 600))
            return self.cards
        with patch("veda.card_regions.detect_card_regions", side_effect=change_image):
            result = read_saved_frame(self.image, viewport=[0, 0, 1000, 600], reader=self.reader,
                                      refine_cards=False, refine_energy=False, read_combat=False)
        self.assertFalse(result["ok"])
        self.assertIn("image_changed_during_processing", result["issues"])
        self.assertIsNone(result["hud"]["hp"])
        self.assertEqual([], result["cards"]["card_candidates"])

    def test_missing_optional_pixel_dependency_preserves_only_partial_hud(self):
        with patch("veda.card_regions.detect_card_regions", side_effect=ModuleNotFoundError("PIL")):
            result = read_saved_frame(self.image, viewport=[0, 0, 1000, 600], reader=self.reader, refine_energy=False, read_combat=False)
        self.assertTrue(result["ok"])
        # No heart or red numeral pixels support the mocked HP on this blank PNG.
        self.assertIsNone(result["hud"]["hp"])
        self.assertEqual(0, result["hud"]["energy"])
        self.assertIn("hp_unknown:missing_heart_backed_hp_fraction", result["issues"])
        self.assertEqual([], result["cards"]["card_candidates"])
        self.assertIn("card_analysis_dependency_unavailable", result["issues"])

    def test_complete_hand_claim_from_candidate_detector_is_rejected(self):
        self.cards["hand_complete"] = True
        result = self.read()
        self.assertFalse(result["ok"])
        self.assertIn("unsupported_hand_completeness_claim", result["issues"])
        self.assertIsNone(result["hud"]["hp"])

    def test_card_identity_mismatch_rejects_all_derived_state(self):
        for key, value in (("image_sha256", "f" * 64), ("source_dimensions", [1000, 700])):
            with self.subTest(key=key):
                original = self.cards[key]
                self.cards[key] = value
                result = self.read()
                self.assertFalse(result["ok"])
                self.assertIn("card_source_mismatch", result["issues"])
                self.assertIsNone(result["hud"]["hp"])
                self.assertEqual([], result["cards"]["card_candidates"])
                self.cards[key] = original

    def test_non_png_and_missing_source_do_not_start_reader(self):
        self.image.write_bytes(b"not a png")
        self.assertFalse(self.read()["ok"])
        self.image.unlink()
        self.assertFalse(self.read()["ok"])
        self.reader.observe.assert_not_called()

    def test_cli_requires_viewport_and_distinguishes_processing_failure(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main([str(self.image), "--native-helper", "missing"])
        self.assertEqual(2, caught.exception.code)
        with redirect_stdout(io.StringIO()) as output:
            code = main([str(self.image), "--native-helper", "missing", "--viewport", "0", "0", "1000", "600"])
        self.assertEqual(1, code)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_cli_single_pass_disables_every_focused_ocr_stage(self):
        with patch("scripts.read_saved_frame.read_saved_frame", return_value={"ok": True}) as read, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(0, main([str(self.image), "--native-helper", "missing",
                                    "--viewport", "0", "0", "1000", "600", "--single-pass"]))
        for key in ("refine_cards", "refine_energy", "refine_combat"):
            self.assertIs(read.call_args.kwargs[key], False)


if __name__ == "__main__":
    unittest.main()
