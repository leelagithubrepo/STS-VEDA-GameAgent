"""Menu CLI contracts with synthetic PNGs; no capture, DB, bridge or input.

Fixture facts are explicit test declarations. Validation is source-free, and
preparation requires a separate affirmative review of the exact saved image.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import veda_menu, veda_reviewed_play
from tests import test_reviewed_play as image_fixture
from veda.menu_controls import CONTROL_PROFILE

ROOT = Path(__file__).resolve().parents[1]


class MenuDraftCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.draft = json.loads((ROOT / '.veda/examples/menu-upgrade-open-draft.json').read_text())
        self.draft['context'].update(run_id='synthetic-run', floor_id='synthetic-floor')
        self.draft_path = self.root / 'draft.json'
        self.draft_path.write_text(json.dumps(self.draft))
        self.original_draft = self.draft_path.read_bytes()
        self.output = self.root / 'prepared.json'
        self.now = datetime.now(timezone.utc)
        self.captured = self.now - timedelta(seconds=2)
        self.make_capture(self.captured)

    def make_capture(self, captured):
        self.image = self.root / ('ps5_observation_' + captured.strftime('%Y%m%dT%H%M%S.%fZ')
                                  + '_' + 'a' * 32 + '.png')
        self.image.write_bytes(image_fixture.png_bytes((7, 42, 91)))
        self.receipt = {'schema': 'veda.game-window-capture.v1', 'image_path': str(self.image),
            'image_sha256': hashlib.sha256(self.image.read_bytes()).hexdigest(), 'dimensions': [8, 8],
            'capture_requested_at': captured.isoformat(),
            'capture_completed_at': (captured + timedelta(seconds=1)).isoformat(),
            'window': {'id': 123, 'title': 'Synthetic fixture, never queried'},
            'pixel_content_verified': False, 'controller_input_sent': False}
        self.receipt_path = self.image.with_suffix('.capture.json')
        self.receipt_path.write_text(json.dumps(self.receipt))

    def args(self):
        return ['--draft', str(self.draft_path), '--capture', str(self.image),
            '--reviewer', 'Synthetic inspector', '--evidence-note', 'Explicit inspection declaration for test PNG.',
            '--reviewed', '--output', str(self.output), '--control-profile', CONTROL_PROFILE]

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = veda_menu.main(args)
            except SystemExit as error:
                status = error.code
        return status, stdout.getvalue(), stderr.getvalue()

    def unchanged(self):
        self.assertEqual(self.draft_path.read_bytes(), self.original_draft)
        self.assertFalse(self.output.exists())

    def test_real_cli_help_needs_no_source_or_runtime(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/veda_menu.py'), '--help'],
                                cwd=self.root, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ('--draft', '--validate', '--capture', '--reviewed', '--request'):
            self.assertIn(text, result.stdout)
        self.unchanged()

    def test_real_cli_validates_source_free_draft_from_other_working_directory(self):
        before = set(self.root.iterdir())
        result = subprocess.run([sys.executable, str(ROOT/'scripts/veda_menu.py'),
            '--draft', str(self.draft_path), '--validate', '--control-profile', CONTROL_PROFILE],
            cwd=self.root, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertNotIn('request_file', report)
        self.assertIs(report['controller_input_sent'], False)
        self.assertEqual(set(self.root.iterdir()), before)
        self.unchanged()

    def test_validation_never_calls_capture_or_source_review(self):
        from veda import menu_requests
        with patch.object(menu_requests, 'reviewed_capture_source', side_effect=AssertionError('no capture binding')) as bind, \
             patch('veda.game_capture.capture_game_window', side_effect=AssertionError('no live capture')) as capture:
            status, output, error = self.invoke(['--draft', str(self.draft_path), '--validate',
                                               '--control-profile', CONTROL_PROFILE])
        self.assertEqual(status, 0, error + output)
        self.assertNotIn('request_file', json.loads(output))
        bind.assert_not_called()
        capture.assert_not_called()
        self.unchanged()

    def test_prepare_requires_every_explicit_review_argument(self):
        args = self.args()
        for flag in ('--capture', '--reviewer', '--evidence-note', '--reviewed', '--output'):
            missing = list(args)
            index = missing.index(flag)
            del missing[index:index + (1 if flag == '--reviewed' else 2)]
            with self.subTest(flag=flag):
                status, output, error = self.invoke(missing)
                self.assertEqual(status, 2)
                self.assertEqual(output, '')
                self.assertIn('requires', error)
                self.unchanged()

    def test_validate_rejects_capture_review_and_output_even_empty_text_flags(self):
        base = ['--draft', str(self.draft_path), '--validate', '--control-profile', CONTROL_PROFILE]
        extra_options = [['--capture', str(self.image)], ['--capture', ''],
            ['--reviewer', 'person'], ['--reviewer', ''], ['--evidence-note', 'seen'],
            ['--evidence-note', ''], ['--reviewed'], ['--output', str(self.output)]]
        for extra in extra_options:
            with self.subTest(extra=extra):
                status, output, error = self.invoke(base + extra)
                self.assertEqual(status, 2)
                self.assertEqual(output, '')
                self.assertIn('source-free draft only', error)
                self.unchanged()

    def test_legacy_request_rejects_draft_and_review_flags(self):
        base = ['--request', str(self.draft_path), '--output', str(self.output),
                '--control-profile', CONTROL_PROFILE]
        for extra in (['--draft', str(self.draft_path)], ['--validate'], ['--reviewer', ''],
                      ['--reviewer', 'person'], ['--evidence-note', ''], ['--reviewed'],
                      ['--capture', str(self.image)]):
            with self.subTest(extra=extra):
                status, output, error = self.invoke(base + extra)
                self.assertEqual(status, 2)
                self.assertEqual(output, '')
                self.assertTrue(error)
                self.unchanged()

    def test_missing_empty_directory_capture_never_changes_draft_or_publishes(self):
        for value in ('', str(self.root/'missing.png'), str(self.root)):
            args = self.args(); args[args.index('--capture')+1] = value
            with self.subTest(capture=value):
                status, output, error = self.invoke(args)
                self.assertEqual(status, 2)
                report = json.loads(output)
                self.assertEqual(report['status'], 'needs_review')
                self.assertIs(report['controller_input_sent'], False)
                self.assertTrue(report['reason'])
                self.unchanged()

    def test_missing_receipt_changed_bytes_or_wrong_metadata_reject_without_mutation(self):
        original_image, original_receipt = self.image.read_bytes(), self.receipt_path.read_bytes()
        changes = [lambda: self.receipt_path.unlink(),
                   lambda: self.image.write_bytes(image_fixture.png_bytes((1, 2, 3))),
                   lambda: self.receipt_path.write_text(json.dumps({**self.receipt, 'image_sha256': 'f'*64})),
                   lambda: self.receipt_path.write_text(json.dumps({**self.receipt, 'image_path': str(self.root/'different.png')}))]
        for change in changes:
            self.image.write_bytes(original_image); self.receipt_path.write_bytes(original_receipt)
            change()
            with self.subTest(change=change):
                status, output, error = self.invoke(self.args())
                self.assertEqual(status, 2)
                self.assertIs(json.loads(output)['controller_input_sent'], False)
                self.unchanged()

    def test_stale_original_capture_cannot_be_refreshed_by_new_completed_time(self):
        self.make_capture(datetime.now(timezone.utc)-timedelta(seconds=61))
        self.receipt['capture_completed_at'] = datetime.now(timezone.utc).isoformat()
        self.receipt_path.write_text(json.dumps(self.receipt))
        status, output, error = self.invoke(self.args())
        self.assertEqual(status, 2)
        self.assertIn('stale', json.loads(output)['reason'])
        self.unchanged()

    def test_valid_cli_prepares_exact_original_receipt_identity_and_pointer(self):
        before_image, before_receipt = self.image.read_bytes(), self.receipt_path.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT/'scripts/veda_menu.py'), *self.args()],
                                cwd=self.root, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout), {'request_file': str(self.output)})
        request = json.loads(self.output.read_text())
        self.assertEqual(request['operation'], 'prepare')
        self.assertEqual(request['kind'], 'choice')
        self.assertEqual(request['source']['path'], str(self.image))
        self.assertEqual(request['source']['captured_at'], self.receipt['capture_requested_at'])
        self.assertEqual(request['source']['sha256'], self.receipt['image_sha256'])
        identity = 'reviewed-' + hashlib.sha256((self.receipt['capture_requested_at']+'\0'+self.receipt['image_sha256']).encode()).hexdigest()
        for review in (request['review'], request['observation']['review'], request['choice']['review']):
            self.assertIs(review['complete'], True)
            self.assertEqual(review['frame_id'], identity)
            self.assertEqual(review['image_sha256'], self.receipt['image_sha256'])
            self.assertEqual(review['reviewer'], 'Synthetic inspector')
        self.assertEqual(request['observation']['frame'], {'frame_id':identity,
            'image_sha256':self.receipt['image_sha256'], 'observed_at':self.receipt['capture_requested_at']})
        self.assertEqual(request['choice']['review']['kind'], 'reviewed_choice')
        self.assertEqual(request['review']['kind'], 'reviewed_choice_ui')
        self.assertEqual(self.draft_path.read_bytes(), self.original_draft)
        self.assertEqual(self.image.read_bytes(), before_image)
        self.assertEqual(self.receipt_path.read_bytes(), before_receipt)
        self.assertIs(json.loads(before_receipt)['pixel_content_verified'], False)

    def test_existing_output_source_and_receipt_are_not_overwritten(self):
        self.output.write_bytes(b'prior unrelated file')
        for path in (self.output, self.draft_path, self.image, self.receipt_path):
            before = path.read_bytes()
            args = self.args(); args[args.index('--output')+1] = str(path)
            with self.subTest(path=path.name):
                status, output, error = self.invoke(args)
                self.assertEqual(status, 2)
                self.assertIs(json.loads(output)['controller_input_sent'], False)
                self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.draft_path.read_bytes(), self.original_draft)

    def test_prepared_pointer_flows_through_real_shadow_adapter_without_runtime_access(self):
        status, output, error = self.invoke(self.args())
        self.assertEqual(status, 0, error + output)
        pointer = json.loads(output)
        marker = self.root/'not-a-database'; marker.write_bytes(b'not SQLite')
        class Telemetry:
            def recover(self, *, run_id):
                if run_id != 'synthetic-run': raise AssertionError('wrong run')
                return {'pending': []}
        lines = '\n'.join(json.dumps(value) for value in (pointer,
            {'operation':'cancel_prepared'}, {'operation':'stop'})) + '\n'
        stdin = io.TextIOWrapper(io.BytesIO(lines.encode()))
        stdout = io.StringIO()
        with patch.object(veda_reviewed_play, 'TelemetryDatabase', return_value=object()) as db, \
             patch.object(veda_reviewed_play, 'PlayTelemetry', return_value=Telemetry()), \
             patch.object(veda_reviewed_play, 'BridgeClient', side_effect=AssertionError('no bridge')) as bridge, \
             patch.object(veda_reviewed_play.sys, 'stdin', stdin), redirect_stdout(stdout):
            result = veda_reviewed_play.main([str(self.root/'shadow'), '--run-id', 'synthetic-run',
                '--database', str(marker), '--mode', 'shadow'])
        self.assertEqual(result, 0)
        db.assert_called_once_with(marker)
        bridge.assert_not_called()
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([r['status'] for r in replies],
                         ['ready_unarmed','prepared','cancelled_without_input','stopped'])
        self.assertEqual(replies[1]['command']['buttons'], ['cross'])
        self.assertIs(replies[1]['controller_input_sent'], False)
        state = json.loads((self.root/'shadow/state.json').read_text())
        self.assertIsNone(state['pending'])
        self.assertEqual(state['completed'], 0)
        self.assertEqual(marker.read_bytes(), b'not SQLite')


if __name__ == '__main__':
    unittest.main()
