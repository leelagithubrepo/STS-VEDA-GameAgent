import copy
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_execution import contract_manifest, reading
from veda.calibration import CalibrationReport
from veda.execution import ActionJournal, ARM_PHRASE, ExecutionLoop, RUNTIME_FIELDS, RuntimeStop
from veda.execution_adapters import RecordedInterpreter, ReplayController, ReplaySource


class RuntimeGuardTests(unittest.TestCase):
    def run_fixture(self, directory, manifest, *, journal=None, controller=None, **options):
        source = ReplaySource(manifest, Path(directory))
        controller = controller or ReplayController(manifest['expected_commands'])
        loop = ExecutionLoop(source, RecordedInterpreter(source), controller,
            calibration=CalibrationReport({key: 1.0 for key in RUNTIME_FIELDS}, 12),
            run_id=manifest['run_id'], max_inputs=3, journal=journal, **options)
        return loop.run(), controller

    def test_stop_requested_during_journal_flush_prevents_send(self):
        class StoppingJournal(ActionJournal):
            stopped = False
            def append(self, **row):
                super().append(**row)
                if row['status'] == 'attempted':
                    self.stopped = True
        with TemporaryDirectory() as directory:
            journal = StoppingJournal()
            result, controller = self.run_fixture(directory, contract_manifest(directory),
                journal=journal, should_stop=lambda: journal.stopped)
            self.assertEqual(controller.commands, [])
            self.assertEqual(result['unresolved_actions'], [])
            self.assertIn('before send', result['reason'])

    def test_validated_card_resolution_rejects_unexpected_hp_loss(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            final = manifest['frames'][-1]['reading']
            final['state']['hp'] -= 1
            final['context']['state']['hp'] -= 1
            result, _ = self.run_fixture(directory, manifest)
            self.assertIn('HP differs', result['reason'])
            self.assertEqual(len(result['unresolved_actions']), 1)

    def test_cleanup_failure_preserves_original_reason(self):
        class BrokenCleanup(ReplayController):
            def close(self):
                raise OSError('fake shutdown error')
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            manifest['frames'][1]['reading']['ui']['focused_card_id'] = 'd'
            result, _ = self.run_fixture(directory, manifest,
                controller=BrokenCleanup(manifest['expected_commands']))
            self.assertIn('mismatch', result['reason'])
            self.assertEqual(result['cleanup_errors'], ['BrokenCleanup: OSError'])

    def test_input_cycles_are_measured_and_confirmed_state_is_retained(self):
        with TemporaryDirectory() as directory:
            journal = ActionJournal(Path(directory) / 'actions.jsonl')
            result, _ = self.run_fixture(directory, contract_manifest(directory), journal=journal)
            self.assertEqual(result['metrics']['stages']['input_verification']['completed_samples'], 3)
            verified = [row for row in journal.rows if row['status'] == 'verified']
            self.assertEqual(verified[-1]['reading']['context']['state']['energy'], 0)
            self.assertEqual(verified[-1]['reading']['context']['state']['enemies'][0]['hp'], 6)

    def test_end_turn_needs_observable_change_not_only_new_id(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            first = copy.deepcopy(manifest['frames'][0]['reading'])
            first['context']['state']['energy'] = 0
            first = reading(first['context'], {**first['ui'], 'focused_card_id': None})
            second = copy.deepcopy(first)
            second['turn_id'] = 'turn-2'
            manifest['frames'][0]['reading'] = first
            manifest['frames'][1]['reading'] = second
            manifest['expected_commands'] = [['triangle']]
            result, controller = self.run_fixture(directory, manifest)
            self.assertEqual(len(controller.commands), 1)
            self.assertIn('End Turn transition not verified', result['reason'])

    def test_nonfinite_limits_reject_before_adapter_use(self):
        for value in (float('nan'), float('inf'), -1, 0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ExecutionLoop(None, None, None, run_id='x',
                    calibration=CalibrationReport({}, 0), max_seconds=value)

    def test_live_constructor_requires_persistent_journal_before_adapter_use(self):
        from test_runtime_authorization import authorize, identity, synthetic_report
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            authorization = authorize(synthetic_report(reader), reader)
            options = dict(mode='live', arm=ARM_PHRASE, run_id='synthetic-only',
                calibration=authorization.calibration, authorization=authorization,
                evidence_directory=Path(directory) / 'evidence')
            source = SimpleNamespace(provenance='live_capture')
            interpreter = SimpleNamespace(command=reader['command'], recognizer_identity=reader)
            for journal in (None, ActionJournal()):
                with self.subTest(journal=journal), self.assertRaisesRegex(RuntimeStop, 'persistent action journal'):
                    ExecutionLoop(source, interpreter, object(), journal=journal, **options)
            journal = ActionJournal(Path(directory) / 'journal.jsonl')
            loop = ExecutionLoop(source, interpreter, object(), journal=journal, **options)
            self.assertIs(loop.journal, journal)
            self.assertFalse(journal.path.exists())

    def test_journal_directory_sync_failure_stops_before_input(self):
        real_fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('synthetic journal directory sync failure')
            return real_fsync(fd)
        with TemporaryDirectory() as directory:
            journal = ActionJournal(Path(directory) / 'new' / 'nested' / 'actions.jsonl')
            manifest = contract_manifest(directory)
            with patch('veda.execution.os.fsync', side_effect=fail_directory):
                result, controller = self.run_fixture(directory, manifest, journal=journal)
            self.assertEqual(controller.commands, [])
            self.assertIn('journal directory sync failure', result['reason'])
            self.assertFalse(journal._directory_synced)
            # The possibly persisted attempted row is conservative on restart.
            self.assertEqual(len(ActionJournal(journal.path).unresolved()), 1)

    def test_first_journal_append_syncs_new_directory_entries_once(self):
        real_fsync = os.fsync
        synced = []
        def record_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                synced.append(os.fstat(fd).st_ino)
            return real_fsync(fd)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'new' / 'nested' / 'actions.jsonl'
            journal = ActionJournal(path)
            with patch('veda.execution.os.fsync', side_effect=record_sync):
                journal.append(status='attempted', action_id='synthetic')
                first = list(synced)
                journal.append(status='not_sent', action_id='synthetic')
            self.assertEqual(set(first), {p.stat().st_ino for p in (path.parent, path.parent.parent, Path(directory))})
            self.assertEqual(synced, first)
            self.assertEqual(ActionJournal(path).unresolved(), [])


if __name__ == '__main__':
    unittest.main()
