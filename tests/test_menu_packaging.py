"""Menu packaging uses synthetic PNG/review facts, no DB or controller.

The real shadow adapter handles the pointer protocol with a read-only telemetry
stub. Review facts are explicit test declarations, not recognition claims.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import veda_menu, veda_reviewed_play
from tests import test_choice_execution as choice_fixture
from tests import test_reviewed_play as image_fixture
from veda.choice_execution import ChoiceError, plan_choice_step
from veda.menu_controls import CONTROL_PROFILE, EVENT_RULE
from veda.reviewed_play import inventory_digest


class MenuPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        self.image = self.root / 'synthetic-menu.png'
        self.image.write_bytes(image_fixture.png_bytes((17, 35, 54)))
        self.inventory = {'coverage': {'relic': 'complete', 'potion': 'complete'},
                          'current': {'relic': [], 'potion': []}}
        obs = choice_fixture.observation(screen='event')
        obs['context'].update(combat_id=None, turn_id=None)
        obs['facts'] = {'event_id': 'neow', 'event_phase': 'reward_options'}
        obs['frame'].update(image_sha256=hashlib.sha256(self.image.read_bytes()).hexdigest(),
                            observed_at=self.now.isoformat())
        obs['review'].update(image_sha256=obs['frame']['image_sha256'])
        obs['inventory_digest'] = inventory_digest(self.inventory)
        obs['ui'].update(menu_family='event_options', control_layout='ps5_default', navigation=[])
        for option in obs['ui']['options']:
            option.pop('activate', None)
        goal = choice_fixture.choice(obs, kind='event')
        goal['postconditions'].update(screen='event')
        goal['postconditions']['facts'] = {'event_phase': 'reward_resolved'}
        goal['postconditions']['allow_changed_facts'] = []
        self.request = {'operation': 'prepare', 'kind': 'choice',
            'context': deepcopy(obs['context']), 'inventory': deepcopy(self.inventory),
            'observation': obs, 'review': deepcopy(obs['review']), 'choice': goal,
            'source': {'path': str(self.image), 'sha256': obs['frame']['image_sha256'],
                       'captured_at': obs['frame']['observed_at'], 'origin': 'reviewer',
                       'evidence_note': 'Synthetic solid PNG; facts explicitly declared by test.'},
            'reasoning': 'Synthetic event option chosen for a packaging contract test.'}
        self.input = self.root / 'input.json'
        self.output = self.root / 'output.json'

    def write_request(self, request=None):
        self.input.write_text(json.dumps(self.request if request is None else request))

    def package(self):
        self.write_request()
        return veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)

    def test_preserves_review_source_and_semantics_adds_only_scoped_controls(self):
        original = deepcopy(self.request)
        result = self.package()
        self.assertEqual(result, {'request_file': str(self.output.resolve())})
        packaged = json.loads(self.output.read_text())
        for field in ('context', 'inventory', 'review', 'choice', 'source', 'reasoning'):
            self.assertEqual(packaged[field], original[field])
        proof = packaged['observation']['ui']['options'][0]['activate']['evidence']
        self.assertEqual(proof['kind'], 'documented_control_profile')
        self.assertEqual(proof['rule_id'], EVENT_RULE)
        self.assertEqual(proof['image_sha256'], original['source']['sha256'])
        self.assertEqual(proof['frame_id'], original['observation']['frame']['frame_id'])
        for fake_observation in ('hint_text', 'reference_id', 'before_sha256', 'after_sha256'):
            self.assertNotIn(fake_observation, proof)
        self.assertEqual(packaged['observation']['ui']['navigation'], [])
        for option in packaged['observation']['ui']['options']:
            self.assertEqual(option['activate']['evidence']['rule_id'], EVENT_RULE)
            self.assertEqual(option['activate']['evidence']['meaning'], 'activate:' + option['id'])
        restored = deepcopy(packaged)
        for option in restored['observation']['ui']['options']:
            option.pop('activate')
        self.assertEqual(restored, original)
        self.assertEqual(json.loads(self.input.read_text()), original)
        planned = plan_choice_step(packaged['observation'], packaged['choice'], now=self.now)
        self.assertEqual(planned['command']['buttons'], ['cross'])

    def test_source_context_inventory_and_review_mismatches_reject_before_output(self):
        mutations = [
            lambda r: r['source'].update(sha256='f' * 64),
            lambda r: r['source'].update(captured_at=(self.now-timedelta(seconds=1)).isoformat()),
            lambda r: r['context'].update(run_id='other-run'),
            lambda r: r['inventory']['current'].update(potion=['Block Potion']),
            lambda r: r['review'].update(reviewer='somebody else'),
            lambda r: r['observation']['review'].update(complete=False),
        ]
        for mutate in mutations:
            request = deepcopy(self.request)
            mutate(request)
            self.write_request(request)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
            self.assertFalse(self.output.exists())

    def test_stale_future_and_naive_capture_rejected(self):
        for timestamp in ((self.now-timedelta(seconds=31)).isoformat(),
                          (self.now+timedelta(seconds=1)).isoformat(),
                          self.now.replace(tzinfo=None).isoformat()):
            request = deepcopy(self.request)
            request['source']['captured_at'] = timestamp
            request['observation']['frame']['observed_at'] = timestamp
            self.write_request(request)
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
            self.assertFalse(self.output.exists())

    def test_changed_image_bytes_rejected(self):
        self.image.write_bytes(image_fixture.png_bytes((1, 2, 3)))
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_source_rechecked_after_binding(self):
        original_bind = veda_menu.bind_reviewed_menu_controls
        def changed_during_binding(*args, **kwargs):
            result = original_bind(*args, **kwargs)
            self.image.write_bytes(image_fixture.png_bytes((4, 5, 6)))
            return result
        with patch.object(veda_menu, 'bind_reviewed_menu_controls', side_effect=changed_during_binding):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                self.package()
        self.assertFalse(self.output.exists())

    def test_does_not_overwrite_existing_output_request_or_image(self):
        self.write_request()
        self.output.write_bytes(b'existing unrelated file')
        for destination in (self.output, self.input, self.image):
            before = destination.read_bytes()
            with self.subTest(path=destination.name), self.assertRaises(FileExistsError):
                veda_menu.package(self.input, destination, control_profile=CONTROL_PROFILE, now=self.now)
            self.assertEqual(destination.read_bytes(), before)

    def test_existing_symlink_target_not_overwritten(self):
        target = self.root / 'existing.txt'
        target.write_bytes(b'private existing bytes')
        self.output.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.package()
        self.assertEqual(target.read_bytes(), b'private existing bytes')
        self.assertTrue(self.output.is_symlink())

    def test_unreviewed_controls_profile_and_opening_talk_are_not_assumed(self):
        bad = deepcopy(self.request)
        bad['observation']['facts']['event_phase'] = 'opening_dialogue'
        self.write_request(bad)
        with self.assertRaisesRegex(ChoiceError, 'narrower opening'):
            veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
        self.write_request()
        with self.assertRaises(ChoiceError):
            veda_menu.package(self.input, self.output, control_profile='invented-profile', now=self.now)
        bad = deepcopy(self.request)
        bad['choice']['option_ids'] = ['2']
        self.write_request(bad)
        with self.assertRaisesRegex(ChoiceError, 'no reviewed path'):
            veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
        self.assertFalse(self.output.exists())

    def test_incomplete_review_rejected_even_when_both_envelopes_agree(self):
        self.request['review']['complete'] = False
        self.request['observation']['review']['complete'] = False
        self.request['choice']['review']['complete'] = False
        with self.assertRaisesRegex(ChoiceError, 'complete review'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_oversized_request_rejected_before_output(self):
        self.request['unused'] = 'x' * 256001
        with self.assertRaisesRegex(ValueError, 'byte bound'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_request_input_read_is_bounded_before_parsing(self):
        self.write_request()
        original_open = Path.open
        calls = []
        class BoundedInput(io.BytesIO):
            def read(inner, size=-1):
                calls.append(size)
                self.assertGreater(size, 0, 'unbounded input read')
                self.assertLessEqual(size, 256001)
                return super().read(size)
        def opening(path, mode='r', *args, **kwargs):
            if path == self.input and mode == 'rb':
                return BoundedInput(b' ' * 256001)
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', opening), self.assertRaisesRegex(ValueError, 'byte bound'):
            veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
        self.assertEqual(calls, [256001])
        self.assertFalse(self.output.exists())

    def test_numeric_overflow_rejected_before_creating_output(self):
        self.write_request()
        text = self.input.read_text()
        self.input.write_text(text[:-1] + ', "unused": 1e999}')
        with self.assertRaises(ValueError):
            veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
        self.assertFalse(self.output.exists())

    def test_preserves_existing_reviewed_binding_without_relabeling_it(self):
        supplied = choice_fixture.binding('cross', 'activate:0')
        self.request['observation']['ui']['options'][0]['activate'] = deepcopy(supplied)
        self.package()
        packaged = json.loads(self.output.read_text())
        self.assertEqual(packaged['observation']['ui']['options'][0]['activate'], supplied)
        self.assertEqual(packaged['observation']['ui']['options'][0]['activate']['evidence']['kind'],
                         'reviewed_transition')

    def test_nonfinite_request_does_not_leave_a_partial_output(self):
        self.request['unused'] = float('nan')
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse(self.output.exists())

    def test_duplicate_review_key_rejected(self):
        self.write_request()
        text = self.input.read_text().replace('"complete": true', '"complete": false, "complete": true', 1)
        self.input.write_text(text)
        with self.assertRaises(ValueError):
            veda_menu.package(self.input, self.output, control_profile=CONTROL_PROFILE, now=self.now)
        self.assertFalse(self.output.exists())

    def test_cli_returns_exact_pointer_and_errors_do_not_dispatch(self):
        self.write_request()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = veda_menu.main(['--request', str(self.input), '--output', str(self.output),
                                     '--control-profile', CONTROL_PROFILE])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {'request_file': str(self.output.resolve())})
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = veda_menu.main(['--request', str(self.input), '--output', str(self.output),
                                     '--control-profile', CONTROL_PROFILE])
        self.assertEqual(status, 2)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result['status'], 'needs_review')
        self.assertIs(result['controller_input_sent'], False)

    def test_packaged_pointer_enters_real_shadow_adapter_without_db_or_bridge(self):
        pointer = self.package()
        database_marker = self.root / 'not-a-database'
        database_marker.write_bytes(b'Only satisfies CLI is_file; database constructor is mocked.')
        sentinel = object()
        class ReadOnlyTelemetry:
            def recover(self, *, run_id):
                if run_id != 'run':
                    raise AssertionError('wrong run')
                return {'pending': []}
        lines = '\n'.join(json.dumps(item) for item in (
            pointer, {'operation': 'cancel_prepared'}, {'operation': 'stop'})) + '\n'
        stdin = io.TextIOWrapper(io.BytesIO(lines.encode()))
        stdout = io.StringIO()
        with patch.object(veda_reviewed_play, 'TelemetryDatabase', return_value=sentinel) as db_factory, \
             patch.object(veda_reviewed_play, 'PlayTelemetry', return_value=ReadOnlyTelemetry()) as telemetry_factory, \
             patch.object(veda_reviewed_play, 'BridgeClient', side_effect=AssertionError('no bridge')) as bridge, \
             patch.object(veda_reviewed_play.sys, 'stdin', stdin), redirect_stdout(stdout):
            status = veda_reviewed_play.main([str(self.root / 'shadow'), '--run-id', 'run',
                '--database', str(database_marker), '--mode', 'shadow'])
        self.assertEqual(status, 0)
        db_factory.assert_called_once_with(database_marker.resolve())
        telemetry_factory.assert_called_once_with(sentinel)
        bridge.assert_not_called()
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([r['status'] for r in responses],
                         ['ready_unarmed', 'prepared', 'cancelled_without_input', 'stopped'])
        self.assertIs(responses[1]['controller_input_sent'], False)
        self.assertEqual(responses[1]['command']['buttons'], ['cross'])
        saved = json.loads((self.root / 'shadow' / 'state.json').read_text())
        self.assertIsNone(saved['pending'])
        self.assertEqual(saved['completed'], 0)
        self.assertEqual(database_marker.read_bytes(), b'Only satisfies CLI is_file; database constructor is mocked.')


if __name__ == '__main__':
    unittest.main()
