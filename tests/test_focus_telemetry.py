"""Resolve a delivered focus probe without inventing card or hand progress."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import unittest

from tests import test_play_telemetry as telemetry_fixture
from veda.play_telemetry import outcome_request_digest


class FocusTelemetryTests(unittest.TestCase):
    def fixture(self, *, legacy=False):
        f = telemetry_fixture.PlayTelemetryTests(); f.setUp(); self.addCleanup(f.doCleanups)
        focus = {'domain': 'potion', 'tooltip_kind': 'potion', 'subject_id': 'energy-potion',
                 'focused_card_id': None, 'selected_card_id': None}
        f.request['action'] = {'kind': 'navigation', 'expected': {'kind': 'clear' if legacy else 'focus_probe'}}
        f.request['state'] = {'hp': 79, 'energy': 3, 'hand': ['Uppercut'], 'observed_at': f.now.isoformat()}
        receipt = f.prepare()
        after = f.outcome(receipt['decision_id'], state=dict(f.request['state'],
            observed_at=(f.now + timedelta(seconds=1)).isoformat()))
        Path(after['source']['path']).write_bytes(Path(f.request['source']['path']).read_bytes())
        after['source']['sha256'] = f.request['source']['sha256']
        prior = dict(focus, domain='unknown', tooltip_kind='unknown', subject_id=None) if legacy else deepcopy(focus)
        proof = {'basis': 'fresh_verified_result', 'action_id': f.request['operation_id'],
                 'outcome_sha256': outcome_request_digest(after), 'source': deepcopy(after['source']),
                 'reviewed_at': (f.now + timedelta(seconds=1)).isoformat(),
                 'review': {'reviewer': 'Fixture reviewer', 'frame_id': 'independent-after',
                            'complete': True, 'image_sha256': after['source']['sha256']},
                 'focus_transition': {'before': prior, 'after': focus,
                                      'effect': 'focus_observed' if legacy else 'unchanged',
                                      'returned_to_hand': False}}
        return f, after, proof

    def resolve(self, f, after, proof):
        return f.api.record_outcome(after, verified_evidence=proof, now=f.now + timedelta(seconds=2))

    def test_identical_later_capture_logs_no_progress_and_does_not_move_cards(self):
        f, after, proof = self.fixture()
        receipt = self.resolve(f, after, proof)
        self.assertEqual('verified', receipt['status'])
        self.assertEqual(0, f.table_count('combat_zone_events'))
        self.assertEqual(0, f.table_count('inventory_events'))
        self.assertEqual(receipt['decision_id'], self.resolve(f, after, proof)['decision_id'])

    def test_legacy_unknown_focus_can_be_observed_without_claiming_movement(self):
        f, after, proof = self.fixture(legacy=True)
        self.assertEqual('verified', self.resolve(f, after, proof)['status'])

    def test_same_pixels_cannot_establish_a_card_or_return_to_hand(self):
        f, after, proof = self.fixture()
        proof['focus_transition']['returned_to_hand'] = True
        with self.assertRaisesRegex(ValueError, 'unchanged source bytes'):
            self.resolve(f, after, proof)
        self.assertEqual('recommended', f.status(after['decision_id']))

    def test_equal_pixels_cannot_hide_changed_hp(self):
        f, after, proof = self.fixture()
        after['state']['hp'] = 80
        proof['outcome_sha256'] = outcome_request_digest(after)
        with self.assertRaisesRegex(ValueError, 'unchanged source bytes'):
            self.resolve(f, after, proof)

    def test_missing_durable_focus_review_keeps_input_pending(self):
        f, after, proof = self.fixture()
        proof.pop('focus_transition')
        with self.assertRaisesRegex(ValueError, 'unchanged source bytes'):
            self.resolve(f, after, proof)
        self.assertEqual('recommended', f.status(after['decision_id']))

    def test_same_source_path_is_not_a_second_observation(self):
        f, after, proof = self.fixture()
        after['source']['path'] = f.request['source']['path']
        proof['source'] = deepcopy(after['source'])
        proof['outcome_sha256'] = outcome_request_digest(after)
        with self.assertRaisesRegex(ValueError, 'unchanged source bytes'):
            self.resolve(f, after, proof)


if __name__ == '__main__':
    unittest.main()
