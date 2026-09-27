from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from veda.helper_timing import record_helper_failure
from veda.play_timing import PlayTiming


class HelperTimingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.run = str(uuid4())
        self.session = self.root / "session"
        self.clock = PlayTiming(self.session / "timing.json", run_id=self.run,
                                clock=lambda: datetime.now(timezone.utc) - timedelta(seconds=10),
                                monotonic_clock=lambda: 0, clock_id="fixture")
        self.clock.event("begin_move", move_id="move", kind="combat_card")

    def record(self, error=ValueError("capture_stale"), **kwargs):
        return record_helper_failure(error, self.run, session_path=self.session / "state.json", **kwargs)

    def test_stale_capture_accounting_is_automatic_and_exact_capture_deduplicated(self):
        first = self.record(capture=self.root / "frame-1.png")
        duplicate = self.record(capture=self.root / "frame-1.png")
        second = self.record(capture=self.root / "frame-2.png")
        self.assertTrue(first["recorded"])
        self.assertEqual(first["scopes"]["move"]["stale_recapture_count"], 1)
        self.assertEqual(duplicate["scopes"]["move"]["stale_recapture_count"], 1)
        self.assertEqual(second["scopes"]["move"]["stale_recapture_count"], 2)
        self.assertTrue(second["scopes"]["move"]["stale_budget_exhausted"])
        self.assertIn("next_step", second)
        saved = self.clock.snapshot()
        self.assertEqual(saved["move"]["id"], "move")
        self.assertEqual(saved["phase"], "recovery")
        self.assertGreaterEqual(saved["move"]["active_seconds"], 9)
        self.assertIsNone(saved["pause"])
        self.assertFalse(second["controller_input_sent"])

    def test_nonstale_schema_failure_counts_active_recovery_without_stale_increment(self):
        result = self.record(ValueError("compact draft field missing"), capture=self.root / "frame.png")
        self.assertTrue(result["recorded"])
        self.assertEqual(result["scopes"]["move"]["stale_recapture_count"], 0)
        saved = self.clock.snapshot()
        self.assertEqual(saved["events"][-2]["operation"], "failure")
        self.assertEqual(saved["events"][-2]["code"], "ValueError")
        self.assertEqual(saved["events"][-1], {"operation": "phase", "name": "recovery", "at": saved["updated_at"]})

    def test_output_directory_finds_existing_clock_without_explicit_session(self):
        result = record_helper_failure("stale capture", self.run, output_path=self.session / "new-packet.json",
                                       capture=self.root / "frame.png")
        self.assertTrue(result["recorded"])
        self.assertFalse((self.session / "new-packet.json").exists())

    def test_fallback_only_uses_safe_uuid_and_never_creates_unknown_run_files(self):
        with patch("veda.helper_timing.PROJECT_ROOT", self.root):
            existing = self.root / "artifacts" / "reviewed-play" / self.run
            fallback_clock = PlayTiming(existing / "timing.json", run_id=self.run)
            result = record_helper_failure(ValueError("capture_stale"), self.run, capture=self.root / "frame.png")
            self.assertTrue(result["recorded"])
            self.assertEqual(fallback_clock.snapshot()["startup"]["stale_recapture_count"], 1)
            before = {str(path.relative_to(self.root)) for path in self.root.rglob("*")}
            for bad in (None, "../../outside", "../" + self.run, "unknown", str(uuid4())):
                result = record_helper_failure(ValueError("capture_stale"), bad, capture=self.root / "frame.png")
                self.assertFalse(result["recorded"])
            after = {str(path.relative_to(self.root)) for path in self.root.rglob("*")}
            self.assertEqual(before, after)

    def test_explicit_different_run_clock_is_not_written_or_skipped_to_another_candidate(self):
        before = (self.session / "timing.json").read_bytes()
        result = record_helper_failure(ValueError("capture_stale"), str(uuid4()),
                                       session_path=self.session / "state.json", capture=self.root / "frame.png")
        self.assertFalse(result["recorded"])
        self.assertEqual(result["measurement_error"], "existing_play_clock_run_mismatch")
        self.assertEqual((self.session / "timing.json").read_bytes(), before)

    def test_missing_lock_does_not_get_created_and_failure_is_returned(self):
        (self.session / "timing.json.lock").unlink()
        before = (self.session / "timing.json").read_bytes()
        result = self.record(capture=self.root / "frame.png")
        self.assertFalse(result["recorded"])
        self.assertIn("measurement_error", result)
        self.assertFalse((self.session / "timing.json.lock").exists())
        self.assertEqual((self.session / "timing.json").read_bytes(), before)

    def test_malformed_existing_clock_and_write_error_never_escape_original_cli_failure(self):
        path = self.session / "timing.json"
        original = path.read_text()
        path.write_text('{"schema":"veda.play-timing.v1","run_id":"' + self.run + '"}')
        result = self.record(capture=self.root / "frame.png")
        self.assertFalse(result["recorded"])
        self.assertIn("measurement_error", result)
        path.write_text(original)
        with patch("veda.helper_timing.os.replace", side_effect=OSError("synthetic timing write failure")):
            result = self.record(capture=self.root / "frame.png")
        self.assertEqual(result["measurement_error"], "helper_timing_io_error")
        self.assertEqual(path.read_text(), original)
        self.assertEqual(list(self.session.glob(".helper-timing-*")), [])

    def test_absent_capture_is_not_counted_as_an_invented_stale_identity(self):
        result = self.record()
        self.assertTrue(result["recorded"])
        self.assertEqual(result["stale_capture_accounting"], "unavailable_without_exact_capture_path")
        self.assertEqual(result["scopes"]["move"]["stale_recapture_count"], 0)

    def test_result_helper_derives_identity_only_from_explicit_existing_session_state(self):
        path = self.session / "state.json"
        path.write_text(json.dumps({"schema": "veda.reviewed-play.v1", "run_id": self.run, "pending": None}))
        result = record_helper_failure(ValueError("capture_stale"), None, session_path=path,
                                       capture=self.root / "frame.png")
        self.assertTrue(result["recorded"])
        self.assertEqual(result["run_id"], self.run)
        self.assertEqual(result["scopes"]["move"]["stale_recapture_count"], 1)
        path.write_text(json.dumps({"schema": "unrelated", "run_id": self.run}))
        bad = record_helper_failure(ValueError("capture_stale"), None, session_path=path,
                                    capture=self.root / "frame-2.png")
        self.assertFalse(bad["recorded"])
        self.assertEqual(bad["measurement_error"], "reviewed_session_identity_unavailable")
        self.assertEqual(self.clock.snapshot()["move"]["stale_recapture_count"], 1)

    def test_missing_run_without_explicit_session_never_borrows_output_timer_identity(self):
        before = (self.session / "timing.json").read_bytes()
        result = record_helper_failure(ValueError("capture_stale"), None,
                                       output_path=self.session / "packet.json", capture=self.root / "frame.png")
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "run_identity_unavailable")
        self.assertEqual((self.session / "timing.json").read_bytes(), before)

    def test_explicit_pause_is_not_cleared_or_reclassified_by_a_helper_error(self):
        self.clock.event("pause", category="user_wait", reason="User requested pause")
        result = self.record(capture=self.root / "frame.png")
        self.assertTrue(result["recorded"])
        saved = self.clock.snapshot()
        self.assertEqual(saved["pause"]["category"], "user_wait")
        self.assertEqual(saved["move"]["active_seconds"], 0)
        self.assertGreaterEqual(saved["move"]["excluded_seconds"], 9)

    def test_concurrent_helpers_preserve_unique_captures_and_never_lose_failure_records(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda index: self.record(capture=self.root / f"frame-{index}.png"), range(12)))
        self.assertTrue(all(result["recorded"] for result in results))
        state = self.clock.snapshot()
        self.assertEqual(state["move"]["stale_recapture_count"], 12)
        self.assertEqual(sum(event["operation"] == "failure" for event in state["events"]), 12)

    def test_combat_cli_stale_capture_counts_automatically_and_still_emits_no_request(self):
        from scripts.veda_combat import main
        from tests.test_combat_requests import draft
        from tests.test_menu_requests import make_capture
        value = draft({"run_id": self.run, "floor_id": "synthetic-floor", "combat_id": "synthetic-combat", "turn_id": "synthetic-turn"})
        draft_path = self.root / "draft.json"
        draft_path.write_text(json.dumps(value))
        capture = make_capture(self.root, datetime.now(timezone.utc) - timedelta(seconds=120))
        packet = self.session / "request.json"
        argv = ["--draft", str(draft_path), "--capture", str(capture), "--reviewer", "Fixture",
                "--evidence-note", "Synthetic stale original image", "--reviewed", "--output", str(packet)]
        for _ in range(2):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = main(argv)
            response = json.loads(stdout.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(response["status"], "needs_review")
            self.assertEqual(response["reason"], "capture_stale")
            self.assertFalse(response["controller_input_sent"])
            self.assertTrue(response["timing"]["recorded"])
            self.assertEqual(response["timing"]["scopes"]["move"]["stale_recapture_count"], 1)
            self.assertFalse(packet.exists())

    def test_wrong_shape_cli_result_preserves_original_error_and_uses_explicit_session_identity(self):
        from scripts.veda_combat import main
        result = self.root / "bad-result.json"
        result.write_text("[]")
        state = self.session / "state.json"
        state.write_text(json.dumps({"schema": "veda.reviewed-play.v1", "run_id": self.run, "pending": None}))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = main(["--result", str(result), "--session", str(state), "--validate"])
        response = json.loads(stdout.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(response["status"], "needs_review")
        self.assertIn("get", response["reason"])
        self.assertTrue(response["timing"]["recorded"])
        self.assertEqual(response["timing"]["scopes"]["move"]["stale_recapture_count"], 0)
        self.assertFalse(response["controller_input_sent"])


if __name__ == "__main__":
    unittest.main()
