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
        self.assertEqual(self.response()['status'], 'ready_unarmed')

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
        self.send({'operation': 'stop'})
        self.assertEqual(self.response()['status'], 'stopped')
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assert_terminal_restored()

    def test_request_file_pointer_and_interrupt_restore_terminal(self):
        packet = self.directory / 'request.json'
        packet.write_text(json.dumps({'operation': 'summary'}))
        self.send({'request_file': str(packet)})
        self.assertFalse(self.response()['armed'])
        self.process.send_signal(signal.SIGINT)
        self.assertEqual(self.response()['status'], 'interrupted')
        self.assertEqual(self.process.wait(timeout=5), 130)
        self.assert_terminal_restored()
