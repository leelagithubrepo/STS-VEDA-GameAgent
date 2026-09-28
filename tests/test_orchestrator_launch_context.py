"""Nested launcher protection with synthetic process trees and mocked exec only."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import SourceFileLoader
import importlib.util
import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class OrchestratorLaunchContextTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'scripts' / 'orchestrator'
        loader = SourceFileLoader('isolated_orchestrator_launch_context', str(source))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.module = importlib.util.module_from_spec(spec)
        loader.exec_module(self.module)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.module.__file__ = str(self.root / 'scripts' / 'orchestrator')
        self.markers = {'CODEX_SHELL': '1', 'CODEX_CI': '1', 'CODEX_THREAD_ID': 'synthetic-thread'}

    def reason(self, processes, environment=None):
        with (patch.dict(self.module.os.environ, environment or {}, clear=True),
              patch.object(self.module.os, 'getppid', return_value=10),
              patch.object(self.module, '_parent_process', side_effect=processes)):
            return self.module._nested_launch_reason()

    def test_actual_codex_ancestor_detected_through_shell_even_without_env_markers(self):
        self.assertEqual('codex_parent_process', self.reason([
            (20, '/bin/zsh'), (30, '/Applications/ChatGPT.app/Contents/Resources/codex')]))
        self.assertEqual('codex_parent_process', self.reason([(20, '/usr/local/bin/codex')]))

    def test_normal_terminal_or_app_boundary_is_allowed_despite_inherited_codex_environment(self):
        for host in ('/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal',
                     '/Applications/iTerm.app/Contents/MacOS/iTerm2', '/usr/bin/login',
                     '/usr/bin/tmux', '/Applications/Codex.app/Contents/MacOS/Codex',
                     '/Applications/ChatGPT.app/Contents/MacOS/ChatGPT'):
            with self.subTest(host=host):
                self.assertIsNone(self.reason([(20, '/bin/zsh'), (30, host)], self.markers))

    def test_denied_process_probe_needs_combined_execution_markers_not_thread_id_alone(self):
        for error in (PermissionError('synthetic process restriction'), subprocess.TimeoutExpired('ps', .2)):
            self.assertEqual('codex_execution_shell', self.reason(error, self.markers))
            for env in ({}, {'CODEX_THREAD_ID': 'inherited'}, {'CODEX_SHELL': '1'},
                        {'CODEX_CI': '1', 'CODEX_THREAD_ID': 'inherited'}):
                with self.subTest(env=env):
                    self.assertIsNone(self.reason(error, env))

    def test_process_walk_is_bounded_and_does_not_treat_codex_named_directory_as_cli(self):
        self.assertIsNone(self.reason([(20, '/projects/codex/zsh'), (1, '/usr/bin/login')]))
        with (patch.dict(self.module.os.environ, {}, clear=True),
              patch.object(self.module.os, 'getppid', return_value=10),
              patch.object(self.module, '_parent_process', side_effect=[(n + 11, '/bin/zsh') for n in range(6)]) as probe):
            self.assertIsNone(self.module._nested_launch_reason())
        self.assertEqual(6, probe.call_count)

    def test_rejected_nested_launch_never_reads_database_writes_clock_or_executes(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(self.module.sys, 'argv', ['orchestrator']),
              patch.object(self.module, '_nested_launch_reason', return_value='codex_parent_process'),
              patch('veda.play_context.read_play_context', side_effect=AssertionError('database read')),
              patch('veda.play_timing.PlayTiming', side_effect=AssertionError('timer write')),
              patch.object(self.module.shutil, 'which', side_effect=AssertionError('launch lookup')),
              patch.object(self.module.os, 'execv') as execute,
              redirect_stdout(stdout), redirect_stderr(stderr)):
            self.assertEqual(2, self.module.main())
        execute.assert_not_called()
        self.assertEqual('', stdout.getvalue())
        self.assertIn('No second session was started', stderr.getvalue())
        self.assertIn('› prompt is Codex', stderr.getvalue())
        self.assertIn('new Terminal tab', stderr.getvalue())
        self.assertFalse((self.root / 'artifacts').exists())

    def test_normal_shell_exec_keeps_luna_high_and_learning_prompt(self):
        with (patch.object(self.module.sys, 'argv', ['orchestrator']),
              patch.object(self.module, '_nested_launch_reason', return_value=None),
              patch('veda.play_context.read_play_context', return_value={'selection_status': 'needs_review'}),
              patch.object(self.module.shutil, 'which', return_value='/synthetic/codex'),
              patch.object(self.module.os, 'execv') as execute,
              redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
            self.module.main()
        execute.assert_called_once()
        executable, command = execute.call_args.args
        self.assertEqual('/synthetic/codex', executable)
        self.assertIn('gpt-5.6-luna', command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn('--decision-policy learning', command[-1])

    def test_print_command_skips_context_probe_and_all_launch_side_effects(self):
        output = io.StringIO()
        with (patch.object(self.module.sys, 'argv', ['orchestrator', '--print-command']),
              patch.object(self.module, '_nested_launch_reason', side_effect=AssertionError('process inspection')),
              patch.object(self.module.shutil, 'which', side_effect=AssertionError('launch lookup')),
              patch('veda.play_context.read_play_context', side_effect=AssertionError('database read')),
              patch.object(self.module.os, 'execv') as execute, redirect_stdout(output)):
            self.assertEqual(0, self.module.main())
        execute.assert_not_called()
        self.assertIn('gpt-5.6-luna', output.getvalue())
        self.assertFalse((self.root / 'artifacts').exists())


if __name__ == '__main__':
    unittest.main()
