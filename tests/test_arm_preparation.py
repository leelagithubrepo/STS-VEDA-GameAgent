"""Synthetic preview/acknowledgement tests; no capture or actual controller."""
from contextlib import redirect_stdout
from datetime import timedelta
import hashlib
import io
import json
from unittest import TestCase
from unittest.mock import patch

from scripts.veda_arm import main
import tests.test_play_requests as arm_fixtures
from tests.test_reviewed_play import FakeController, png_bytes
from veda.arm_preparation import (confirm_arm_review, stage_arm_review,
                                  validate_arm_draft)
from veda.play_requests import PlayRequestError
from veda.reviewed_play import ReviewedPlaySession


class ArmPreparationTests(TestCase):
    def setUp(self):
        self.fixture = arm_fixtures.ArmRequestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.draft = {'schema': 'veda.arm-draft.v1', 'run_id': f.kwargs['run_id'],
                      'screen': 'combat', 'reviewer': 'Offline reviewer',
                      'phrase': f.kwargs['phrase'], 'exclusive_client_confirmed': True}
        self.stage_path = f.root / 'stage.json'

    def stage(self):
        self.preview = stage_arm_review(self.draft, capture=self.fixture.image,
            output=self.stage_path, request_output=self.fixture.output, now=self.fixture.now)
        return self.preview

    def confirm(self, **overrides):
        return confirm_arm_review(self.stage_path, **{
            'stage_sha256': self.preview['stage_sha256'], 'run_id': self.draft['run_id'],
            'screen': 'combat', 'note': 'Inspected the exact synthetic combat fixture.',
            'reviewed': True, 'now': self.fixture.now, **overrides})

    def test_unreviewed_preview_is_not_an_arm_request(self):
        self.stage()
        stage = json.loads(self.stage_path.read_text())
        self.assertFalse(stage['review_complete'])
        self.assertNotIn('operation', stage)
        self.assertNotIn('review', stage)
        self.assertFalse(self.fixture.output.exists())
        controller = FakeController()
        from veda.play_telemetry import PlayTelemetry
        from veda.telemetry_database import TelemetryDatabase
        database = TelemetryDatabase(self.fixture.root / 'synthetic.sqlite3')
        run_id = database.start_or_resume_run(ascension=2)
        with ReviewedPlaySession(self.fixture.root / 'session', run_id=run_id,
                telemetry=PlayTelemetry(database), controller_factory=lambda: controller,
                clock=lambda: self.fixture.now) as session:
            with self.assertRaises((ValueError, KeyError)):
                session.handle(stage)
            self.assertFalse(session.armed)
        self.assertEqual(controller.calls, [])

    def test_explicit_confirmation_preserves_original_source_and_has_ordinary_arm_shape(self):
        self.stage()
        self.confirm()
        packet = json.loads(self.fixture.output.read_text())
        self.assertEqual(packet['operation'], 'arm')
        self.assertEqual(packet['source']['captured_at'], self.fixture.receipt['capture_requested_at'])
        self.assertTrue(packet['review']['complete'])
        self.assertEqual(packet['source']['sha256'], self.fixture.receipt['image_sha256'])
        self.assertFalse(json.loads(self.stage_path.read_text())['review_complete'])

    def test_missing_review_or_wrong_run_screen_or_note_cannot_publish(self):
        self.stage()
        for change in ({'reviewed': False}, {'reviewed': 1}, {'run_id': 'different'},
                       {'screen': 'map'}, {'note': ''}, {'stage_sha256': '0' * 64}):
            with self.subTest(change=change), self.assertRaises(PlayRequestError):
                self.confirm(**change)
            self.assertFalse(self.fixture.output.exists())

    def test_changed_stage_draft_or_target_rejected_by_emitted_preview_hash(self):
        self.stage()
        original = self.stage_path.read_bytes()
        for key, value in [('request_output', str(self.fixture.root / 'other.json')),
                           ('draft', {**self.draft, 'run_id': 'different'})]:
            altered = json.loads(original)
            altered[key] = value
            self.stage_path.write_text(json.dumps(altered))
            with self.subTest(key=key), self.assertRaisesRegex(PlayRequestError, 'arm_stage_changed'):
                self.confirm()
            self.assertFalse(self.fixture.output.exists())

    def test_receipt_change_and_image_change_rejected_after_preview(self):
        self.stage()
        receipt = self.fixture.receipt_path.read_bytes()
        self.fixture.receipt_path.write_bytes(receipt + b' ')
        with self.assertRaisesRegex(PlayRequestError, 'arm_receipt_changed'):
            self.confirm()
        self.fixture.receipt_path.write_bytes(receipt)
        self.fixture.image.write_bytes(self.fixture.image.read_bytes() + b'changed')
        with self.assertRaises(PlayRequestError):
            self.confirm()
        self.assertFalse(self.fixture.output.exists())

    def test_preview_and_review_cannot_refresh_original_31_second_capture(self):
        self.stage()
        with self.assertRaisesRegex(PlayRequestError, 'capture_stale'):
            self.confirm(now=self.fixture.captured + timedelta(seconds=31))
        self.assertFalse(self.fixture.output.exists())

    def test_stage_at_31_seconds_is_rejected_without_preview(self):
        with self.assertRaisesRegex(PlayRequestError, 'capture_stale'):
            stage_arm_review(self.draft, capture=self.fixture.image, output=self.stage_path,
                request_output=self.fixture.output, now=self.fixture.captured + timedelta(seconds=31))
        self.assertFalse(self.stage_path.exists())

    def test_existing_outputs_preserved_and_repeat_confirm_rejected(self):
        self.stage()
        self.confirm()
        packet = self.fixture.output.read_bytes()
        with self.assertRaisesRegex(PlayRequestError, 'output_already_exists'):
            self.confirm()
        with self.assertRaisesRegex(PlayRequestError, 'output_already_exists'):
            self.stage()
        self.assertEqual(self.fixture.output.read_bytes(), packet)

    def test_authorization_and_schema_checked_before_capture(self):
        for alteration in ({'phrase': 'yes'}, {'exclusive_client_confirmed': 1},
                           {'reviewed': True}, {'screen': 'not a game screen'}):
            with self.subTest(alteration=alteration), self.assertRaises(PlayRequestError):
                validate_arm_draft({**self.draft, **alteration})

    def test_failed_capture_never_publishes_preview_or_falls_back_to_old_image(self):
        draft_path = self.fixture.root / 'draft.json'
        draft_path.write_text(json.dumps(self.draft))
        with patch('veda.game_capture.capture_game_window', side_effect=ValueError('capture failed')), redirect_stdout(io.StringIO()) as stdout:
            code = main(['stage', '--draft', str(draft_path), '--capture-new',
                '--output', str(self.stage_path), '--request-output', str(self.fixture.output)])
        self.assertEqual(code, 2)
        self.assertIsNone(json.loads(stdout.getvalue())['request_file'])
        self.assertFalse(self.stage_path.exists())
        self.assertFalse(self.fixture.output.exists())

    def test_changed_draft_after_stage_does_not_change_bound_preview(self):
        self.stage()
        self.draft['screen'] = 'map'
        self.draft['run_id'] = 'changed'
        result = self.confirm(run_id=self.fixture.kwargs['run_id'])
        self.assertEqual(json.loads(self.fixture.output.read_text())['screen'], 'combat')
        self.assertEqual(result['request_file'], str(self.fixture.output))

    def test_coherent_source_replacement_at_writer_boundary_never_publishes_unseen_bytes(self):
        from veda.play_requests import write_arm_request
        self.stage()
        def replace_before_write(**kwargs):
            self.fixture.image.write_bytes(png_bytes((90, 50, 10)))
            receipt = dict(self.fixture.receipt)
            receipt['image_sha256'] = hashlib.sha256(self.fixture.image.read_bytes()).hexdigest()
            self.fixture.receipt_path.write_text(json.dumps(receipt))
            return write_arm_request(**kwargs)
        with patch('veda.arm_preparation.write_arm_request', side_effect=replace_before_write):
            with self.assertRaisesRegex(PlayRequestError, 'reviewed_preview_source_changed'):
                self.confirm()
        self.assertFalse(self.fixture.output.exists())
