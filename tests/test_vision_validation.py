"""Synthetic scoring-contract tests; none constitute saved-game calibration."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from veda.execution import RUNTIME_FIELDS
from veda.pipeline_trace import TraceRecorder
from veda.vision import StructuredGameState
from veda.vision_validation import (
    SavedFrame, ValidationManifest, evaluate_predictions, load_manifest,
    load_predictions, observe_saved_frames,
)


def frame(index, expected, digest=None):
    return SavedFrame(f"test-{index}", Path(f"synthetic-{index}"), digest or f"{index:064x}", expected)


def manifest(frames, independent=True):
    return ValidationManifest(tuple(frames), "Synthetic scoring test, not production calibration", independent)


def prediction(f, values, **extra):
    return {"frame_id": f.frame_id, "sha256": f.sha256, "prediction": values, **extra}


def full_labels():
    context = {
        "fresh": True, "unknowns": [], "encounter_type": "enemy",
        "inventory": {"coverage": {"relic": "complete", "potion": "complete"},
                      "current": {"relic": [], "potion": []}},
        "state": {"hp": 40, "max_hp": 80, "energy": 2, "block": 0,
                  "strength": 0, "dexterity": 0, "weak": 0, "vulnerable": 0, "frail": 0,
                  "no_block": 0, "hand": [], "enemies": [], "powers": {}, "counters": {},
                  "piles": {"draw": [], "discard": [], "exhaust": [], "draw_order": []},
                  "hand_complete": True, "powers_complete": True,
                  "end_turn_damage": 0, "unmodeled_effects": []},
    }
    return {"screen_type": "COMBAT", "hp": 40, "max_hp": 80, "energy": 2, "block": 0,
            "player_strength": 0, "player_weak": 0, "player_vulnerable": 0, "player_frail": 0,
            "hand": [], "hand_complete": True, "hand_details": [], "enemies": [], "end_turn_damage": 0,
            "advisory_context": context, "ui_phase": "hand", "focused_card_id": None,
            "selected_card_id": None, "focused_target_id": None, "hand_order": [], "target_order": []}


class VisionValidationTests(unittest.TestCase):
    def test_sparse_twelve_frames_do_not_validate_each_field(self):
        frames = [frame(i, {"screen_type": "COMBAT", "hp": 40} if i < 6 else
                        {"screen_type": "COMBAT", "energy": 2}) for i in range(12)]
        result = evaluate_predictions(manifest(frames), [prediction(f, f.expected.copy()) for f in frames])
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual(result["distinct_image_hashes"], 12)
        self.assertEqual(result["fields"]["hp"]["runtime_labeled_unique_images"], 6)
        self.assertEqual(result["fields"]["energy"]["runtime_labeled_unique_images"], 6)
        self.assertEqual(result["fields"]["advisory_context"]["labeled_unique_images"], 0)

    def test_every_runtime_field_requires_twelve_distinct_images(self):
        labels = full_labels()
        self.assertTrue(RUNTIME_FIELDS <= labels.keys(), "test fixture must track the runtime contract")
        frames = [frame(i, copy.deepcopy(labels)) for i in range(12)]
        predictions = [prediction(f, copy.deepcopy(f.expected)) for f in frames]
        result = evaluate_predictions(manifest(frames), predictions)
        self.assertTrue(result["recognition_fields_validated"], result["authorization_blockers"])
        self.assertFalse(result["runtime_authorized"], "scores cannot manufacture runtime reader identity")
        predictions[-1]["prediction"].pop("focused_target_id")
        incomplete = evaluate_predictions(manifest(frames), predictions)
        self.assertFalse(incomplete["runtime_authorized"])
        self.assertEqual(incomplete["fields"]["focused_target_id"]["unknown_predictions"], 1)
        self.assertEqual(incomplete["fields"]["focused_target_id"]["runtime_accuracy"], 11 / 12)

    def test_duplicate_hashes_do_not_inflate_denominator(self):
        frames = [frame(i, full_labels(), digest="a" * 64) for i in range(12)]
        result = evaluate_predictions(manifest(frames), [prediction(f, f.expected.copy()) for f in frames])
        self.assertEqual(result["fields"]["hp"]["labeled_unique_images"], 1)
        self.assertFalse(result["runtime_authorized"])

    def test_duplicate_prediction_errors_are_not_hidden_by_good_copy(self):
        frames = [frame(0, {"screen_type": "COMBAT", "hp": 40}), frame(1, {"hp": 40}, digest=f"{0:064x}")]
        result = evaluate_predictions(manifest(frames), [prediction(frames[0], frames[0].expected),
            prediction(frames[1], {"hp": 20}, flagged_fields=["hp"])])
        self.assertEqual(result["fields"]["hp"]["errors"], 1)
        self.assertEqual(result["fields"]["hp"]["runtime_accuracy"], 0)

    def test_null_numeric_labels_unknowns_and_verified_ui_absence(self):
        f = frame(0, {"screen_type": "COMBAT", "hp": None, "energy": 2, "block": 0, "selected_card_id": None})
        result = evaluate_predictions(manifest([f]), [prediction(f, {"screen_type": "COMBAT", "energy": None,
                                                                   "block": 0, "selected_card_id": None})])
        self.assertEqual(result["fields"]["hp"]["labeled_unique_images"], 0)
        self.assertEqual(result["fields"]["hp"]["unknown_label_rows"], 1)
        self.assertEqual(result["fields"]["energy"]["unknown_predictions"], 1)
        self.assertEqual(result["fields"]["block"]["correct"], 1)
        self.assertEqual(result["fields"]["selected_card_id"]["correct"], 1)

    def test_partial_context_enemy_and_hand_labels_cannot_authorize(self):
        f = frame(0, {"screen_type": "COMBAT", "enemies": [{"hp": 10}],
                      "hand_details": [{"name": "Strike"}], "advisory_context": {"state": {"hp": 40}}})
        result = evaluate_predictions(manifest([f]), [prediction(f, f.expected)])
        for field in ("enemies", "hand_details", "advisory_context"):
            self.assertEqual(result["fields"][field]["correct"], 1)
            self.assertEqual(result["fields"][field]["runtime_labeled_unique_images"], 0)
            self.assertEqual(result["fields"][field]["incomplete_label_rows"], 1)

    def test_enemy_confidence_is_not_a_human_label_but_all_facts_must_match(self):
        enemy = {"name": "Cultist", "hp": 40, "max_hp": 50, "block": 0,
                 "intent": "attack", "intent_hits": [6], "intent_total_damage": 6}
        f = frame(0, {"screen_type": "COMBAT", "enemies": [enemy]})
        row = prediction(f, {"screen_type": "COMBAT", "enemies": [{**enemy, "intent_damage_confidence": .97}]})
        result = evaluate_predictions(manifest([f]), [row])
        self.assertEqual(result["fields"]["enemies"]["correct"], 1)
        self.assertEqual(result["fields"]["enemies"]["runtime_labeled_unique_images"], 1)
        row["prediction"]["enemies"][0]["block"] = 1
        self.assertEqual(evaluate_predictions(manifest([f]), [row])["fields"]["enemies"]["errors"], 1)

    def test_noncombat_examples_cannot_certify_combat_runtime(self):
        frames = [frame(i, {**full_labels(), "screen_type": "TITLE"}) for i in range(12)]
        result = evaluate_predictions(manifest(frames), [prediction(f, f.expected) for f in frames])
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual(result["fields"]["hp"]["accuracy"], 1)
        self.assertEqual(result["fields"]["hp"]["runtime_labeled_unique_images"], 0)

    def test_unflagged_critical_error_blocks_even_at_accuracy_threshold(self):
        frames = [frame(i, full_labels()) for i in range(20)]
        predictions = [prediction(f, copy.deepcopy(f.expected)) for f in frames]
        predictions[0]["prediction"]["hp"] = 41
        result = evaluate_predictions(manifest(frames), predictions)
        self.assertEqual(result["fields"]["hp"]["accuracy"], .95)
        self.assertEqual(result["unflagged_critical_errors"], 1)
        self.assertFalse(result["runtime_authorized"])
        predictions[0]["flagged_fields"] = ["hp"]
        flagged = evaluate_predictions(manifest(frames), predictions)
        self.assertEqual(flagged["unflagged_critical_errors"], 0)
        self.assertEqual(flagged["fields"]["hp"]["errors"], 1)

    def test_missing_predictions_and_provider_errors_are_unknown(self):
        frames = [frame(i, {"screen_type": "COMBAT", "hp": 40}) for i in range(2)]
        result = evaluate_predictions(manifest(frames), [prediction(frames[0], {"hp": 40}, error="TimeoutExpired")])
        self.assertEqual(result["missing_prediction_rows"], 1)
        self.assertEqual(result["fields"]["hp"]["unknown_predictions"], 2)
        self.assertEqual(result["fields"]["hp"]["prediction_coverage"], 0)
        self.assertEqual(result["prediction_errors"], {"TimeoutExpired": 1})

    def test_timing_only_counts_measured_request_samples(self):
        frames = [frame(i, {"hp": 40}) for i in range(3)]
        rows = [prediction(frames[0], {}, model_request_round_trip_ns=100),
                prediction(frames[1], {}, model_request_round_trip_ns=300), prediction(frames[2], {})]
        result = evaluate_predictions(manifest(frames), rows)
        latency = result["latency"]
        self.assertEqual((latency["label"], latency["samples"], latency["missing_samples"]),
                         ("model_request_round_trip", 2, 1))
        self.assertEqual((latency["p50_ns"], latency["p95_ns"]), (200, 290))
        empty = evaluate_predictions(manifest([]), [])
        self.assertIsNone(empty["latency"]["p50_ns"])
        self.assertFalse(empty["runtime_authorized"])

    def test_wrong_hash_duplicate_id_and_invalid_timing_are_rejected(self):
        f = frame(0, {"hp": 40})
        for rows in ([prediction(f, {}, sha256="wrong")], [prediction(f, {}), prediction(f, {})],
                     [prediction(f, {}, model_request_round_trip_ns=True)],
                     [prediction(f, {}, unknown_fields="hp")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                evaluate_predictions(manifest([f]), rows)

    def test_provenance_is_required_even_for_complete_synthetic_test_data(self):
        frames = [frame(i, full_labels()) for i in range(12)]
        result = evaluate_predictions(manifest(frames, independent=False), [prediction(f, f.expected) for f in frames])
        self.assertFalse(result["runtime_authorized"])
        self.assertIn("independent label provenance has not been declared", result["authorization_blockers"])

    def test_loader_hash_binding_relative_paths_conflicts_and_predictions(self):
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            image = directory / "saved-frame.dat"
            image.write_bytes(b"synthetic file for hash-binding contract test only")
            row = {"frame_id": "one", "image": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                   "expected": {"hp": 40}}
            path = directory / "manifest.json"
            path.write_text(json.dumps({"label_source": "unit test", "independent_labels": True, "frames": [row]}))
            loaded = load_manifest(path)
            self.assertEqual(loaded.frames[0].image, image.resolve())
            predictions_path = directory / "predictions.json"
            predictions_path.write_text(json.dumps({"predictions": [prediction(loaded.frames[0], {"hp": 40})]}))
            self.assertEqual(len(load_predictions(predictions_path)), 1)
            conflicting = {**row, "frame_id": "two", "expected": {"hp": 41}}
            path.write_text(json.dumps({"frames": [row, conflicting]}))
            with self.assertRaisesRegex(ValueError, "conflicting labels"):
                load_manifest(path)
            path.write_text(json.dumps({"frames": [row]}))
            image.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_manifest(path)

    def test_explicit_saved_observation_receives_no_labels_and_times_only_request(self):
        with TemporaryDirectory() as directory:
            image = Path(directory) / "test-image.dat"
            image.write_bytes(b"synthetic only")
            f = SavedFrame("one", image, hashlib.sha256(image.read_bytes()).hexdigest(), {"hp": 99})
            now = [0]
            trace = TraceRecorder(clock=lambda: now[0])

            class FakeProvider:
                last_error = None
                last_latency_ms = 999999  # Must never be mislabeled as request time.

                def observe(self, path, *, frame_id):
                    self.args = path, frame_id
                    with trace.span("image_preparation"):
                        now[0] += 100
                    with trace.span("model_request_round_trip"):
                        now[0] += 7
                    with trace.span("parsing"):
                        now[0] += 20
                    return StructuredGameState("COMBAT", 1.0, hp=40)

            provider = FakeProvider()
            with patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                 patch("socket.socket", side_effect=AssertionError("no network")):
                rows = observe_saved_frames(manifest([f]), provider, trace)
            self.assertEqual(provider.args, (image, "one"))
            self.assertEqual(rows[0]["prediction"]["hp"], 40, "labels must never fill or correct predictions")
            self.assertEqual(rows[0]["model_request_round_trip_ns"], 7)
            self.assertNotIn("advisory_context", rows[0]["prediction"])
            trace.close()

    def test_saved_image_change_prevents_any_provider_call(self):
        with TemporaryDirectory() as directory:
            image = Path(directory) / "changed.dat"
            image.write_bytes(b"changed")
            f = SavedFrame("one", image, "wrong", {"hp": 99})
            with self.assertRaisesRegex(ValueError, "changed after"):
                observe_saved_frames(manifest([f]), object(), TraceRecorder())

    def test_cli_offline_never_calls_a_provider_and_outputs_denial(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_runtime_vision.py"
        spec = importlib.util.spec_from_file_location("validate_runtime_vision", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            image = directory / "synthetic.dat"
            image.write_bytes(b"offline cli unit test")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            path, predictions_path, output = directory / "labels.json", directory / "predictions.json", directory / "report.json"
            path.write_text(json.dumps({"frames": [{"frame_id": "one", "image": image.name,
                "sha256": digest, "expected": {"screen_type": "COMBAT", "hp": 40}}]}))
            predictions_path.write_text(json.dumps({"predictions": [{"frame_id": "one", "sha256": digest,
                "prediction": {"screen_type": "COMBAT", "hp": 40}}]}))
            with patch("veda.vision.LocalOllamaVisionProvider", side_effect=AssertionError("offline must not instantiate provider")):
                self.assertEqual(module.main(["--manifest", str(path), "--predictions", str(predictions_path),
                                              "--output", str(output)]), 0)
            result = json.loads(output.read_text())
            self.assertFalse(result["runtime_authorized"])
            self.assertEqual(result["fields"]["hp"]["correct"], 1)
            self.assertEqual(result["prediction_source"]["mode"], "offline_predictions")


if __name__ == "__main__":
    unittest.main()
