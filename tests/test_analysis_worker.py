"""Bounded transport tests using tiny local Python workers, never models/game I/O."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import sys
import time
import unittest
from unittest.mock import patch

from veda.execution import RuntimeStop
from veda.execution_adapters import AnalysisWorker
from veda.runtime_authorization import IDENTITY_SCHEMA, recognizer_fingerprint
from veda.runtime_frames import RuntimeFrame
from veda.vision import StructuredGameState


TEMPLATE = dict(frame_id='', image_sha256='', state=asdict(StructuredGameState('COMBAT', 1.0)),
                context={}, ui={}, encounter_name=None, run_id='synthetic', floor_id='f1', turn_id='t1')


def frame(ident='synthetic-1'):
    return RuntimeFrame(ident, Path('/tmp/synthetic-worker-frame-not-opened'),
        hashlib.sha256(ident.encode()).hexdigest(), datetime.now(timezone.utc).isoformat(), time.monotonic_ns())


def command(body=None, *, setup=''):
    body = body or "sys.stdout.write(json.dumps(response) + '\\n'); sys.stdout.flush()"
    script = ('import sys,json,os,signal,time\n'
              f'template=json.loads({json.dumps(TEMPLATE)!r})\n' + setup + '\n'
              'for line in sys.stdin:\n'
              ' request=json.loads(line)\n'
              ' response=dict(template, frame_id=request["frame"]["frame_id"], '
              'image_sha256=request["frame"]["sha256"])\n' +
              '\n'.join(' ' + line for line in body.splitlines()) + '\n')
    return [sys.executable, '-u', '-c', script]


def identity(cmd):
    # Only the structural fingerprint function is exercised here, not live
    # authorization or any claim about independent vision validation.
    return {'schema': IDENTITY_SCHEMA, 'command': cmd,
            'code_files': [{'path': sys.executable, 'sha256': '0' * 64}],
            'model': {'id': 'synthetic-protocol-fixture', 'sha256': '1' * 64},
            'prompt': {'sha256': '2' * 64}, 'preprocessing': {'sha256': '3' * 64},
            'output_contract': 'veda.runtime-reading.v1'}


class AnalysisWorkerTests(unittest.TestCase):
    def test_reuses_one_process_and_nonblocking_pipes(self):
        worker = AnalysisWorker(command())
        try:
            first = worker.read(frame(), purpose='contract-test')
            pid = worker.process.pid
            second = worker.read(frame('synthetic-2'), purpose='contract-test', previous=first)
            self.assertEqual(worker.process.pid, pid)
            self.assertEqual(second.frame_id, 'synthetic-2')
            self.assertFalse(os.get_blocking(worker.process.stdin.fileno()))
            self.assertFalse(os.get_blocking(worker.process.stdout.fileno()))
        finally:
            worker.close()
        self.assertIsNotNone(worker.process.poll())
        self.assertTrue(worker.process.stdin.closed)
        self.assertTrue(worker.process.stdout.closed)
        worker.close()  # Idempotent cleanup cannot restart a worker.

    def test_partial_writes_and_transient_pipe_backpressure(self):
        worker = AnalysisWorker(command())
        try:
            worker._start()
            real_write = os.write
            count = 0

            def partial(fd, data):
                nonlocal count
                count += 1
                if count == 1:
                    raise BlockingIOError()
                return real_write(fd, data[:17])

            with patch('veda.execution_adapters.os.write', side_effect=partial):
                result = worker.read(frame(), purpose='partial-write-fixture')
            self.assertEqual(result.frame_id, 'synthetic-1')
            self.assertGreater(count, 2)
        finally:
            worker.close()

    def test_nonreading_worker_hits_write_deadline_and_is_cleaned_up(self):
        worker = AnalysisWorker([sys.executable, '-u', '-c', 'import time; time.sleep(30)'],
                                timeout_seconds=0.15, max_request_bytes=1_000_000)
        began = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'write deadline'):
            worker.read(frame(), purpose='x' * 500_000)
        self.assertLess(time.monotonic() - began, 2)
        self.assertIsNotNone(worker.process.poll())
        self.assertTrue(worker.process.stdin.closed)
        with self.assertRaisesRegex(RuntimeStop, 'closed'):
            worker.read(frame(), purpose='cannot-retry')

    def test_zero_write_fails_instead_of_spinning(self):
        worker = AnalysisWorker(command())
        worker._start()
        began = time.monotonic()
        with patch('veda.execution_adapters.os.write', return_value=0):
            with self.assertRaisesRegex(RuntimeStop, 'no write progress'):
                worker.read(frame(), purpose='zero-write')
        self.assertLess(time.monotonic() - began, 2)
        self.assertIsNotNone(worker.process.poll())

    def test_missing_response_hits_read_deadline_and_never_restarts(self):
        worker = AnalysisWorker(command('time.sleep(30)'), timeout_seconds=0.15)
        with self.assertRaisesRegex(TimeoutError, 'response deadline'):
            worker.read(frame(), purpose='no-response')
        pid = worker.process.pid
        with self.assertRaisesRegex(RuntimeStop, 'closed'):
            worker.read(frame('next'), purpose='no-restart')
        self.assertEqual(worker.process.pid, pid)

    def test_trailing_data_and_multiple_responses_are_rejected(self):
        for suffix in ('extra', '\n', '{}\n'):
            with self.subTest(suffix=suffix):
                worker = AnalysisWorker(command(
                    f'os.write(1, (json.dumps(response) + "\\n" + {suffix!r}).encode())'))
                with self.assertRaisesRegex(RuntimeStop, 'unsolicited trailing output'):
                    worker.read(frame(), purpose='trailing-data')
                self.assertIsNotNone(worker.process.poll())

    def test_late_unsolicited_output_is_rejected_before_another_request(self):
        worker = AnalysisWorker(command(setup="signal.signal(signal.SIGUSR1, lambda *_: os.write(1, b'unsolicited\\n'))"))
        try:
            worker.read(frame(), purpose='first')
            os.kill(worker.process.pid, signal.SIGUSR1)
            self.assertTrue(select.select([worker.process.stdout.fileno()], [], [], 2)[0])
            with self.assertRaisesRegex(RuntimeStop, 'unsolicited trailing output'):
                worker.read(frame('second'), purpose='must-not-submit')
        finally:
            worker.close()

    def test_size_bound_eof_and_wrong_frame_fail_closed(self):
        workers = [
            (AnalysisWorker(command('os.write(1, b"x" * 200)'), max_response_bytes=100), RuntimeStop, 'exceeds bound'),
            (AnalysisWorker(command('os.write(1, b"{"); sys.exit(0)')), RuntimeStop, 'closed before response'),
            (AnalysisWorker(command('response["frame_id"]="wrong"\nprint(json.dumps(response), flush=True)')),
             RuntimeStop, 'different frame'),
        ]
        for worker, error, message in workers:
            with self.subTest(message=message):
                try:
                    with self.assertRaisesRegex(error, message):
                        worker.read(frame(), purpose='invalid-output')
                    self.assertIsNotNone(worker.process.poll())
                finally:
                    worker.close()

    def test_request_bound_is_checked_before_launch(self):
        worker = AnalysisWorker(command(), max_request_bytes=64)
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            with self.assertRaisesRegex(RuntimeStop, 'request exceeds bound'):
                worker.read(frame(), purpose='too-large')
        self.assertIsNone(worker.process)

    def test_recognizer_fingerprint_is_bound_and_copied(self):
        cmd = command('response["recognizer_fingerprint"]=request["recognizer_fingerprint"]\nprint(json.dumps(response), flush=True)')
        declared = identity(cmd)
        expected = recognizer_fingerprint(declared)
        worker = AnalysisWorker(cmd, recognizer_identity=declared)
        declared['model']['id'] = 'mutated-after-construction'
        try:
            self.assertEqual(worker.recognizer_fingerprint, expected)
            self.assertNotEqual(worker.recognizer_identity['model']['id'], declared['model']['id'])
            self.assertEqual(worker.read(frame(), purpose='fingerprint-test').frame_id, 'synthetic-1')
        finally:
            worker.close()
        for value in (None, 'incorrect'):
            cmd = command(f'response["recognizer_fingerprint"]={value!r}\nprint(json.dumps(response), flush=True)')
            worker = AnalysisWorker(cmd, recognizer_identity=identity(cmd))
            with self.assertRaisesRegex(RuntimeStop, 'configuration fingerprint'):
                worker.read(frame(), purpose='fingerprint-mismatch')
            self.assertIsNotNone(worker.process.poll())

    def test_cleanup_kills_worker_ignoring_termination(self):
        worker = AnalysisWorker(command(setup='signal.signal(signal.SIGTERM, signal.SIG_IGN)'))
        worker.read(frame(), purpose='cleanup-fixture')
        began = time.monotonic()
        worker.close()
        self.assertLess(time.monotonic() - began, 3)
        self.assertEqual(worker.process.returncode, -signal.SIGKILL)
        self.assertTrue(worker.process.stdout.closed)

    def test_cleanup_error_preserves_request_failure_and_can_be_retried(self):
        worker = AnalysisWorker(command('time.sleep(30)'), timeout_seconds=0.15)
        worker._start()
        try:
            with patch.object(worker.process, 'terminate', side_effect=PermissionError('synthetic cleanup failure')):
                with self.assertRaisesRegex(TimeoutError, 'response deadline') as caught:
                    worker.read(frame(), purpose='cleanup-error-fixture')
            self.assertTrue(worker.process.stdin.closed)
            self.assertTrue(worker.process.stdout.closed)
            self.assertIn('cleanup failed', '\n'.join(caught.exception.__notes__))
        finally:
            worker.close()
        self.assertIsNotNone(worker.process.poll())

    def test_limits_are_finite_and_positive(self):
        for value in (float('nan'), float('inf'), 0, -1, True):
            with self.assertRaises(ValueError):
                AnalysisWorker(command(), timeout_seconds=value)


if __name__ == '__main__':
    unittest.main()
