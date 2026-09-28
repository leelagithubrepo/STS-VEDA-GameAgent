"""Launch diagnostics cannot become an extra gameplay gate; mocked exec only."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import SourceFileLoader
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from veda.play_timing import PlayTiming


class OrchestratorLauncherTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = str(uuid4())
        self.path = self.root / 'artifacts' / 'reviewed-play' / self.run / 'timing.json'
        source = Path(__file__).resolve().parents[1] / 'scripts' / 'orchestrator'
        loader = SourceFileLoader('isolated_orchestrator_launcher_timing', str(source))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.module = importlib.util.module_from_spec(spec)
        loader.exec_module(self.module)
        self.module.__file__ = str(self.root / 'scripts' / 'orchestrator')

    def launch(self, *, explicit=True, print_only=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ['orchestrator']
        if explicit:
            argv += ['--run-id', self.run]
        if print_only:
            argv.append('--print-command')
        with (patch.object(self.module.sys, 'argv', argv),
              patch.object(self.module, '_nested_launch_reason', return_value=None),
              patch.object(self.module.shutil, 'which', return_value='/synthetic/codex'),
              patch.object(self.module.os, 'execv') as execute,
              redirect_stdout(stdout), redirect_stderr(stderr)):
            result = self.module.main()
        return execute, stdout.getvalue(), stderr.getvalue(), result

    def assert_launched(self, execute):
        execute.assert_called_once()
        executable, command = execute.call_args.args
        self.assertEqual(executable, '/synthetic/codex')
        self.assertIn('gpt-5.6-luna', command)
        self.assertIn('before arming the reviewed adapter', command[-1])
        self.assertIn('Never invent observations, replay unresolved input', command[-1])

    def test_corrupt_clock_reports_warning_preserves_file_and_still_launches(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{corrupt timing JSON')
        original = self.path.read_bytes()
        execute, _, stderr, _ = self.launch()
        self.assert_launched(execute)
        self.assertIn('Startup timing unavailable', stderr)
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_lock_is_recreated_without_losing_existing_session_or_blocking_launch(self):
        clock = PlayTiming(self.path, run_id=self.run)
        clock.event('begin_move', move_id='pending-card', kind='combat_card')
        original = clock.snapshot()
        self.path.with_name('timing.json.lock').unlink()
        execute, _, stderr, _ = self.launch()
        self.assert_launched(execute)
        result = clock.snapshot()
        self.assertEqual(result['session_id'], original['session_id'])
        self.assertEqual(result['move']['id'], 'pending-card')
        self.assertEqual(result['move']['started_at'], original['move']['started_at'])
        self.assertEqual(stderr, '')

    def test_invalid_sidecar_fields_and_foreign_run_do_not_reset_or_prevent_exec(self):
        for value in ({'schema': 'veda.play-timing.v1', 'run_id': self.run, 'session_id': 'session'},
                      {'schema': 'veda.play-timing.v1', 'run_id': str(uuid4()), 'session_id': 'session'}):
            with self.subTest(value=value):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(value))
                original = self.path.read_bytes()
                execute, _, stderr, _ = self.launch()
                self.assert_launched(execute)
                self.assertIn('Startup timing unavailable', stderr)
                self.assertEqual(self.path.read_bytes(), original)

    def test_lock_access_failure_reports_diagnostic_and_still_launches(self):
        clock = PlayTiming(self.path, run_id=self.run)
        original = self.path.read_bytes()
        with patch.object(PlayTiming, '_lock', side_effect=PermissionError('synthetic timing lock unavailable')):
            execute, _, stderr, _ = self.launch()
        self.assert_launched(execute)
        self.assertIn('PermissionError', stderr)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(clock.snapshot()['run_id'], self.run)

    def test_database_open_and_selection_errors_do_not_create_fallback_run_or_block_launch(self):
        for error in (sqlite3.OperationalError('synthetic database unavailable'), ValueError('invalid sidecar'),
                      TypeError('invalid selected run record')):
            with self.subTest(error=error), patch('veda.play_context.read_play_context', side_effect=error):
                execute, _, stderr, _ = self.launch(explicit=False)
            self.assert_launched(execute)
            self.assertIn('recorded run selection failed', stderr)
            self.assertFalse((self.root / 'artifacts').exists())

    def test_ambiguous_or_unsafe_recorded_run_keeps_live_identity_unresolved(self):
        for recorded in ({'selection_status': 'needs_review'},
                         {'selection_status': 'selected', 'run': {'id': '../../invented-run'}}):
            with self.subTest(recorded=recorded), patch('veda.play_context.read_play_context', return_value=recorded):
                execute, _, stderr, _ = self.launch(explicit=False)
            self.assert_launched(execute)
            self.assertIn('Startup timing unavailable', stderr)
            self.assertNotIn('Resume only run', execute.call_args.args[1][-1])
            self.assertFalse((self.root / 'artifacts').exists())

    def test_print_command_is_side_effect_free_even_with_broken_diagnostic_services(self):
        with (patch('veda.play_context.read_play_context', side_effect=AssertionError('database read')),
              patch('veda.play_timing.PlayTiming', side_effect=AssertionError('timer creation'))):
            execute, stdout, stderr, result = self.launch(explicit=False, print_only=True)
        execute.assert_not_called()
        self.assertEqual(result, 0)
        self.assertIn('gpt-5.6-luna', stdout)
        self.assertEqual(stderr, '')
        self.assertFalse((self.root / 'artifacts').exists())


if __name__ == '__main__':
    unittest.main()
