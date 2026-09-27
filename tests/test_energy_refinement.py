"""Energy pass fusion with saved PNGs and mocked native responses only."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tests.test_native_ocr import observation, png_bytes
from veda.energy_refinement import refine_energy_reading
from veda.native_ocr import REGIONS_SCHEMA, SCHEMA, extract_hud


class EnergyRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "saved.png"
        self.path.write_bytes(png_bytes())
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.viewport = [0, 0, 1000, 600]
        self.source = {"image_path": str(self.path.resolve()), "frame_id": "saved",
                       "parent_frame_id": "saved", "image_sha256": self.digest,
                       "parent_image_sha256": self.digest, "source_dimensions": [1000, 600]}
        self.native = {**self.source, "schema": SCHEMA, "ok": True,
                       "observations": [observation("17/91", [280, 15, 55, 15])]}
        self.set_original()
        self.focused = []
        self.batch_error = None
        self.region_error = None
        self.region_mutation = None
        self.batch_mutation = None
        self.change_file = False
        self.reader = Mock()
        self.reader.observe_regions.side_effect = self.native_response

    @staticmethod
    def energy(text, *, confidence=1, x=70):
        return observation(text, [x, 490, 40, 20], confidence)

    def set_original(self, *rows):
        self.native["observations"] = [observation("17/91", [280, 15, 55, 15]), *deepcopy(rows)]
        self.native["hud"] = extract_hud(self.native["observations"], source_dimensions=[1000, 600],
                                         regions={"viewport": self.viewport})

    def native_response(self, path, *, frame_id, regions):
        request = regions[0]
        region = {**self.source, "id": request["id"], "scale": 3,
                  "ok": self.region_error is None and self.batch_error is None,
                  "error": self.region_error or self.batch_error,
                  "box_original_pixels_ltrb": list(request["box"]), "preprocessing": "original",
                  "observations": [] if self.region_error or self.batch_error else deepcopy(self.focused)}
        if self.region_error == "native_ocr_outside_region":
            region["rejected_observation_count"] = 1
        if self.region_mutation:
            self.region_mutation(region)
        response = {**self.source, "schema": REGIONS_SCHEMA, "scale": 3,
                    "ok": self.batch_error is None, "error": self.batch_error,
                    "regions": [region], "timing_ms": {"ocr": 1}}
        if self.batch_mutation:
            self.batch_mutation(response)
        if self.change_file:
            self.path.write_bytes(png_bytes(1001, 600))
        return response

    def read(self):
        return refine_energy_reading(self.path, viewport=self.viewport, native=self.native,
                                     reader=self.reader, frame_id="saved")

    def assert_energy(self, result, value, maximum):
        self.assertEqual((value, maximum), (result["hud"]["energy"], result["hud"]["energy_max"]))
        self.assertEqual((17, 91), (result["hud"]["hp"], result["hud"]["max_hp"]))
        self.assertFalse(result["refinement"]["runtime_authorized"])
        self.assertFalse(result["refinement"]["controller_authorized"])

    def test_missing_original_is_recovered_from_one_literal_crop(self):
        self.set_original(self.energy("/3"))
        self.focused = [self.energy("1/3")]
        original = deepcopy(self.native)
        result = self.read()
        self.assert_energy(result, 1, 3)
        self.assertEqual("refined", result["refinement"]["status"])
        self.assertIsNone(result["hud"]["errors"]["energy"])
        self.assertEqual(original, self.native)
        self.reader.observe_regions.assert_called_once()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual([{"id": "energy", "box": [45, 450, 170, 558], "preprocessing": "original"}], requests)
        self.assertEqual("/3", result["refinement"]["original"]["selected"][0]["candidates"][0]["text"])

    def test_viewport_translates_and_rounds_outward_without_expected_values(self):
        self.viewport = [100, 50, 900, 551]
        self.set_original()
        self.focused = []
        result = self.read()
        self.assertEqual([136, 425, 236, 516], result["refinement"]["requested_region"]["box"])
        self.assertEqual([136, 425, 236, 516], result["hud"]["energy_extraction_region_px"])
        self.assertEqual([136.0, 425.75, 236.0, 515.9300000000001], result["hud"]["regions"]["pixels"]["energy"])

    def test_zero_and_energy_above_denominator_are_valid(self):
        for text, expected in (("0/3", (0, 3)), ("5/3", (5, 3)), (" 1 / 3 ", (1, 3))):
            with self.subTest(text=text):
                self.focused = [self.energy(text)]
                result = self.read()
                self.assert_energy(result, *expected)
                self.assertIsNone(result["hud"]["errors"]["energy"])

    def test_missing_focused_text_retains_only_strictly_valid_original(self):
        self.set_original(self.energy("2/3"))
        result = self.read()
        self.assert_energy(result, 2, 3)
        self.assertEqual("original_retained", result["refinement"]["status"])
        self.assertIsNone(result["refinement"]["focused"]["fraction"])

    def test_agreeing_passes_preserve_original_hp_evidence_and_errors(self):
        self.set_original(self.energy("0/3"))
        self.focused = [self.energy("0/3")]
        result = self.read()
        self.assert_energy(result, 0, 3)
        self.assertEqual("agreed", result["refinement"]["status"])
        self.assertEqual(self.native["hud"]["evidence"]["hp"], result["hud"]["evidence"]["hp"])
        self.assertEqual(self.native["hud"]["errors"]["hp"], result["hud"]["errors"]["hp"])

    def test_fragments_are_never_joined_or_repaired(self):
        for fragments in (("1", "/3"), ("3",), ("l/3",), ("1|3",), ("Energy 1/3",)):
            with self.subTest(fragments=fragments):
                self.focused = [self.energy(text, x=70+i*45) for i, text in enumerate(fragments)]
                self.assert_energy(self.read(), None, None)

    def test_alternative_cannot_repair_malformed_top_in_either_pass(self):
        row = self.energy("l/3")
        row["candidates"].append({"text": "1/3", "confidence": .99})
        for original in (False, True):
            with self.subTest(original=original):
                self.set_original(row) if original else self.set_original()
                self.focused = [] if original else [row]
                result = self.read()
                self.assert_energy(result, None, None)
                self.assertEqual("unknown", result["refinement"]["status"])

    def test_original_hud_is_reparsed_with_stricter_confidence(self):
        self.set_original(self.energy("1/3", confidence=.79))
        self.assertEqual(1, self.native["hud"]["energy"])
        self.assert_energy(self.read(), None, None)

    def test_same_image_with_different_hud_viewport_or_regions_is_rejected(self):
        for config in ({"viewport": [100, 0, 1000, 600]},
                       {"viewport": self.viewport, "energy": [.04, .74, .18, .94]}):
            with self.subTest(config=config):
                self.native["hud"] = extract_hud(self.native["observations"],
                                                 source_dimensions=[1000, 600], regions=config)
                with self.assertRaisesRegex(ValueError, "original_hud_mismatch"):
                    self.read()
        self.reader.observe_regions.assert_not_called()

    def test_same_image_stale_or_boolean_hud_values_are_rejected(self):
        for field, value in (("hp", 18), ("max_hp", 92), ("energy", 9), ("energy_max", 4), ("energy", True)):
            with self.subTest(field=field, value=value):
                self.set_original(self.energy("1/3"))
                self.native["hud"][field] = value
                with self.assertRaisesRegex(ValueError, "original_hud_mismatch"):
                    self.read()
        self.reader.observe_regions.assert_not_called()

    def test_confidence_threshold_applies_to_both_passes(self):
        for original in (False, True):
            for confidence, expected in ((.79, None), (.8, 2)):
                with self.subTest(original=original, confidence=confidence):
                    row = self.energy("2/3", confidence=confidence)
                    self.set_original(row) if original else self.set_original()
                    self.focused = [] if original else [row]
                    self.assert_energy(self.read(), expected, 3 if expected is not None else None)

    def test_any_conflicting_exact_alternative_vetoes_both_passes(self):
        for malformed_top in (False, True):
            row = self.energy("l/3" if malformed_top else "1/3")
            row["candidates"].append({"text": "2/3", "confidence": .001})
            for original in (False, True):
                with self.subTest(malformed_top=malformed_top, original=original):
                    self.set_original(row) if original else self.set_original(self.energy("1/3"))
                    self.focused = [self.energy("1/3")] if original else [row]
                    result = self.read()
                    self.assert_energy(result, None, None)
                    self.assertEqual("conflict", result["refinement"]["status"])

    def test_multiple_matching_observations_are_ambiguous_even_when_equal(self):
        rows = [self.energy("1/3", x=65), self.energy("1/3", x=120)]
        for original in (False, True):
            with self.subTest(original=original):
                self.set_original(*rows) if original else self.set_original(self.energy("1/3"))
                self.focused = [self.energy("1/3")] if original else rows
                result = self.read()
                self.assert_energy(result, None, None)
                self.assertIn("multiple_matching_observations", result["refinement"]["ambiguity_reasons"])

    def test_zero_denominator_in_any_exact_candidate_vetoes(self):
        row = self.energy("1/3")
        row["candidates"].append({"text": "1/0", "confidence": .01})
        self.set_original(row)
        self.focused = [self.energy("1/3")]
        self.assert_energy(self.read(), None, None)

    def test_conflicting_current_or_maximum_across_passes_stays_unknown(self):
        self.set_original(self.energy("1/3"))
        for text in ("2/3", "1/4"):
            with self.subTest(text=text):
                self.focused = [self.energy(text)]
                self.assert_energy(self.read(), None, None)

    def test_expected_operational_or_region_failures_keep_valid_original(self):
        self.set_original(self.energy("2/3"))
        for scope, error in (("batch", "helper_timeout"), ("batch", "helper_not_prebuilt_or_executable"),
                             ("region", "native_ocr_failed"), ("region", "native_ocr_outside_region")):
            with self.subTest(scope=scope, error=error):
                self.batch_error = error if scope == "batch" else None
                self.region_error = error if scope == "region" else None
                self.assert_energy(self.read(), 2, 3)

    def test_integrity_failure_is_never_operational_abstention(self):
        self.set_original(self.energy("2/3"))
        for error in ("helper_observation_outside_region", "image_changed_during_ocr", "unknown", None):
            with self.subTest(error=error):
                self.batch_mutation = lambda response: response.update(ok=False, error=error)
                with self.assertRaisesRegex(ValueError, "integrity_failure"):
                    self.read()

    def test_all_original_source_fields_are_bound_before_native_call(self):
        for key, value in (("image_path", "wrong.png"), ("image_sha256", "f"*64),
                           ("parent_image_sha256", "f"*64), ("source_dimensions", [999, 600]),
                           ("frame_id", "wrong"), ("parent_frame_id", "wrong"), ("schema", "wrong")):
            with self.subTest(key=key):
                original = self.native[key]
                self.native[key] = value
                with self.assertRaisesRegex(ValueError, "source_mismatch"):
                    self.read()
                self.native[key] = original
        self.reader.observe_regions.assert_not_called()

    def test_all_focused_source_fields_are_bound(self):
        for key, value in (("image_path", "wrong.png"), ("image_sha256", "f"*64),
                           ("parent_image_sha256", "f"*64), ("source_dimensions", [999, 600]),
                           ("frame_id", "wrong"), ("parent_frame_id", "wrong"), ("schema", "wrong")):
            with self.subTest(key=key):
                self.batch_mutation = lambda response: response.update({key: value})
                with self.assertRaisesRegex(ValueError, "source_mismatch"):
                    self.read()

    def test_region_identity_geometry_and_preprocessing_must_match(self):
        for key, value in (("id", "card"), ("box_original_pixels_ltrb", [45, 450, 171, 558]),
                           ("preprocessing", "green_text"), ("image_sha256", "f"*64),
                           ("parent_image_sha256", "f"*64), ("source_dimensions", [999, 600]),
                           ("image_path", "wrong.png"), ("parent_frame_id", "wrong"), ("scale", 1)):
            with self.subTest(key=key):
                self.region_mutation = lambda region: region.update({key: value})
                with self.assertRaisesRegex(ValueError, "region_mismatch"):
                    self.read()

    def test_missing_duplicate_or_unexpected_regions_reject_entire_result(self):
        for count in (0, 2):
            self.batch_mutation = lambda response: response.update(regions=response["regions"]*count)
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "region_count"):
                self.read()

    def test_outside_crop_observation_and_malformed_evidence_are_rejected(self):
        for row in (self.energy("1/3", x=169), {"candidates": []}):
            self.focused = [row]
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, "invalid_region"):
                self.read()

    def test_failed_region_cannot_smuggle_text_and_requires_rejection_count(self):
        self.region_error = "native_ocr_outside_region"
        for key, value in (("observations", [self.energy("2/3")]),
                           ("rejected_observation_count", 0), ("rejected_observation_count", True)):
            self.region_mutation = lambda region: region.update({key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.read()

    def test_source_changes_during_reader_call_reject_all_derived_values(self):
        self.set_original(self.energy("2/3"))
        self.focused = [self.energy("2/3")]
        self.change_file = True
        with self.assertRaisesRegex(ValueError, "image_changed"):
            self.read()

    def test_invalid_viewport_fails_before_native_call(self):
        self.viewport = [0, 0, 1001, 600]
        with self.assertRaises(ValueError):
            self.read()
        self.reader.observe_regions.assert_not_called()

    def test_oversized_fraction_conversion_abstains_without_integer_limit_exception(self):
        self.focused = [self.energy("1"*100+"/3")]
        self.assert_energy(self.read(), None, None)


if __name__ == "__main__":
    unittest.main()
