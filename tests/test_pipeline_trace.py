import contextvars
import json
from pathlib import Path
import tempfile
import unittest

from veda.pipeline_trace import TraceRecorder


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, amount):
        self.now += amount


class PipelineTraceTests(unittest.TestCase):
    def test_nested_stage_accounting_keeps_gaps_unaccounted(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock, run_id="run-1")
        clock.advance(5)
        with trace.span("input_verification_cycle", action_id="action-1"):
            clock.advance(3)
            with trace.span("capture"):
                clock.advance(7)
            clock.advance(2)
            with trace.span("rule_validation"):
                clock.advance(11)
            clock.advance(2)
        clock.advance(5)
        report = trace.report()
        self.assertEqual(report["total_wall_ns"], 35)
        self.assertEqual(report["active_ns"], 35)
        self.assertEqual(report["stage_union_ns"], 25)
        self.assertEqual(report["unaccounted_active_ns"], 10)
        stages = report["stages"]
        self.assertEqual(stages["input_verification_cycle"]["union_ns"], 25)
        self.assertEqual(stages["input_verification_cycle"]["exclusive_ns"], 7)
        self.assertEqual(stages["capture"]["exclusive_ns"], 7)
        self.assertEqual(stages["rule_validation"]["exclusive_ns"], 11)
        self.assertEqual(sum(item["exclusive_ns"] for item in stages.values()), 25)

    def test_recursive_stage_is_unioned_instead_of_counted_twice(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        with trace.span("recovery"):
            clock.advance(3)
            with trace.span("recovery"):
                clock.advance(4)
            clock.advance(3)
        stage = trace.report()["stages"]["recovery"]
        self.assertEqual(stage["union_ns"], 10)
        self.assertEqual(stage["exclusive_ns"], 10)
        self.assertEqual(stage["sample_wall_sum_ns"], 14)
        self.assertEqual(stage["completed_samples"], 2)

    def test_concurrent_stages_have_explicit_overlap_bucket(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        first_context, second_context = contextvars.Context(), contextvars.Context()
        capture, telemetry = trace.span("capture"), trace.span("telemetry")
        first_context.run(capture.__enter__)
        clock.advance(3)
        second_context.run(telemetry.__enter__)
        clock.advance(3)
        first_context.run(capture.__exit__, None, None, None)
        clock.advance(2)
        second_context.run(telemetry.__exit__, None, None, None)
        clock.advance(2)
        report = trace.report()
        self.assertEqual(report["stage_union_ns"], 8)
        self.assertEqual(report["overlapping_stages_ns"], 3)
        self.assertEqual(report["stages"]["capture"]["exclusive_ns"], 3)
        self.assertEqual(report["stages"]["telemetry"]["exclusive_ns"], 2)
        self.assertEqual(report["unaccounted_active_ns"], 2)

    def test_pause_and_maintenance_overlap_does_not_inflate_wall_time(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        with trace.span("pipeline"):
            clock.advance(10)
            trace.event("pause", reason="player request")
            clock.advance(5)
            trace.event("maintenance_start", reason="calibration")
            clock.advance(5)
            trace.event("resume")
            clock.advance(5)
            trace.event("maintenance_end")
            clock.advance(5)
        report = trace.report()
        self.assertEqual(report["total_wall_ns"], 30)
        self.assertEqual(report["pause_ns"], 10)
        self.assertEqual(report["maintenance_ns"], 10)
        self.assertEqual(report["pause_or_maintenance_ns"], 15)
        self.assertEqual(report["active_ns"], 15)
        self.assertEqual(report["active_stage_union_ns"], 15)
        self.assertEqual(report["stages"]["pipeline"]["exclusive_active_ns"], 15)
        self.assertEqual(report["unaccounted_active_ns"], 0)

    def test_open_pause_and_frozen_report_endpoint(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        clock.advance(4)
        trace.event("pause")
        clock.advance(10)
        report = trace.report()
        self.assertEqual(report["active_ns"], 4)
        self.assertEqual(report["pause_ns"], 10)
        self.assertEqual(report["open_interruptions"], ["pause"])
        trace.close()
        clock.advance(100)
        self.assertEqual(trace.report()["total_wall_ns"], 14)
        self.assertTrue(trace.report()["closed"])
        with self.assertRaises(RuntimeError):
            trace.event("resume")
        trace.close()  # Closing an already closed recorder is harmless.

    def test_completed_samples_only_and_labeled_percentiles(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        with trace.span("capture"):
            clock.advance(8)
            report = trace.report()
            self.assertEqual(report["stages"]["capture"]["completed_samples"], 0)
            self.assertEqual(report["stages"]["capture"]["in_flight_spans"], 1)
            self.assertIsNone(report["stages"]["capture"]["p95_ns"])
            self.assertEqual(report["stage_union_ns"], 8)
            with self.assertRaises(RuntimeError):
                trace.close()
        for duration in [10, 20, 30, 40]:
            with trace.span("model_request_round_trip", request_id=str(duration)):
                clock.advance(duration)
        report = trace.report()
        stage = report["stages"]["model_request_round_trip"]
        self.assertEqual(stage["completed_samples"], 4)
        self.assertEqual(stage["p50_ns"], 25)
        self.assertAlmostEqual(stage["p95_ns"], 38.5)
        self.assertIn("completed-span wall", report["percentile_method"])
        self.assertEqual(report["unit"], "nanoseconds")
        self.assertNotIn("reasoning", report["stages"])

    def test_retry_errors_and_coverage_use_real_unique_opportunities(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        self.assertIsNone(trace.report()["coverage"]["fast_path_coverage"])
        trace.record_decision("d1", handled_locally=True, frame_id="frame-1")
        trace.record_decision("d2", handled_locally=True, error="state_mismatch")
        trace.record_decision("d3", handled_locally=False,
                              escalation_reason="unsupported_card")
        trace.record_decision("d4", handled_locally=False,
                              escalation_reason="unknown_intent", error="state_unknown")
        trace.event("retry", reason="capture_timeout", request_id="r1")
        trace.event("retry", reason="capture_timeout", request_id="r2")
        trace.event("state_error", reason="unreadable_cost")
        with self.assertRaises(ValueError):
            trace.record_decision("d1", handled_locally=True)
        with self.assertRaises(ValueError):
            trace.record_decision("d5", handled_locally=False)
        report = trace.report()
        coverage = report["coverage"]
        self.assertEqual(coverage["opportunities"], 4)
        self.assertEqual(coverage["handled_locally"], 2)
        self.assertEqual(coverage["fast_path_coverage"], .5)
        self.assertEqual(coverage["successful_fast_path_coverage"], .25)
        self.assertEqual(coverage["decision_errors"], 2)
        self.assertEqual(coverage["escalations_by_reason"],
                         {"unsupported_card": 1, "unknown_intent": 1})
        self.assertEqual(report["retries_by_reason"], {"capture_timeout": 2})
        self.assertEqual(report["events"]["state_error"], 1)

    def test_exception_is_preserved_and_span_stack_recovers(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        with self.assertRaisesRegex(RuntimeError, "deliberate failure"):
            with trace.span("capture"):
                clock.advance(7)
                raise RuntimeError("deliberate failure")
        with trace.span("recovery"):
            clock.advance(3)
        report = trace.report()
        self.assertEqual(report["span_errors"], {"RuntimeError": 1})
        self.assertEqual(report["stages"]["capture"]["completed_samples"], 1)
        self.assertEqual(report["stages"]["recovery"]["exclusive_ns"], 3)

    def test_later_execution_error_updates_same_opportunity_and_journal(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            trace = TraceRecorder(path, clock=clock)
            trace.record_decision("d1", handled_locally=True, action_id="a1")
            self.assertEqual(trace.report()["coverage"]["successful_fast_path_coverage"], 1)
            clock.advance(9)
            trace.record_decision_error("d1", "verification_mismatch")
            trace.record_decision_error("d1", "verification_mismatch")
            coverage = trace.report()["coverage"]
            self.assertEqual(coverage["opportunities"], 1)
            self.assertEqual(coverage["fast_path_coverage"], 1)
            self.assertEqual(coverage["successful_fast_path_coverage"], 0)
            self.assertEqual(coverage["decision_errors"], 1)
            self.assertEqual(coverage["errors_by_reason"], {"verification_mismatch": 1})
            with self.assertRaises(ValueError):
                trace.record_decision_error("unknown", "mismatch")
            trace.close()
            updates = [json.loads(line) for line in path.read_text().splitlines()
                       if json.loads(line)["kind"] == "decision_error"]
            self.assertEqual(len(updates), 2)
            self.assertEqual(updates[0]["decision_id"], "d1")
            self.assertEqual(updates[0]["ids"], {"action_id": "a1"})

    def test_jsonl_is_correlated_append_only_and_available_before_close(self):
        clock = Clock(100)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            trace = TraceRecorder(path, clock=clock, run_id="run", floor_id="floor")
            with trace.span("cycle", turn_id="turn", action_id="action") as handle:
                handle.annotate(frame_id="frame")
                with trace.span("capture", request_id="request"):
                    clock.advance(4)
                    trace.event("retry", reason="not_ready")
            trace.record_decision("decision", handled_locally=True)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            capture = next(record for record in records
                           if record["kind"] == "span_start" and record["stage"] == "capture")
            self.assertEqual(capture["ids"], {"run_id": "run", "floor_id": "floor",
                             "turn_id": "turn", "action_id": "action",
                             "frame_id": "frame", "request_id": "request"})
            retry = next(record for record in records if record.get("event") == "retry")
            self.assertEqual(retry["metadata"]["frame_id"], "frame")
            self.assertEqual(retry["metadata"]["request_id"], "request")
            self.assertEqual({record["trace_id"] for record in records}, {trace.trace_id})
            trace.close()
            previous = path.read_text()
            second = TraceRecorder(path, clock=clock)
            second.close()
            self.assertTrue(path.read_text().startswith(previous))
            self.assertNotEqual(trace.trace_id, second.trace_id)

    def test_invalid_event_transitions_do_not_change_the_record(self):
        clock = Clock()
        trace = TraceRecorder(clock=clock)
        for kind in ["resume", "maintenance_end"]:
            with self.assertRaises(ValueError):
                trace.event(kind)
        trace.event("pause")
        with self.assertRaises(ValueError):
            trace.event("pause")
        self.assertEqual(trace.report()["events"], {"pause": 1})
        with self.assertRaises(ValueError):
            with trace.span(""):
                pass
        with self.assertRaises(ValueError):
            trace.record_decision("d1", handled_locally=False, escalation_reason=[])

    def test_clock_is_monotonic_and_zero_duration_sample_is_valid(self):
        clock = Clock(20)
        trace = TraceRecorder(clock=clock)
        with trace.span("parse"):
            pass
        self.assertEqual(trace.report()["stages"]["parse"]["p95_ns"], 0)
        clock.now = 19
        with self.assertRaisesRegex(ValueError, "backwards"):
            trace.report()
        with self.assertRaisesRegex(ValueError, "integer nanoseconds"):
            TraceRecorder(clock=lambda: 1.2)


if __name__ == "__main__":
    unittest.main()
