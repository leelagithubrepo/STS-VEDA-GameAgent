from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from veda.play_timing import (MAX_HISTORY, PHASES, STALE_RECAPTURE_LIMIT, TARGET_SECONDS,
                              WATCHDOG_SECONDS, PlayTiming, begin_session, decision_watchdog_profile,
                              record_event, summarize_timing)


BASE = datetime(2026, 9, 27, 20, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def event(state, seconds, operation, **fields):
    return record_event(state, {"operation": operation, **fields}, at=at(seconds))


class Clock:
    def __init__(self):
        self.seconds = 0

    def wall(self):
        return at(self.seconds)

    def monotonic(self):
        return int(self.seconds * 1_000_000_000)


class PlayTimingTests(unittest.TestCase):
    def state(self, **kwargs):
        return begin_session("run-1", "session-1", at=at(0), **kwargs)

    def test_startup_includes_preflight_planning_and_arming_time_until_verified_input(self):
        state = event(self.state(), 5, "phase", name="preflight")
        state = event(state, 75, "phase", name="planning")
        before = summarize_timing(state, at=at(100))
        self.assertEqual(before["startup"]["active_seconds"], 100)
        self.assertTrue(before["startup"]["over_target"])
        state = event(state, 125, "verified_input", action_id="focus-1", step_kind="focus")
        after = summarize_timing(state, at=at(200))
        self.assertEqual(after["startup"]["active_seconds"], 125)
        self.assertEqual(after["startup"]["phase_active_seconds"], {"startup": 5, "preflight": 70, "planning": 50})
        self.assertEqual(after["completed_move_count"], 0)

    def test_decision_watchdog_uses_inspected_request_class_and_never_authorizes_input(self):
        self.assertEqual(decision_watchdog_profile({'kind': 'choice',
            'choice': {'kind': 'reward'}})['seconds'], WATCHDOG_SECONDS['routine'])
        self.assertEqual(decision_watchdog_profile({'kind': 'combat',
            'context': {'encounter_type': 'boss'}})['seconds'], WATCHDOG_SECONDS['boss'])
        self.assertEqual(decision_watchdog_profile({'kind': 'unknown'})['class'], 'new_noncombat')

        state = event(self.state(), 0, 'begin_move', move_id='reward', kind='reviewed_decision',
                      watchdog_seconds=WATCHDOG_SECONDS['routine'], watchdog_class='routine',
                      watchdog_fallback='use_current_screen_helper')
        result = summarize_timing(state, at=at(WATCHDOG_SECONDS['routine']))
        self.assertTrue(result['move']['watchdog_due'])
        self.assertEqual(result['move']['watchdog_fallback'], 'use_current_screen_helper')
        self.assertTrue(any(item['code'] == 'watchdog_due' for item in result['diagnostics']))
        self.assertFalse(result['automatic_input'])
        self.assertFalse(result['controller_authorized'])

    def test_watchdog_alert_is_emitted_once_by_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timing.json'
            clock = Clock()
            tracker = PlayTiming(path, run_id='run-1', clock=clock.wall,
                                 monotonic_clock=clock.monotonic, clock_id='test')
            tracker.event('begin_move', move_id='reward', kind='reviewed_decision',
                          watchdog_seconds=WATCHDOG_SECONDS['routine'], watchdog_class='routine',
                          watchdog_fallback='use_current_screen_helper')
            clock.seconds = WATCHDOG_SECONDS['routine']
            first = tracker.poll()
            self.assertEqual([item['code'] for item in first['new_alerts']], ['watchdog_due'])
            self.assertEqual(tracker.poll()['new_alerts'], [])

    def test_watchdog_can_be_refined_without_resetting_elapsed_decision_time(self):
        state = event(self.state(), 0, 'begin_move', move_id='after-input', kind='reviewed_decision')
        state = event(state, 7, 'set_watchdog', watchdog_seconds=WATCHDOG_SECONDS['routine'],
                      watchdog_class='routine', watchdog_fallback='use_current_screen_helper')
        self.assertEqual(state['move']['active_seconds'], 7)
        self.assertEqual(state['move']['watchdog_budget_seconds'], WATCHDOG_SECONDS['routine'])

    def test_focus_target_are_not_completed_cards_and_repeated_begin_does_not_reset(self):
        state = event(self.state(), 0, "begin_move", move_id="play-defend", kind="combat_card")
        state = event(state, 5, "verified_input", action_id="focus", step_kind="focus", move_complete=False)
        state = event(state, 12, "begin_move", move_id="play-defend", kind="combat_card")
        state = event(state, 15, "verified_input", action_id="target", step_kind="target", move_complete=False)
        self.assertEqual(state["move"]["started_at"], at(0).isoformat())
        self.assertEqual(state["move"]["active_seconds"], 15)
        state = event(state, 25, "verified_input", action_id="play", step_kind="select", move_complete=True)
        self.assertIsNone(state["move"])
        self.assertEqual(state["completed_move_count"], 1)
        self.assertEqual(state["completed_move_overrun_count"], 1)
        self.assertEqual(state["completed_moves"][0]["active_seconds"], 25)
        state = event(state, 30, "verified_input", action_id="play", step_kind="select", move_complete=True)
        self.assertEqual(state["completed_move_count"], 1)
        self.assertEqual(state["verified_input_count"], 3)

    def test_navigation_cannot_claim_logical_completion_or_displace_pending_move(self):
        state = event(self.state(), 0, "begin_move", move_id="play", kind="combat_card")
        original = deepcopy(state)
        for step_kind in ("focus", "target", "inspect", "navigation", "arm"):
            with self.subTest(step_kind=step_kind), self.assertRaises(ValueError):
                event(state, 1, "verified_input", action_id="a", step_kind=step_kind, move_complete=True)
        with self.assertRaisesRegex(ValueError, "must not be reset"):
            event(state, 3, "begin_move", move_id="another", kind="combat_card")
        self.assertEqual(state, original)

    def test_only_explicit_pause_and_user_wait_are_excluded_from_all_open_scopes(self):
        state = event(self.state(), 0, "begin_floor", floor_id="floor", kind="combat")
        state = event(state, 0, "begin_move", move_id="play", kind="combat_card")
        state = event(state, 10, "pause", category="user_wait", reason="User requested a break")
        state = event(state, 110, "resume")
        state = event(state, 115, "pause", category="paused", reason="Stopped adapter")
        state = event(state, 215, "resume")
        state = event(state, 220, "tick")
        self.assertEqual(state["active_seconds"], 20)
        self.assertEqual(state["excluded_seconds"], 200)
        self.assertEqual(state["excluded_by_category"], {"paused": 100, "user_wait": 100})
        for scope in ("startup", "move", "floor"):
            self.assertEqual(state[scope]["active_seconds"], 20)
            self.assertEqual(state[scope]["excluded_seconds"], 200)
        with self.assertRaisesRegex(ValueError, "requires an explicit pause"):
            event(state, 221, "resume")

    def test_failures_research_and_unmarked_idle_count_as_active(self):
        state = event(self.state(), 0, "begin_move", move_id="play", kind="combat_card")
        state = event(state, 10, "failure", code="draft_invalid", reason="Fix source-free request")
        state = event(state, 20, "phase", name="recovery")
        state = event(state, 70, "overrun", reason="Schema work exceeded target")
        summary = summarize_timing(state)
        self.assertEqual(summary["move"]["active_seconds"], 70)
        self.assertEqual(summary["move"]["excluded_seconds"], 0)
        self.assertEqual(len(summary["recent_failures"]), 2)
        self.assertFalse(summary["controller_authorized"])
        self.assertFalse(summary["automatic_input"])
        self.assertFalse(summary["targets_are_safety_overrides"])

    def test_model_and_tool_wait_have_separate_phase_buckets(self):
        state = event(self.state(), 1, "phase", name="model_inference")
        state = event(state, 4, "phase", name="tool_wait")
        summary = summarize_timing(state)
        self.assertEqual(summary["startup"]["phase_active_seconds"],
                         {"startup": 1, "model_inference": 3})
        self.assertIn("tool_wait", PHASES)

    def test_phase_accepts_optional_reason_for_launcher_annotations(self):
        state = event(self.state(), 1, "phase", name="model_inference", reason="Choose the next legal action")
        self.assertEqual(state["phase"], "model_inference")
        self.assertEqual(state["events"][-1]["reason"], "Choose the next legal action")

    def test_stale_capture_budget_is_bounded_per_move_and_duplicate_receipt_does_not_inflate(self):
        state = event(self.state(), 0, "begin_move", move_id="play", kind="combat_card")
        state = event(state, 10, "stale_capture", capture_id="frame-1")
        state = event(state, 11, "stale_capture", capture_id="frame-1")
        self.assertEqual(state["move"]["stale_recapture_count"], 1)
        state = event(state, 15, "stale_capture", capture_id="frame-2")
        state = event(state, 16, "verified_input", action_id="focus", step_kind="focus")
        result = summarize_timing(state)
        self.assertEqual(result["move"]["stale_recapture_count"], STALE_RECAPTURE_LIMIT)
        self.assertTrue(result["move"]["stale_budget_exhausted"])
        self.assertTrue(any(item["code"] == "stale_recapture_budget_exhausted" for item in result["diagnostics"]))

    def test_every_floor_target_and_completed_overruns_are_recorded(self):
        for kind, target in (("noncombat", 90), ("combat", 240), ("elite", 360), ("boss", 480)):
            state = event(self.state(), 0, "begin_floor", floor_id="floor", kind=kind)
            state = event(state, target - 1, "begin_floor", floor_id="floor", kind=kind)
            self.assertEqual(state["floor"]["started_at"], at(0).isoformat())
            state = event(state, target + 1, "complete_floor", floor_id="floor")
            self.assertEqual(state["completed_floors"][0]["target_seconds"], target)
            self.assertEqual(state["completed_floor_overrun_count"], 1)

    def test_pending_move_prevents_floor_completion(self):
        state = event(self.state(), 0, "begin_floor", floor_id="floor", kind="combat")
        state = event(state, 1, "begin_move", move_id="play", kind="combat_card")
        with self.assertRaisesRegex(ValueError, "pending logical move"):
            event(state, 2, "complete_floor", floor_id="floor")

    def test_midfloor_resume_is_qualified_without_invalidating_the_global_clock(self):
        partial = event(self.state(), 0, "begin_floor", floor_id="floor", kind="combat")
        observed = event(self.state(), 0, "begin_floor", floor_id="floor", kind="combat", observed_from_entry=True)
        partial_result = summarize_timing(partial, at=at(10))
        observed_result = summarize_timing(observed, at=at(10))
        self.assertFalse(partial_result["floor"]["observed_from_entry"])
        self.assertFalse(partial_result["floor"]["full_floor_target_verifiable"])
        self.assertIsNone(partial_result["floor"]["over_target"])
        self.assertTrue(partial_result["measurement_complete"])
        self.assertTrue(observed_result["floor"]["full_floor_target_verifiable"])
        self.assertFalse(observed_result["floor"]["over_target"])
        repeated = event(partial, 20, "begin_floor", floor_id="floor", kind="combat", observed_from_entry=True)
        self.assertFalse(repeated["floor"]["observed_from_entry"], "later declarations cannot manufacture missing entry coverage")

    def test_rebase_archives_a_paused_floor_and_starts_a_partial_resumed_segment(self):
        state = event(self.state(), 0, "begin_floor", floor_id="floor", kind="combat", observed_from_entry=True)
        state = event(state, 20, "pause", category="paused", reason="Player stopped the adapter")
        state = event(state, 120, "resume")
        state = event(state, 120, "rebase_floor", floor_id="floor", kind="combat",
                      reason="Adapter resumed after a paused play session.")
        result = summarize_timing(state, at=at(135))
        self.assertEqual(1, result["interrupted_floor_segments"])
        self.assertEqual(20, state["interrupted_floors"][0]["active_seconds"])
        self.assertEqual(15, result["floor"]["active_seconds"])
        self.assertFalse(result["floor"]["full_floor_target_verifiable"])
        self.assertIsNone(result["floor"]["over_target"])

    def test_wall_rollback_uses_same_process_monotonic_without_losing_elapsed(self):
        state = self.state(monotonic_ns=0, clock_id="process-a")
        state = record_event(state, {"operation": "begin_move", "move_id": "m", "kind": "card"},
                             at=at(10), monotonic_ns=10_000_000_000, clock_id="process-a")
        state = record_event(state, {"operation": "tick"}, at=at(0), monotonic_ns=20_000_000_000, clock_id="process-a")
        self.assertEqual(state["active_seconds"], 20)
        self.assertEqual(state["move"]["active_seconds"], 10)
        self.assertTrue(state["measurement_complete"])
        self.assertIn("wall_clock_changed_monotonic_used", state["clock_issues"])

    def test_restart_clock_rollback_is_explicit_incomplete_measurement_not_negative_or_reset(self):
        state = self.state(monotonic_ns=0, clock_id="process-a")
        state = record_event(state, {"operation": "begin_move", "move_id": "m", "kind": "card"},
                             at=at(10), monotonic_ns=10_000_000_000, clock_id="process-a")
        state = record_event(state, {"operation": "resume_session"}, at=at(5), monotonic_ns=0, clock_id="process-b")
        result = summarize_timing(state)
        self.assertEqual(result["active_seconds"], 10)
        self.assertEqual(result["move"]["id"], "m")
        self.assertFalse(result["measurement_complete"])
        self.assertIsNone(result["move"]["remaining_seconds"])

    def test_late_adapter_start_does_not_claim_full_startup_measurement(self):
        result = summarize_timing(self.state(startup_observed=False), at=at(10))
        self.assertFalse(result["startup"]["observed_from_launch"])
        self.assertEqual(result["startup"]["measurement_basis"], "incomplete_elapsed_lower_bound")
        self.assertTrue(any(item["code"] == "startup_measurement_started_late" for item in result["diagnostics"]))

    def test_percentiles_reuse_completed_logical_move_samples_only(self):
        state = event(self.state(), 0, "begin_move", move_id="first", kind="card")
        state = event(state, 10, "verified_input", action_id="first", step_kind="select", move_complete=True)
        state = event(state, 10, "begin_move", move_id="second", kind="card")
        state = event(state, 30, "verified_input", action_id="second", step_kind="select", move_complete=True)
        state = event(state, 30, "begin_move", move_id="third", kind="card")
        result = summarize_timing(state, at=at(90))
        self.assertEqual(result["recent_completed_move_seconds"]["samples"], 2)
        self.assertEqual(result["recent_completed_move_seconds"]["p50"], 15)
        self.assertEqual(result["recent_completed_move_seconds"]["p95"], 19.5)

    def test_invalid_time_phase_or_unbounded_fields_do_not_mutate_input(self):
        state = self.state()
        before = deepcopy(state)
        for payload in ({"operation": "phase", "name": "skip-safety"},
                        {"operation": "begin_move", "move_id": "x" * 129, "kind": "card"},
                        {"operation": "tick", "controller": True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                record_event(state, payload, at=at(1))
        with self.assertRaises(ValueError):
            record_event(state, {"operation": "tick"}, at=datetime(2026, 1, 1))
        self.assertEqual(state, before)


class PersistentTimingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "timing.json"
        self.clock = Clock()

    def tracker(self, **kwargs):
        return PlayTiming(self.path, run_id="run-1", clock=self.clock.wall, monotonic_clock=self.clock.monotonic,
                          clock_id=kwargs.pop("clock_id", "process-a"), **kwargs)

    def test_reopen_resume_keeps_pending_move_scope_and_counts_unmarked_restart_gap(self):
        original = self.tracker()
        original.event("begin_move", move_id="move-1", kind="card")
        self.clock.seconds = 10
        original.event("phase", name="planning")
        self.clock.seconds = 50
        reopened = self.tracker(clock_id="process-b")
        result = reopened.event("resume_session")
        self.assertEqual(result["session_id"], original.session_id)
        self.assertEqual(result["move"]["active_seconds"], 50)
        self.assertEqual(result["move"]["started_at"], at(0).isoformat())
        self.assertEqual(result["clock_bridges"], 1)

    def test_opening_existing_clock_never_overwrites_another_identity(self):
        original = self.tracker()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "another run/session"):
            PlayTiming(self.path, run_id="different-run")
        with self.assertRaisesRegex(ValueError, "another run/session"):
            self.tracker(session_id="different-session")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(original.snapshot()["session_id"], original.session_id)

    def test_pause_survives_reopen_until_explicit_resume(self):
        original = self.tracker()
        self.clock.seconds = 10
        original.event("pause", category="paused", reason="User stopped play")
        self.clock.seconds = 100
        reopened = self.tracker(clock_id="process-b")
        paused = reopened.event("resume_session")
        self.assertIsNotNone(paused["pause"])
        self.assertEqual(paused["active_seconds"], 10)
        self.assertEqual(paused["excluded_seconds"], 90)
        resumed = reopened.event("resume")
        self.assertIsNone(resumed["pause"])

    def test_poll_emits_each_threshold_once_even_after_process_restart(self):
        tracker = self.tracker()
        tracker.event("begin_move", move_id="move", kind="card")
        tracker.event("begin_floor", floor_id="floor", kind="noncombat")
        self.clock.seconds = 91
        first = tracker.poll()
        self.assertEqual({item["scope"] for item in first["new_alerts"]}, {"startup", "move", "floor"})
        self.clock.seconds = 100
        self.assertEqual(self.tracker(clock_id="process-b").poll()["new_alerts"], [])
        self.assertEqual(len(tracker.summary()["diagnostics"]), 3)

    def test_per_mutation_file_lock_preserves_concurrent_diagnostic_events(self):
        self.tracker()
        trackers = [self.tracker() for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda index: trackers[index % 4].event("failure", code=f"failure-{index}", reason="Measured schema delay"), range(40)))
        stored = self.tracker().snapshot()
        self.assertEqual(len(stored["events"]), 40)
        self.assertEqual(len({item["code"] for item in stored["events"]}), 40)
        self.assertEqual(json.loads(self.path.read_text())["schema"], "veda.play-timing.v1")


if __name__ == "__main__":
    unittest.main()
