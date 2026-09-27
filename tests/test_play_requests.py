"""Synthetic saved PNG/receipt tests. No capture, bridge, model or run DB."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_play_request import main
from tests.test_reviewed_play import FakeController, png_bytes
from veda.execution import ARM_PHRASE
from veda import play_requests as requests
from veda.reviewed_play import ReviewedPlaySession


class ArmRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2026, 9, 27, 12, 30, 0, 654321, tzinfo=timezone.utc)
        self.captured = self.now - timedelta(seconds=4)
        self.image = self.root / ("ps5_observation_" + self.captured.strftime("%Y%m%dT%H%M%S.%fZ")
                                  + "_" + "a" * 32 + ".png")
        self.image.write_bytes(png_bytes((30, 10, 60)))
        self.receipt_path = self.image.with_suffix(".capture.json")
        self.receipt = {"schema": "veda.game-window-capture.v1", "image_path": str(self.image.resolve()),
            "image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(), "dimensions": [8, 8],
            "capture_requested_at": self.captured.isoformat(),
            "capture_completed_at": (self.captured + timedelta(seconds=2)).isoformat(),
            "window": {"id": 123, "title": "Synthetic fixture only"},
            "pixel_content_verified": False, "controller_input_sent": False}
        self.save_receipt()
        self.output = self.root / "arm.json"
        self.kwargs = dict(run_id="explicit-fixture-run", capture=self.image, screen="combat",
            reviewer="Synthetic reviewer", evidence_note="Inspected the fixture; no actual game evidence.",
            phrase=ARM_PHRASE, reviewed=True, exclusive_client_confirmed=True,
            output=self.output, now=self.now)

    def save_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt))

    def build(self, **overrides):
        return requests.write_arm_request(**{**self.kwargs, **overrides})

    def failure(self, code, **overrides):
        with self.assertRaisesRegex(requests.PlayRequestError, "^" + code + "$"):
            self.build(**overrides)
        self.assertFalse(self.output.exists())

    def test_deterministic_request_binds_original_start_bytes_and_declared_review(self):
        result = self.build()
        self.assertEqual({"request_file": str(self.output.resolve())}, result)
        value = json.loads(self.output.read_bytes())
        self.assertEqual("arm", value["operation"])
        self.assertEqual("explicit-fixture-run", value["run_id"])
        self.assertEqual(ARM_PHRASE, value["phrase"])
        self.assertEqual(self.receipt["capture_requested_at"], value["source"]["captured_at"])
        self.assertNotEqual(self.receipt["capture_completed_at"], value["source"]["captured_at"])
        self.assertEqual(self.receipt["image_sha256"], value["source"]["sha256"])
        self.assertEqual(str(self.image.resolve()), value["source"]["path"])
        self.assertEqual({"complete": True, "reviewer": "Synthetic reviewer", "frame_id": value["frame_id"],
                          "image_sha256": value["source"]["sha256"]}, value["review"])
        self.assertNotIn("reading", value)
        self.assertNotIn("context", value)
        self.assertNotIn("window", value)
        other = self.root / "other.json"
        self.build(output=other)
        self.assertEqual(self.output.read_bytes(), other.read_bytes())
        self.assertFalse(json.loads(self.receipt_path.read_bytes())["pixel_content_verified"])

    def test_review_exclusivity_phrase_and_required_text_are_explicit(self):
        for key, value, code in [
            ("reviewed", False, "exact_image_review_required"),
            ("reviewed", 1, "exact_image_review_required"),
            ("exclusive_client_confirmed", False, "exclusive_client_declaration_required"),
            ("exclusive_client_confirmed", 1, "exclusive_client_declaration_required"),
            ("phrase", "yes", "current_run_arming_phrase_required"),
            ("run_id", " ", "run_id_invalid"), ("run_id", "x" * 129, "run_id_invalid"),
            ("reviewer", "", "reviewer_invalid"), ("reviewer", "x" * 129, "reviewer_invalid"),
            ("evidence_note", "\0", "evidence_note_invalid"),
            ("evidence_note", "x" * 4097, "evidence_note_invalid"),
            ("screen", "unknown", "screen_unknown"), ("screen", None, "screen_unknown"),
            ("capture", None, "path_invalid")]:
            with self.subTest(key=key, value=value):
                self.failure(code, **{key: value})

    def test_receipt_is_required_bounded_and_strict_json(self):
        for raw, code in [(b"[]", "capture_schema_invalid"), (b"null", "capture_schema_invalid"),
                          (b"not json", "capture_metadata_invalid"), (b"\xff", "capture_metadata_invalid"),
                          (b'{"schema":1,"schema":2}', "capture_metadata_duplicate_key"),
                          (b'{"x":NaN}', "capture_metadata_invalid"),
                          (b" " * (requests.MAX_RECEIPT_BYTES + 1), "capture_metadata_too_large")]:
            with self.subTest(code=code):
                self.receipt_path.write_bytes(raw)
                self.failure(code)
        self.receipt_path.unlink()
        self.failure("capture_metadata_unreadable")

    def test_metadata_identity_mismatches_do_not_publish(self):
        original = deepcopy(self.receipt)
        for key, value, code in [
            ("schema", "other", "capture_schema_invalid"),
            ("image_path", str(self.root / "different.png"), "capture_path_mismatch"),
            ("image_sha256", "0" * 64, "capture_hash_mismatch"),
            ("dimensions", [8, 9], "capture_dimensions_mismatch"),
            ("dimensions", [8.0, 8.0], "capture_dimensions_mismatch"),
            ("dimensions", [True, 8], "capture_dimensions_mismatch"),
            ("dimensions", None, "capture_dimensions_mismatch")]:
            with self.subTest(key=key, value=value):
                self.receipt = {**original, key: value}
                self.save_receipt()
                self.failure(code)

    def test_changed_image_and_invalid_png_do_not_publish(self):
        self.image.write_bytes(png_bytes((31, 10, 60)))
        self.failure("capture_hash_mismatch")
        self.image.write_bytes(b"not a PNG")
        self.failure("capture_image_invalid")
        self.image.unlink()
        self.failure("capture_not_regular")

    def test_timestamp_cannot_be_replaced_by_completion_or_now(self):
        original = deepcopy(self.receipt)
        for value in (None, "", "2026-09-27T12:29:56", "not a time"):
            self.receipt = {**original, "capture_requested_at": value}
            self.save_receipt()
            self.failure("capture_requested_at_invalid")
        self.receipt = {**original, "capture_requested_at": original["capture_completed_at"]}
        self.save_receipt()
        self.failure("capture_filename_time_mismatch")

    def test_age_uses_start_with_exact_boundary_and_future_rejected(self):
        self.failure("capture_stale", now=self.captured + timedelta(seconds=requests.MAX_AGE, microseconds=1))
        self.failure("capture_future_dated", now=self.captured - timedelta(microseconds=1))
        self.build(now=self.captured + timedelta(seconds=requests.MAX_AGE))
        self.assertTrue(self.output.exists())

    def test_new_completion_timestamp_does_not_refresh_stale_capture(self):
        late = self.captured + timedelta(seconds=requests.MAX_AGE + 1)
        self.receipt["capture_completed_at"] = late.isoformat()
        self.save_receipt()
        self.failure("capture_stale", now=late)

    def test_completion_order_timezone_and_filename_validation(self):
        original = deepcopy(self.receipt)
        for value, code in [("no time", "capture_completed_at_invalid"),
                            ((self.captured - timedelta(seconds=1)).isoformat(), "capture_time_order_invalid"),
                            ((self.now + timedelta(seconds=1)).isoformat(), "capture_time_order_invalid")]:
            self.receipt = {**original, "capture_completed_at": value}
            self.save_receipt()
            self.failure(code)
        self.receipt = original
        self.receipt["capture_requested_at"] = self.captured.astimezone(timezone(timedelta(hours=-7))).isoformat()
        self.save_receipt()
        self.build()
        self.assertEqual(self.receipt["capture_requested_at"], json.loads(self.output.read_text())["source"]["captured_at"])

    def test_legacy_second_only_capture_name_is_compatible(self):
        observed = self.captured.replace(microsecond=0)
        legacy = self.root / ("ps5_observation_" + observed.strftime("%Y%m%dT%H%M%SZ") + ".png")
        legacy.write_bytes(self.image.read_bytes())
        receipt = {**self.receipt, "image_path": str(legacy), "capture_requested_at": observed.isoformat()}
        legacy.with_suffix(".capture.json").write_text(json.dumps(receipt))
        self.build(capture=legacy)

    def test_renamed_or_timestamp_mismatched_capture_is_rejected(self):
        for name in ("arbitrary.png", "ps5_observation_20269999T126000Z.png", "ps5_observation_20260927T123000Z.png"):
            other = self.root / name
            other.write_bytes(self.image.read_bytes())
            receipt = {**self.receipt, "image_path": str(other)}
            other.with_suffix(".capture.json").write_text(json.dumps(receipt))
            with self.subTest(name=name):
                self.failure("capture_filename_time_mismatch" if name.endswith("123000Z.png") else "capture_filename_invalid", capture=other)

    def test_exclusive_output_preserves_image_receipt_existing_file_and_symlinks(self):
        for source in (self.image, self.receipt_path):
            before = source.read_bytes()
            self.failure("output_is_capture_source", output=source)
            self.assertEqual(before, source.read_bytes())
        self.output.write_text("unrelated existing file")
        with self.assertRaisesRegex(requests.PlayRequestError, "output_already_exists"):
            self.build()
        self.assertEqual("unrelated existing file", self.output.read_text())
        self.output.unlink()
        target = self.root / "not-created.json"
        self.output.symlink_to(target)
        with self.assertRaisesRegex(requests.PlayRequestError, "output_already_exists"):
            self.build()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_receipt_or_image_mutation_during_packaging_aborts(self):
        original = requests._source_identity
        for mutation in (lambda: self.receipt_path.write_text("{}"),
                         lambda: self.image.write_bytes(png_bytes((1, 2, 3)))):
            self.image.write_bytes(png_bytes((30, 10, 60)))
            self.save_receipt()
            calls = []
            def changed(path):
                calls.append(path)
                if len(calls) == 2:
                    mutation()
                return original(path)
            with patch.object(requests, "_source_identity", side_effect=changed):
                self.failure("capture_changed_during_packaging")

    def test_failed_write_removes_partial_new_request(self):
        with patch.object(requests.os, "fsync", side_effect=OSError("synthetic disk failure")):
            self.failure("output_write_failed")

    def test_packaging_calls_no_capture_controller_database_or_subprocess(self):
        with patch("veda.game_capture.capture_game_window", side_effect=AssertionError("capture called")), \
             patch("veda.reviewed_play.ReviewedPlaySession", side_effect=AssertionError("session called")), \
             patch("veda.bridge_client.BridgeClient", side_effect=AssertionError("bridge called")), \
             patch("veda.telemetry_database.TelemetryDatabase", side_effect=AssertionError("database called")), \
             patch("subprocess.run", side_effect=AssertionError("subprocess called")):
            self.build()

    def test_result_is_accepted_by_real_adapter_with_fake_telemetry_and_controller(self):
        class Telemetry:
            def recover(self, *, run_id):
                return {"pending": []}
        self.build()
        controller = FakeController()
        with ReviewedPlaySession(self.root / "fake-session", run_id=self.kwargs["run_id"],
                telemetry=Telemetry(), controller_factory=lambda: controller,
                mode="codex", clock=lambda: self.now) as session:
            result = session.handle(json.loads(self.output.read_bytes()))
            self.assertEqual("armed_codex_reviewed", result["status"])
            self.assertEqual(["status"], [row["action"] for row in controller.calls])
            self.assertEqual([], controller.inputs)
            self.assertFalse(result["runtime_authorized"])

    def test_capture_expiring_during_packaging_is_not_written(self):
        times = iter([self.now, self.captured + timedelta(seconds=requests.MAX_AGE + 1)])
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(next(times).timestamp(), tz)
        with patch.object(requests, "datetime", Clock):
            self.failure("capture_stale", now=None)

    def cli_args(self):
        return ["arm", "--run-id", self.kwargs["run_id"], "--capture", str(self.image),
                "--screen", "combat", "--reviewer", "Synthetic reviewer", "--evidence-note", "Reviewed fixture only.",
                "--phrase", ARM_PHRASE, "--reviewed", "--exclusive-client-confirmed", "--output", str(self.output)]

    def test_cli_prints_only_pointer_and_never_dispatches(self):
        output = io.StringIO()
        current = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(current.timestamp(), tz)
        with patch.object(requests, "datetime", Clock), redirect_stdout(output):
            self.assertEqual(0, main(self.cli_args()))
        self.assertEqual({"request_file": str(self.output)}, json.loads(output.getvalue()))

    def test_cli_requires_declarations_and_reports_fixed_validation_errors(self):
        for flag in ("--reviewed", "--exclusive-client-confirmed"):
            args = self.cli_args()
            args.remove(flag)
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as caught, patch("sys.stderr", new=io.StringIO()):
                main(args)
            self.assertEqual(2, caught.exception.code)
            self.assertFalse(self.output.exists())
        self.receipt["image_sha256"] = "PRIVATE UNTRUSTED CONTENT"
        self.save_receipt()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(2, main(self.cli_args()))
        self.assertEqual({"request_file": None, "error": "capture_hash_mismatch", "controller_input_sent": False}, json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
