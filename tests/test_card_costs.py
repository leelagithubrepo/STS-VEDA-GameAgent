"""Synthetic pixels and supplied OCR only; no native recognition is run."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None

from veda.card_costs import read_card_cost


@unittest.skipIf(Image is None, "Pillow required for offline pixel fixtures")
class CardCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cost.png"
        self.image = Image.new("RGB", (320, 240), (25, 25, 25))
        self.draw = ImageDraw.Draw(self.image)
        self.viewport = [0, 0, 320, 240]
        self.candidate = {"cost_search_box_px": [60, 60, 200, 180], "name": None}
        self.rows = []

    def orb(self, center=(110, 110), radius=26):
        cx, cy = center
        self.draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=(185, 110, 15))

    def numeral(self, text="1", box=(102, 94, 118, 127), alternatives=None, confidence=1):
        left, top, right, bottom = box
        # A simple visible glyph is sufficient: OCR is supplied, not rerun.
        self.draw.rectangle((left+3, top+3, min(right-3, left+7), bottom-3), fill=(240, 240, 240))
        row = {"box_original_pixels_top_left": [left, top, right-left, bottom-top],
               "candidates": alternatives or [{"text": text, "confidence": confidence}]}
        self.rows.append(row)
        return row

    def envelope(self):
        self.image.save(self.path)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return {"ok": True, "id": "cost-0", "image_sha256": digest,
                "parent_image_sha256": digest, "source_dimensions": [320, 240],
                "box_original_pixels_ltrb": self.candidate["cost_search_box_px"],
                "preprocessing": "original", "observations": self.rows}

    def read(self, envelope=None):
        return read_card_cost(self.path, viewport=self.viewport, candidate=self.candidate,
                              observations=self.envelope() if envelope is None else envelope)

    def test_unknown_title_does_not_prevent_literal_nonstandard_cost(self):
        self.orb()
        self.numeral("7")
        result = self.read()
        self.assertEqual(result["current_cost"], 7)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["evidence"]["box_px"], [102, 94, 118, 127])
        self.assertFalse(result["runtime_authorized"])

    def test_zero_is_observed_zero_not_missing(self):
        self.orb()
        self.numeral("0")
        self.assertEqual(self.read()["current_cost"], 0)

    def test_title_and_old_asserted_cost_do_not_supply_values(self):
        self.orb()
        self.candidate.update(name="Some Known Card", current_cost=1)
        self.assertIsNone(self.read()["current_cost"])

    def test_wide_rotated_digit_box_is_not_limited_by_title_font(self):
        self.orb()
        self.numeral("1", box=(89, 88, 130, 134))
        self.candidate["provenance"] = {"header_geometry": {"font_height_px": 15}}
        self.assertEqual(self.read()["current_cost"], 1)

    def test_conflicting_numeric_alternative_ignores_confidence_ranking(self):
        self.orb()
        self.numeral(alternatives=[{"text": "1", "confidence": 1}, {"text": "7", "confidence": .01}])
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertTrue(result["ambiguous"])
        self.assertIn("conflicting_numeric_readings", result["ambiguity_reasons"])
        self.assertTrue(any("conflicting numeric" in issue for issue in result["issues"]))
        self.assertEqual(result["evidence"]["possible_cost_boxes"], [[102, 94, 118, 127]])

    def test_conflicting_separate_observations_stay_unknown(self):
        self.orb()
        self.numeral("1")
        self.numeral("7")
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertEqual(len(result["evidence"]["possible_cost_boxes"]), 2)
        self.assertTrue(any("conflicting numeric" in issue for issue in result["issues"]))

    def test_duplicate_numeric_observations_are_not_certified_unique(self):
        self.orb()
        self.numeral("1")
        self.rows.append(deepcopy(self.rows[0]))
        self.assertIsNone(self.read()["current_cost"])

    def test_nonnumeric_top_is_not_repaired_from_numeric_alternative(self):
        self.orb()
        self.numeral(alternatives=[{"text": "O", "confidence": 1}, {"text": "0", "confidence": .9}])
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertEqual(result["evidence"]["possible_cost_boxes"], [[102, 94, 118, 127]])

    def test_nonnumeric_alternative_does_not_change_literal_numeric_top(self):
        self.orb()
        self.numeral(alternatives=[{"text": "0", "confidence": 1}, {"text": "O", "confidence": .3}])
        self.assertEqual(self.read()["current_cost"], 0)

    def test_low_confidence_preserves_possible_association_without_value(self):
        self.orb()
        self.numeral("2", confidence=.4)
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertFalse(result["ambiguous"])
        self.assertTrue(result["evidence"]["possible_cost_boxes"])

    def test_no_orb_or_wrong_colour_cannot_support_numeral(self):
        self.numeral("2")
        self.assertIsNone(self.read()["current_cost"])
        self.draw.ellipse((84, 84, 136, 136), fill=(40, 225, 225))
        self.assertIsNone(self.read()["current_cost"])

    def test_multiple_orbs_are_ambiguous_even_if_only_one_digit_was_read(self):
        self.orb(center=(95, 110), radius=22)
        self.orb(center=(160, 110), radius=22)
        self.numeral("1", box=(89, 97, 101, 124))
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertTrue(result["ambiguous"])
        self.assertIn("multiple_possible_orbs", result["ambiguity_reasons"])
        self.assertEqual(len(result["evidence"]["orb_boxes"]), 2)
        self.assertTrue(result["evidence"]["possible_cost_boxes"])

    def test_small_stray_gold_fleck_is_not_a_second_cost_symbol(self):
        self.orb()
        self.numeral("2")
        self.draw.ellipse((166, 150, 172, 156), fill=(185, 110, 15))
        result = self.read()
        self.assertEqual(result["current_cost"], 2)
        self.assertFalse(result["ambiguous"])
        self.assertEqual(len(result["evidence"]["orb_boxes"]), 1)

    def test_small_gold_fragment_inside_a_ring_is_not_a_nested_symbol(self):
        self.orb()
        self.draw.ellipse((96, 95, 124, 125), fill=(25, 25, 25))
        self.draw.ellipse((116, 113, 121, 119), fill=(185, 110, 15))
        self.numeral("1")
        result = self.read()
        self.assertEqual(result["current_cost"], 1)
        self.assertFalse(result["ambiguous"])
        self.assertEqual(len(result["evidence"]["orb_boxes"]), 1)

    def test_fragment_rejection_scales_with_the_automatic_search_region(self):
        self.candidate["cost_search_box_px"] = [88, 88, 132, 132]
        self.orb(radius=14)
        self.numeral("0", box=(106, 101, 114, 119))
        self.draw.ellipse((88, 89, 92, 93), fill=(185, 110, 15))
        result = self.read()
        self.assertEqual(result["current_cost"], 0)
        self.assertFalse(result["ambiguous"])
        self.assertEqual(result["evidence"]["minimum_orb_side_px"], 11)

    def test_small_clipped_edge_fragment_does_not_veto_a_complete_orb(self):
        self.candidate["cost_search_box_px"] = [80, 80, 144, 145]
        self.orb(center=(112, 106), radius=20)
        self.numeral("1", box=(105, 93, 119, 119))
        self.draw.ellipse((73, 126, 89, 144), fill=(185, 110, 15))
        result = self.read()
        self.assertEqual(result["current_cost"], 1)
        self.assertFalse(result["ambiguous"])
        self.assertEqual(len(result["evidence"]["orb_boxes"]), 1)

    def test_two_differently_sized_plausible_orbs_still_veto(self):
        self.orb(center=(90, 110), radius=24)
        self.orb(center=(170, 110), radius=16)
        self.numeral("1", box=(83, 95, 97, 125))
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertTrue(result["ambiguous"])
        self.assertEqual(len(result["evidence"]["orb_boxes"]), 2)

    def test_clipped_orb_stays_unknown(self):
        self.orb(center=(75, 110), radius=26)
        self.numeral("1", box=(68, 94, 82, 127))
        result = self.read()
        self.assertIsNone(result["current_cost"])
        self.assertTrue(any("clipped" in issue for issue in result["issues"]))

    def test_one_sided_warm_fragment_does_not_supply_orb_surround(self):
        self.draw.rectangle((84, 84, 111, 136), fill=(185, 110, 15))
        self.numeral("1")
        self.assertIsNone(self.read()["current_cost"])

    def test_solid_warm_panel_is_not_a_rounded_cost_symbol(self):
        self.draw.rectangle((84, 84, 136, 136), fill=(185, 110, 15))
        self.numeral("1")
        self.assertIsNone(self.read()["current_cost"])

    def test_occluded_or_absent_numeral_is_unknown(self):
        self.orb()
        self.assertIsNone(self.read()["current_cost"])
        self.numeral("?")
        self.assertIsNone(self.read()["current_cost"])

    def test_malformed_or_outside_observation_cannot_be_silently_ignored(self):
        self.orb()
        self.numeral("1")
        self.rows.append({"box_original_pixels_top_left": [0, 0, 2, 2], "candidates": []})
        with self.assertRaisesRegex(ValueError, "malformed_observation"):
            self.read()
        self.rows[-1] = {"box_original_pixels_top_left": [0, 0, 2, 2],
                         "candidates": [{"text": "2", "confidence": 1}]}
        with self.assertRaisesRegex(ValueError, "observation_outside_region"):
            self.read()

    def test_observation_precision_matches_native_on_all_four_region_edges(self):
        from tests.test_native_ocr import observation
        from veda.native_ocr import _validate_observations

        self.orb()
        self.numeral("1")
        valid_rows = deepcopy(self.rows)
        for spill in (0, 1e-9, .005, .01):
            for side, box in (("left", [60-spill, 90, 15, 15]),
                              ("top", [90, 60-spill, 15, 15]),
                              ("right", [185, 90, 15+spill, 15]),
                              ("bottom", [90, 165, 15, 15+spill])):
                with self.subTest(side=side, spill=spill):
                    edge = observation("edge text", box)
                    _validate_observations([edge], [320, 240], [60, 60, 200, 180])
                    self.rows = deepcopy(valid_rows) + [edge]
                    envelope = self.envelope()
                    before = deepcopy(envelope)
                    self.assertEqual(1, self.read(envelope)["current_cost"])
                    self.assertEqual(before, envelope)  # No clipping or coordinate rewrite.

    def test_observation_beyond_native_precision_is_rejected_on_every_edge(self):
        from tests.test_native_ocr import observation
        from veda.native_ocr import _ReaderFailure, _validate_observations

        for spill in (.010001, .1, 1):
            for side, box in (("left", [60-spill, 90, 15, 15]),
                              ("top", [90, 60-spill, 15, 15]),
                              ("right", [185, 90, 15+spill, 15]),
                              ("bottom", [90, 165, 15, 15+spill])):
                with self.subTest(side=side, spill=spill):
                    edge = observation("edge text", box)
                    with self.assertRaises(_ReaderFailure):
                        _validate_observations([edge], [320, 240], [60, 60, 200, 180])
                    self.rows = [edge]
                    with self.assertRaisesRegex(ValueError, "observation_outside_region:0"):
                        self.read()

    def test_observation_tolerance_does_not_apply_to_viewport_or_search_geometry(self):
        envelope = self.envelope()
        self.viewport = [-1e-9, 0, 320, 240]
        with self.assertRaisesRegex(ValueError, "viewport_outside"):
            self.read(envelope)
        self.viewport = [0, 0, 320, 240]
        self.candidate["cost_search_box_px"] = [60-1e-9, 60, 200, 180]
        with self.assertRaisesRegex(ValueError, "search_outside_viewport_or_noninteger"):
            self.read(envelope)
        self.candidate["cost_search_box_px"] = [60, 60, 200, 180]
        envelope["box_original_pixels_ltrb"] = [60-1e-9, 60, 200, 180]
        with self.assertRaisesRegex(ValueError, "region_mismatch"):
            self.read(envelope)

    def test_source_envelope_required_and_hash_dimensions_verified(self):
        envelope = self.envelope()
        with self.assertRaisesRegex(ValueError, "source_envelope_required"):
            self.read([])
        for key, value in (("image_sha256", "0"*64), ("parent_image_sha256", "1"*64),
                           ("source_dimensions", [321, 240])):
            changed = deepcopy(envelope)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "source_mismatch"):
                self.read(changed)

    def test_candidate_source_mismatch_is_fatal(self):
        self.candidate["image_sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "candidate_source_mismatch"):
            self.read()

    def test_region_bounds_or_preprocessing_mismatch_is_fatal(self):
        envelope = self.envelope()
        for key, value in (("box_original_pixels_ltrb", [61, 60, 200, 180]),
                           ("preprocessing", "green_text")):
            changed = deepcopy(envelope)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "region_mismatch"):
                self.read(changed)

    def test_invalid_viewport_and_unbounded_search_rejected(self):
        envelope = self.envelope()
        self.viewport = [0, 0, 321, 240]
        with self.assertRaisesRegex(ValueError, "viewport_outside"):
            self.read(envelope)
        self.viewport = [0, 0, 320, 240]
        self.candidate["cost_search_box_px"] = [-1, 60, 200, 180]
        with self.assertRaisesRegex(ValueError, "search_outside"):
            self.read(envelope)

    def test_pixel_sample_bound_is_enforced_before_sampling(self):
        self.image = Image.new("RGB", (1000, 800))
        self.viewport = [0, 0, 1000, 800]
        self.candidate["cost_search_box_px"] = [0, 0, 1000, 800]
        with self.assertRaisesRegex(ValueError, "region_pixel_bound"):
            self.read()

    def test_failed_region_returns_unknown_with_checked_source(self):
        envelope = self.envelope()
        envelope.update(ok=False, error="region_failed")
        result = self.read(envelope)
        self.assertIsNone(result["current_cost"])
        self.assertEqual(result["image_sha256"], envelope["image_sha256"])
        self.assertIn("failed", result["issues"][0])

    def test_source_replacement_during_pixel_analysis_is_fatal(self):
        self.orb()
        self.numeral("1")
        envelope = self.envelope()
        from veda import card_costs
        real_orbs = card_costs._orbs

        def replace(image, search):
            self.path.write_bytes(b"replaced")
            return real_orbs(image, search)

        with patch("veda.card_costs._orbs", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "source_changed"):
                self.read(envelope)


if __name__ == "__main__":
    unittest.main()
