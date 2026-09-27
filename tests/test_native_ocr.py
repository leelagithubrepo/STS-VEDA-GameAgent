"""Saved PNG/JSON fixtures only; never call Vision, capture, or controller APIs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

from veda.native_ocr import NativeTextReader, _ReaderFailure, _run_bounded, extract_hud


def png_bytes(width=1000, height=600):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * width * 3) * height)) + chunk(b"IEND", b""))


def observation(text, box, confidence=1.0):
    x, y, width, height = box
    return {"candidates": [{"text": text, "confidence": confidence}],
            "box_original_pixels_top_left": list(box),
            "quadrilateral_original_pixels_top_left": [[x, y], [x+width, y],
                                                       [x+width, y+height], [x, y+height]]}


class HudExtractionTests(unittest.TestCase):
    def test_default_regions_exclude_enemy_health_and_parse_only_literal_hud(self):
        observations = [observation("17/91", [280, 15, 55, 15]),
                        observation("5/3", [70, 490, 40, 20]),
                        observation("99/99", [500, 300, 55, 15])]
        result = extract_hud(observations, source_dimensions=[1000, 600])
        self.assertEqual((17, 91, 5, 3), tuple(result[k] for k in ("hp", "max_hp", "energy", "energy_max")))
        self.assertTrue(all(error is None for error in result["errors"].values()))

    def test_desktop_viewport_translation_and_explicit_regions(self):
        observations = [observation("23/77", [380, 215, 55, 15]),
                        observation("2/3", [170, 690, 40, 20])]
        result = extract_hud(observations, source_dimensions=[1400, 1000],
                             regions={"viewport": [100, 200, 1100, 800]})
        self.assertEqual(23, result["hp"])
        self.assertEqual(2, result["energy"])
        # Geometry, not a matching expected value, determines membership.
        result = extract_hud(observations, source_dimensions=[1400, 1000],
                             regions={"viewport": [0, 0, 1000, 600]})
        self.assertIsNone(result["hp"])

    def test_candidate_disagreement_is_unknown_even_with_low_confidence(self):
        item = observation("17/91", [280, 15, 55, 15])
        item["candidates"].append({"text": "17/97", "confidence": .001})
        result = extract_hud([item], source_dimensions=[1000, 600])
        self.assertIsNone(result["hp"])
        self.assertIsNone(result["max_hp"])
        self.assertEqual("conflicting_candidates", result["errors"]["hp"])

    def test_multiple_distinct_observations_also_conflict(self):
        result = extract_hud([observation("17/91", [260, 10, 40, 15]),
                              observation("18/91", [320, 10, 40, 15])], source_dimensions=[1000, 600])
        self.assertEqual("conflicting_candidates", result["errors"]["hp"])

    def test_ocr_character_repairs_labels_and_partial_boxes_are_not_inferred(self):
        for text in ("HP 17/91", "l7/91", "17|91", "17/9I", "17", "1.7/91"):
            with self.subTest(text=text):
                result = extract_hud([observation(text, [280, 15, 55, 15])], source_dimensions=[1000, 600])
                self.assertIsNone(result["hp"])
                self.assertEqual("missing_exact_fraction", result["errors"]["hp"])
        result = extract_hud([observation("17/91", [240, 15, 55, 15])], source_dimensions=[1000, 600])
        self.assertEqual("missing_region_text", result["errors"]["hp"])

    def test_zero_denominator_and_impossible_hp_are_unknown(self):
        result = extract_hud([observation("92/91", [280, 15, 55, 15]),
                              observation("3/0", [70, 490, 40, 20])], source_dimensions=[1000, 600])
        self.assertEqual("hp_exceeds_maximum", result["errors"]["hp"])
        self.assertEqual("zero_denominator", result["errors"]["energy"])


class NativeTextReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / "saved.png"
        self.image.write_bytes(png_bytes())
        self.reader = NativeTextReader(Path(sys.executable))
        self.raw = {"schema": "veda.native-text.raw.v1", "ok": True,
                    "image_path": str(self.image.resolve()),
                    "image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                    "source_dimensions": [1000, 600], "orientation": "up",
                    "timing_ms": {"ocr": 12.5, "load_and_ocr": 14.0},
                    "observations": [observation("17/91", [280, 15, 55, 15]),
                                     observation("3/3", [70, 490, 40, 20])]}

    def observe(self, raw=None, **kwargs):
        with patch("veda.native_ocr._run_bounded", return_value=(0, json.dumps(raw or self.raw).encode())) as helper:
            result = self.reader.observe(self.image, frame_id="fixture-frame", **kwargs)
            return result, helper

    def test_fixture_observation_carries_source_identity_boxes_and_separate_timings(self):
        result, helper = self.observe()
        self.assertTrue(result["ok"])
        self.assertEqual("fixture-frame", result["parent_frame_id"])
        self.assertEqual(self.raw["image_sha256"], result["image_sha256"])
        self.assertEqual(result["image_sha256"], result["parent_image_sha256"])
        self.assertEqual([1000, 600], result["source_dimensions"])
        self.assertEqual(17, result["hud"]["hp"])
        self.assertEqual(self.raw["observations"], result["observations"])
        self.assertEqual(12.5, result["timing_ms"]["ocr"])
        self.assertIn("request_round_trip", result["timing_ms"])
        self.assertFalse(result["runtime_authorization_eligible"])
        self.assertEqual(str(self.image.resolve()), helper.call_args.args[0][1])

    def test_missing_prebuilt_helper_does_not_compile_or_start_process(self):
        with patch("veda.native_ocr._run_bounded") as helper:
            result = NativeTextReader(Path(self.temp.name) / "missing").observe(self.image, frame_id="frame")
        self.assertEqual("helper_not_prebuilt_or_executable", result["error"])
        helper.assert_not_called()

    def test_source_path_hash_dimensions_and_orientation_are_checked(self):
        for field, value, expected in (
            ("image_path", "another.png", "helper_image_mismatch"),
            ("image_sha256", "0" * 64, "helper_image_mismatch"),
            ("source_dimensions", [2000, 1200], "helper_dimensions_or_orientation_mismatch"),
            ("orientation", "down", "helper_dimensions_or_orientation_mismatch"),
        ):
            with self.subTest(field=field):
                raw = {**self.raw, field: value}
                result, _ = self.observe(raw)
                self.assertFalse(result["ok"])
                self.assertEqual(expected, result["error"])
                self.assertEqual([], result["observations"])
                self.assertIsNone(result["hud"]["hp"])

    def test_changed_source_after_helper_is_rejected(self):
        def replace_image(*_args):
            self.image.write_bytes(png_bytes(900, 500))
            return 0, json.dumps(self.raw).encode()
        with patch("veda.native_ocr._run_bounded", side_effect=replace_image):
            result = self.reader.observe(self.image, frame_id="frame")
        self.assertEqual("image_changed_during_ocr", result["error"])
        self.assertEqual([], result["observations"])

    def test_invalid_geometry_prevents_helper_start(self):
        for regions in ({"expected_hp": 17}, {"viewport": [-1, 0, 1000, 600]},
                        {"hp": [0, 0, 1.01, 1]}, {"energy": [0, 0, float("nan"), 1]}):
            with self.subTest(regions=regions):
                result, helper = self.observe(regions=regions)
                self.assertFalse(result["ok"])
                helper.assert_not_called()

    def test_invalid_boxes_quads_and_candidates_are_rejected(self):
        mutations = [
            ("box_original_pixels_top_left", [990, 10, 20, 20]),
            ("box_original_pixels_top_left", [10, 10, 0, 20]),
            ("quadrilateral_original_pixels_top_left", [[1, 1]]),
            ("candidates", [{"text": "17/91", "confidence": 2}]),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                raw = copy.deepcopy(self.raw)
                raw["observations"][0][field] = value
                result, _ = self.observe(raw)
                self.assertFalse(result["ok"])
                self.assertEqual([], result["observations"])

    def test_nonfinite_json_and_helper_failure_return_unknown_without_diagnostics(self):
        for code, payload, error in ((0, b'{"ok":NaN}', "invalid_helper_json"),
                                     (1, b'{"secret":"DO_NOT_PRINT"}', "helper_failed"),
                                     (0, b'not-json', "invalid_helper_json")):
            with self.subTest(error=error):
                with patch("veda.native_ocr._run_bounded", return_value=(code, payload)):
                    result = self.reader.observe(self.image, frame_id="frame")
                self.assertEqual(error, result["error"])
                self.assertIsNone(result["hud"]["hp"])
                self.assertNotIn("DO_NOT_PRINT", json.dumps(result))

    def test_timeout_and_output_failures_preserve_unknown_result(self):
        for error in ("helper_timeout", "helper_output_limit", "helper_stderr_limit"):
            with patch("veda.native_ocr._run_bounded", side_effect=_ReaderFailure(error)):
                result = self.reader.observe(self.image, frame_id="frame")
            self.assertEqual(error, result["error"])
            self.assertFalse(result["ok"])
            self.assertIsNone(result["hud"]["energy"])

    def test_non_png_does_not_start_helper(self):
        self.image.write_bytes(b"not an image")
        result, helper = self.observe()
        self.assertEqual("image_not_png", result["error"])
        helper.assert_not_called()


class BoundedFixtureProcessTests(unittest.TestCase):
    """Small Python fixture children exercise pipe limits; never native Vision."""

    def test_successful_fixture_process(self):
        code, output = _run_bounded([sys.executable, "-c", "print('fixture')"], 2, 1024)
        self.assertEqual((0, b"fixture\n"), (code, output))

    def test_excess_stdout_and_stderr_are_bounded(self):
        for stream, expected in (("stdout", "helper_output_limit"), ("stderr", "helper_stderr_limit")):
            with self.subTest(stream=stream):
                command = [sys.executable, "-c", f"import sys; sys.{stream}.write('x' * 20000)"]
                with self.assertRaisesRegex(_ReaderFailure, expected):
                    _run_bounded(command, 2, 1024)

    def test_silent_fixture_process_timeout_is_bounded(self):
        began = time.monotonic()
        with self.assertRaisesRegex(_ReaderFailure, "helper_timeout"):
            _run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], .05, 1024)
        self.assertLess(time.monotonic() - began, 1.5)


class NativeRegionBatchTests(unittest.TestCase):
    setUp = NativeTextReaderTests.setUp

    def batch(self):
        requested = [{"id": "header-0", "box": [250, 5, 400, 50]},
                     {"id": "cost-0", "box": [50, 470, 130, 530]}]
        raw = {key: value for key, value in self.raw.items() if key != "observations"}
        raw.update(schema="veda.native-text-regions.raw.v1", scale=3, regions=[])
        for region, item in zip(requested, [observation("Strike+", [280, 15, 55, 15]),
                                            observation("1", [60, 490, 15, 20])]):
            raw["regions"].append({"id": region["id"], "box_original_pixels_ltrb": region["box"],
                "scale": 3, "preprocessing": "original", "ok": True, "error": None, "observations": [item],
                "image_sha256": raw["image_sha256"], "source_dimensions": raw["source_dimensions"],
                "timing_ms": {"ocr": 3.5, "region_total": 4.0}})
        return requested, raw

    def observe_batch(self, raw=None, regions=None):
        requested, fixture = self.batch()
        with patch("veda.native_ocr._run_bounded", return_value=(0, json.dumps(raw or fixture).encode())) as helper:
            result = self.reader.observe_regions(self.image, frame_id="fixture-frame",
                                                  regions=requested if regions is None else regions)
        return result, helper

    def test_one_process_keeps_region_identity_coordinates_and_raw_alternatives(self):
        requested, raw = self.batch()
        raw["regions"][0]["observations"][0]["candidates"].append({"text": "Strike?", "confidence": .9})
        result, helper = self.observe_batch(raw)
        self.assertTrue(result["ok"])
        self.assertFalse(result["has_region_errors"])
        self.assertEqual("veda.native-text-regions.v1", result["schema"])
        self.assertNotIn("observations", result)
        self.assertEqual(["header-0", "cost-0"], [r["id"] for r in result["regions"]])
        self.assertEqual(raw["regions"][0]["observations"], result["regions"][0]["observations"])
        for region in result["regions"]:
            self.assertEqual(result["image_sha256"], region["parent_image_sha256"])
            self.assertEqual(result["source_dimensions"], region["source_dimensions"])
        self.assertFalse(result["runtime_authorization_eligible"])
        helper.assert_called_once()
        arguments = helper.call_args.args[0]
        self.assertEqual("--regions", arguments[2])
        self.assertEqual([{**r, "preprocessing": "original"} for r in requested], json.loads(arguments[3]))

    def test_explicit_region_failure_keeps_other_region_and_does_not_substitute(self):
        _, raw = self.batch()
        raw["regions"][1].update(ok=False, error="native_ocr_failed", observations=[])
        result, _ = self.observe_batch(raw)
        self.assertTrue(result["ok"])
        self.assertTrue(result["has_region_errors"])
        self.assertTrue(result["regions"][0]["ok"])
        self.assertEqual("native_ocr_failed", result["regions"][1]["error"])
        self.assertEqual([], result["regions"][1]["observations"])

    def test_native_crop_geometry_failure_carries_no_text_and_keeps_independent_regions(self):
        _, raw = self.batch()
        raw["regions"][1].update(ok=False, error="native_ocr_outside_region", observations=[],
                                  rejected_observation_count=1)
        result, _ = self.observe_batch(raw)
        self.assertTrue(result["ok"])
        self.assertTrue(result["has_region_errors"])
        self.assertEqual(raw["regions"][0]["observations"], result["regions"][0]["observations"])
        self.assertEqual([], result["regions"][1]["observations"])
        self.assertEqual("native_ocr_outside_region", result["regions"][1]["error"])

    def test_crop_failure_cannot_smuggle_an_outside_observation(self):
        _, raw = self.batch()
        raw["regions"][1].update(ok=False, error="native_ocr_outside_region")
        result, _ = self.observe_batch(raw)
        self.assertFalse(result["ok"])
        self.assertTrue(all(not region["observations"] for region in result["regions"]))

    def test_native_crop_failure_requires_bounded_rejection_metadata(self):
        for count in (None, True, 0, -1, 10001, "1"):
            with self.subTest(count=count):
                _, raw = self.batch()
                raw["regions"][1].update(ok=False, error="native_ocr_outside_region", observations=[],
                                          rejected_observation_count=count)
                result, _ = self.observe_batch(raw)
                self.assertFalse(result["ok"])

    def test_green_preprocessing_preserves_raw_text_identity_and_coordinates(self):
        requested, raw = self.batch()
        requested[0]["preprocessing"] = "green_text"
        raw["regions"][0]["preprocessing"] = "green_text"
        result, helper = self.observe_batch(raw, regions=requested)
        self.assertTrue(result["ok"])
        self.assertEqual("green_text", result["regions"][0]["preprocessing"])
        self.assertEqual("original", result["regions"][1]["preprocessing"])
        self.assertEqual(raw["regions"][0]["observations"], result["regions"][0]["observations"])
        self.assertEqual(raw["image_sha256"], result["regions"][0]["parent_image_sha256"])
        self.assertEqual("green_text", json.loads(helper.call_args.args[0][3])[0]["preprocessing"])

    def test_preprocessing_must_be_allowed_and_echoed_exactly(self):
        requested, raw = self.batch()
        for mode in (None, "green", "dictionary", 1, []):
            with self.subTest(mode=mode):
                invalid = copy.deepcopy(requested)
                invalid[0]["preprocessing"] = mode
                result, helper = self.observe_batch(regions=invalid)
                self.assertEqual("invalid_region_preprocessing", result["error"])
                helper.assert_not_called()
        requested[0]["preprocessing"] = "green_text"
        result, _ = self.observe_batch(raw, regions=requested)
        self.assertEqual("helper_region_mismatch", result["error"])
        self.assertTrue(all(r["observations"] == [] for r in result["regions"]))
        self.assertEqual("green_text", result["regions"][0]["preprocessing"])
        del raw["regions"][0]["preprocessing"]
        result, _ = self.observe_batch(raw)
        self.assertEqual("helper_region_mismatch", result["error"])

    def test_white_numeric_preprocessing_is_explicit_and_source_bound(self):
        requested, raw = self.batch()
        requested[1]["preprocessing"] = "white_text"
        raw["regions"][1]["preprocessing"] = "white_text"
        result, helper = self.observe_batch(raw, regions=requested)
        self.assertTrue(result["ok"])
        self.assertEqual("white_text", json.loads(helper.call_args.args[0][3])[1]["preprocessing"])
        self.assertEqual(raw["image_sha256"], result["regions"][1]["image_sha256"])
        raw["regions"][1]["preprocessing"] = "original"
        result, _ = self.observe_batch(raw, regions=requested)
        self.assertFalse(result["ok"])
        self.assertEqual("helper_region_mismatch", result["error"])

    def test_empty_regions_does_not_launch_helper(self):
        result, helper = self.observe_batch(regions=[])
        self.assertTrue(result["ok"])
        self.assertEqual([], result["regions"])
        helper.assert_not_called()

    def test_invalid_request_geometry_or_ids_never_starts_helper(self):
        invalid = [None, [{"id": "a", "box": [0, 0, 10, 10]}] * 2,
                   [{"id": "a", "box": [0, 0, 10.5, 10]}],
                   [{"id": "a", "box": [False, 0, 10, 10]}],
                   [{"id": "a", "box": [-1, 0, 10, 10]}],
                   [{"id": "a", "box": [0, 0, 1001, 10]}],
                   [{"id": "a", "box": [0, 0, 10, 10], "expected": "Strike"}],
                   [{"id": str(i), "box": [0, 0, 10, 10]} for i in range(21)]]
        for regions in invalid:
            with self.subTest(regions=regions):
                with patch("veda.native_ocr._run_bounded") as helper:
                    result = self.reader.observe_regions(self.image, frame_id="frame", regions=regions)
                self.assertFalse(result["ok"])
                self.assertEqual([], result["regions"])
                helper.assert_not_called()

    def test_scaled_pixel_total_is_bounded_before_launch(self):
        # Each 1000x600 fixture ROI scales to5.4M pixels; ten exceed48M.
        regions = [{"id": str(i), "box": [0, 0, 1000, 600]} for i in range(10)]
        result, helper = self.observe_batch(regions=regions)
        self.assertEqual("region_pixel_limit", result["error"])
        helper.assert_not_called()

    def test_returned_region_correlation_and_image_identity_are_required(self):
        for field, value, expected in (
            ("id", "another-region", "helper_region_mismatch"),
            ("box_original_pixels_ltrb", [250, 5, 399, 50], "helper_region_mismatch"),
            ("scale", 2, "helper_region_mismatch"),
            ("image_sha256", "0"*64, "helper_region_image_mismatch"),
            ("source_dimensions", [999, 600], "helper_region_image_mismatch")):
            with self.subTest(field=field):
                _, raw = self.batch()
                raw["regions"][0][field] = value
                result, _ = self.observe_batch(raw)
                self.assertFalse(result["ok"])
                self.assertEqual(expected, result["error"])
                self.assertTrue(all(r["observations"] == [] and not r["ok"] for r in result["regions"]))

    def test_unmapped_or_other_region_coordinates_are_rejected(self):
        for box in ([10, 10, 20, 10], [840, 45, 165, 45], [60, 490, 15, 20]):
            with self.subTest(box=box):
                _, raw = self.batch()
                raw["regions"][0]["observations"] = [observation("Strike+", box)]
                result, _ = self.observe_batch(raw)
                self.assertFalse(result["ok"])
                self.assertTrue(all(r["observations"] == [] for r in result["regions"]))

    def test_missing_duplicate_or_reordered_region_results_are_rejected(self):
        for mutation in ("missing", "duplicate", "reordered"):
            with self.subTest(mutation=mutation):
                _, raw = self.batch()
                if mutation == "missing":
                    raw["regions"].pop()
                elif mutation == "duplicate":
                    raw["regions"][1] = copy.deepcopy(raw["regions"][0])
                else:
                    raw["regions"].reverse()
                result, _ = self.observe_batch(raw)
                self.assertEqual("helper_region_mismatch", result["error"])

    def test_failed_region_cannot_carry_successful_observations(self):
        _, raw = self.batch()
        raw["regions"][0].update(ok=False, error="native_ocr_failed")
        result, _ = self.observe_batch(raw)
        self.assertEqual("invalid_helper_region_outcome", result["error"])

    def test_changed_image_clears_all_region_observations(self):
        requested, raw = self.batch()
        def mutate(*_args):
            self.image.write_bytes(png_bytes(900, 500))
            return 0, json.dumps(raw).encode()
        with patch("veda.native_ocr._run_bounded", side_effect=mutate):
            result = self.reader.observe_regions(self.image, frame_id="frame", regions=requested)
        self.assertEqual("image_changed_during_ocr", result["error"])
        self.assertTrue(all(r["observations"] == [] for r in result["regions"]))

    def test_batch_timeout_or_output_limit_has_explicit_error_for_each_region(self):
        requested, _ = self.batch()
        for error in ("helper_timeout", "helper_output_limit"):
            with self.subTest(error=error):
                with patch("veda.native_ocr._run_bounded", side_effect=_ReaderFailure(error)):
                    result = self.reader.observe_regions(self.image, frame_id="frame", regions=requested)
                self.assertFalse(result["ok"])
                self.assertEqual([error, error], [r["error"] for r in result["regions"]])
                self.assertTrue(all(r["observations"] == [] for r in result["regions"]))


if __name__ == "__main__":
    unittest.main()
