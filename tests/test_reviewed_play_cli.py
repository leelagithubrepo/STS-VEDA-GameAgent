"""PTY framing tests using synthetic SQLite and shadow mode only."""
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import unittest
from copy import deepcopy
from scripts.veda_reviewed_play import terminal_response

from veda.telemetry_database import TelemetryDatabase

ROOT = Path(__file__).resolve().parents[1]


class ReviewedPlayCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        database = TelemetryDatabase(self.directory / 'synthetic.sqlite3')
        run_id = database.start_or_resume_run()
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.original = termios.tcgetattr(self.slave)
        self.process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/veda_reviewed_play.py'),
            str(self.directory / 'session'), '--database', str(database.path), '--run-id', run_id,
            '--mode', 'shadow'], cwd=ROOT, stdin=self.slave, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        self.addCleanup(self.stop)
        startup = self.response()
        self.assertEqual(startup['status'], 'ready_unarmed')
        self.assertTrue(startup['summary']['timing']['compact'])

    def stop(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate(timeout=5)

    def response(self):
        ready, _, _ = select.select([self.process.stdout], [], [], 5)
        self.assertTrue(ready, 'adapter must respond to a complete JSONL request without waiting for more input')
        return json.loads(self.process.stdout.readline())

    def send(self, request):
        data = (json.dumps(request) + '\n').encode()
        while data:
            data = data[os.write(self.master, data):]

    def assert_terminal_restored(self):
        restored = termios.tcgetattr(self.slave)
        original = list(self.original)
        # macOS may set its pending-input flag on the noncanonical→canonical
        # transition; it is not a persistent terminal configuration change.
        restored[3] &= ~getattr(termios, 'PENDIN', 0)
        original[3] &= ~getattr(termios, 'PENDIN', 0)
        self.assertEqual(restored, original)

    def test_large_jsonl_request_survives_pty_canonical_limit_and_terminal_restores(self):
        self.send({'operation': 'summary', 'synthetic_padding': 'x' * 12000})
        result = self.response()
        self.assertEqual(result['schema'], 'veda.reviewed-play.v1')
        self.assertFalse(result['armed'])
        self.assertIn('recent_failures', result['timing'])
        self.send({'operation': 'stop'})
        self.assertEqual(self.response()['status'], 'stopped')
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assert_terminal_restored()

    def test_compact_response_keeps_action_recovery_and_failure_details(self):
        reply = {'status': 'recoverable_review', 'reason': 'actual focus differs',
            'pending': {'action_id': 'pending-id', 'must_not_repeat': True},
            'next_operation': 'verify', 'timing': {'schema': 'veda.play-timing-summary.v1',
                'phase': 'verification', 'measurement_complete': False, 'verified_input_count': 3,
                'move': {'id': 'move-id', 'active_seconds': 21, 'target_seconds': 20,
                         'over_target': True, 'measurement_basis': 'incomplete_elapsed_lower_bound'},
                'recent_failures': ['old failure'] * 100}}
        original = deepcopy(reply)
        compact = terminal_response(reply)
        self.assertEqual(original, reply)
        self.assertEqual(original['pending'], compact['pending'])
        self.assertEqual(original['reason'], compact['reason'])
        self.assertEqual('verify', compact['next_operation'])
        self.assertFalse(compact['timing']['measurement_complete'])
        self.assertTrue(compact['timing']['move']['over_target'])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(original)))
        self.assertEqual(original, terminal_response(reply, full_timing=True))

    def test_request_file_pointer_and_interrupt_restore_terminal(self):
        packet = self.directory / 'request.json'
        packet.write_text(json.dumps({'operation': 'summary'}))
        self.send({'request_file': str(packet)})
        self.assertFalse(self.response()['armed'])
        self.process.send_signal(signal.SIGINT)
        self.assertEqual(self.response()['status'], 'interrupted')
        self.assertEqual(self.process.wait(timeout=5), 130)
        self.assert_terminal_restored()
