"""Manual handoff timing cannot claim it measured work before it started."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.veda_play_clock import main
from veda.play_timing import PlayTiming


class ManualClockCoverageTests(unittest.TestCase):
    def test_manual_start_marks_startup_as_partial(self):
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(['start', '--session', directory, '--run-id', 'fixture']))
            result = json.loads(output.getvalue())['timing']
            self.assertFalse(result['startup']['observed_from_launch'])
            self.assertEqual('incomplete_elapsed_lower_bound', result['startup']['measurement_basis'])

    def test_manual_status_preserves_existing_launcher_coverage(self):
        with TemporaryDirectory() as directory:
            timing = PlayTiming(Path(directory) / 'timing.json', run_id='fixture', startup_observed=True)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(['status', '--session', directory, '--run-id', 'fixture']))
            self.assertTrue(timing.snapshot()['startup']['observed_from_launch'])

    def test_gap_annotation_preserves_pending_scopes_and_does_not_unpause(self):
        with TemporaryDirectory() as directory:
            timing = PlayTiming(Path(directory) / 'timing.json', run_id='fixture')
            timing.event('begin_move', move_id='card', kind='combat_card')
            timing.event('pause', category='paused', reason='user stop')
            before = timing.snapshot()
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, main(['measurement_gap', '--session', directory, '--run-id', 'fixture',
                                         '--reason', 'handoff_work_preceded_clock_start']))
            value = timing.snapshot()
            self.assertFalse(value['measurement_complete'])
            self.assertEqual(before['pause'], value['pause'])
            self.assertEqual(before['move']['started_at'], value['move']['started_at'])
            self.assertEqual(before['verified_input_count'], value['verified_input_count'])
            self.assertIn('handoff_work_preceded_clock_start', value['clock_issues'])
            self.assertFalse(json.loads(output.getvalue())['controller_input_sent'])
