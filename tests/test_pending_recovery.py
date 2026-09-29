import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from veda.pending_recovery import find_matching_verify_request
from scripts.veda_submit import _pending_recovery


class PendingRecoveryTests(unittest.TestCase):
    def test_finds_only_exact_verify_packet_and_ignores_nested_or_other_actions(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            action = 'action-1'
            (directory / 'state.json').write_text('{}')
            (directory / 'wrong.json').write_text(json.dumps({'operation': 'verify', 'action_id': 'other', 'operation_id': 'x', 'after': {}}))
            (directory / 'prepare.json').write_text(json.dumps({'operation': 'prepare', 'action_id': action}))
            expected = directory / 'result.json'
            expected.write_text(json.dumps({'operation': 'verify', 'action_id': action, 'operation_id': 'x', 'after': {}}))
            self.assertEqual(expected.resolve(), find_matching_verify_request(directory / 'state.json', action))

    def test_missing_packet_never_replays_and_explains_next_step(self):
        result = _pending_recovery('/tmp/no-session', {
            'status': 'recoverable_review', 'armed': False,
            'pending': {'action_id': 'action-1', 'status': 'attempted'},
            'controller_input_sent': True,
        })
        self.assertEqual('pending_reconciliation_required', result['status'])
        self.assertEqual('verify', result['next_operation'])
        self.assertFalse(result['controller_input_sent'])
        self.assertFalse(result['recovery']['input_replayed'])

    def test_exact_packet_is_verified_then_finalized_without_input(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            packet = directory / 'result.json'
            packet.write_text(json.dumps({'operation': 'verify', 'action_id': 'action-1', 'operation_id': 'x', 'after': {}}))
            responses = iter(({'status': 'verified', 'controller_input_sent': False},))
            with patch('scripts.veda_submit.submit', side_effect=lambda *args, **kwargs: next(responses)) as send:
                result = _pending_recovery(directory, {
                    'status': 'recoverable_review',
                    'pending': {'action_id': 'action-1', 'status': 'attempted'},
                })
            self.assertEqual('pending_recovered', result['status'])
            self.assertEqual(1, send.call_count)

    def test_explicit_finalize_request_is_honored_without_replaying_input(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / 'result.json').write_text(json.dumps({'operation': 'verify', 'action_id': 'action-2', 'operation_id': 'x', 'after': {}}))
            responses = iter(({'status': 'verified', 'requires_finalize': True, 'controller_input_sent': False},
                              {'status': 'finalized', 'controller_input_sent': False}))
            with patch('scripts.veda_submit.submit', side_effect=lambda *args, **kwargs: next(responses)) as send:
                result = _pending_recovery(directory, {
                    'status': 'recoverable_review',
                    'pending': {'action_id': 'action-2', 'status': 'attempted'},
                })
            self.assertEqual('pending_recovered', result['status'])
            self.assertEqual(2, send.call_count)

    def test_run_complete_is_preserved_as_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / 'result.json').write_text(json.dumps({'operation': 'verify', 'action_id': 'action-3', 'operation_id': 'x', 'after': {}}))
            with patch('scripts.veda_submit.submit', return_value={'status': 'run_complete', 'reason': 'victory', 'controller_input_sent': False}):
                result = _pending_recovery(directory, {
                    'status': 'recoverable_review', 'armed': False,
                    'pending': {'action_id': 'action-3', 'status': 'attempted'},
                })
            self.assertEqual('run_complete', result['status'])
            self.assertEqual('victory', result['reason'])


if __name__ == '__main__':
    unittest.main()
