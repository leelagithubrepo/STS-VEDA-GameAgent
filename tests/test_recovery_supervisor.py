import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import subprocess

from veda.recovery_supervisor import (BuilderGapQueue, RecoveryJournal, classify_failure,
    classify_workflow_gap, recovery_summary, retry_delays)
from scripts.veda_recovery_watch import _probe_ready


class RecoverySupervisorTests(unittest.TestCase):
    def test_classifies_external_failures_and_preserves_input_boundary(self):
        self.assertEqual("infrastructure_post_input", classify_failure("Remote Play feed timeout", input_sent=True))
        self.assertEqual("infrastructure_pre_input", classify_failure("bridge disconnected"))
        self.assertEqual("post_input_unknown", classify_failure("unknown outcome", input_sent=True))

    def test_journal_is_append_only_and_summarizable(self):
        with TemporaryDirectory() as directory:
            journal = RecoveryJournal(Path(directory), "run")
            journal.append("interruption", "network lost", pending_action_id="a", input_sent=True)
            journal.append("recovered", "probe succeeded", pending_action_id="a", input_sent=True, attempt=2)
            self.assertEqual({"interruptions": 1, "recoveries": 1, "failed_recoveries": 0},
                             recovery_summary(journal.path))
            rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
            self.assertEqual("a", rows[0]["pending_action_id"])

    def test_retry_schedule_is_bounded(self):
        self.assertEqual([1.0, 2.0, 4.0, 8.0, 16.0, 30.0], retry_delays())
        with self.assertRaises(ValueError):
            retry_delays(65)

    def test_probe_requires_positive_bridge_marker(self):
        self.assertTrue(_probe_ready(subprocess.CompletedProcess([], 0, "PS5 'x' — Ok\n", "")))
        self.assertFalse(_probe_ready(subprocess.CompletedProcess([], 0, "PS5 'x' — Unreachable\n", "")))

    def test_workflow_gap_classification_keeps_small_variations_on_hot_path(self):
        request = {"kind": "loot", "context": {"screen": "loot_rewards"}}
        self.assertEqual("small_variation", classify_workflow_gap(request, "focus was unchanged"))
        self.assertEqual("known_family_gap", classify_workflow_gap(request, "missing mapping"))
        self.assertEqual("new_family_gap", classify_workflow_gap({"kind": "unknown_menu"}, "unrecognized screen"))
        self.assertEqual("infrastructure", classify_workflow_gap(request, "Remote Play feed lost"))

    def test_builder_gap_queue_deduplicates_without_blocking_the_run(self):
        with TemporaryDirectory() as directory:
            queue = BuilderGapQueue(Path(directory), "run")
            request = {"kind": "shop", "context": {"screen": "merchant"}}
            first = queue.enqueue(scenario="known_family_gap", reason="missing mapping",
                                  request=request, pending_action_id="a")
            second = queue.enqueue(scenario="known_family_gap", reason="missing mapping",
                                   request=request, pending_action_id="a")
            self.assertTrue(first["queued"])
            self.assertTrue(second["deduplicated"])
            self.assertEqual(1, len(queue.path.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
