"""Atomic reviewed execution and timing integration; fake controller only."""
from copy import deepcopy
from datetime import timedelta
import unittest

from tests import test_reviewed_play as fixtures
from veda.reviewed_play import RuntimeStop


class PlayHotPathTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReviewedPlayTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.session = self.f.create()

    def execute(self):
        return self.session.handle(dict(self.f.before, operation='execute'))

    def test_execute_requires_arm_without_creating_pending(self):
        with self.assertRaisesRegex(RuntimeStop, 'already armed'):
            self.execute()
        self.assertIsNone(self.session.state['pending'])
        self.assertEqual(self.f.controller.inputs, [])

    def test_execute_sends_one_input_and_still_requires_observed_verification(self):
        self.f.arm()
        sent = self.execute()
        self.assertEqual(sent['status'], 'awaiting_fresh_review')
        self.assertEqual(len(self.f.controller.inputs), 1)
        self.assertFalse(sent['game_outcome_verified'])
        result = self.f.verify_navigation(sent)
        self.assertEqual(result['status'], 'verified')
        self.assertFalse(result['logical_action_complete'])
        self.assertEqual(len(self.f.controller.inputs), 1)

    def test_execute_does_not_replay_pending_input(self):
        self.f.arm()
        self.execute()
        with self.assertRaisesRegex(RuntimeStop, 'pending'):
            self.execute()
        self.assertEqual(len(self.f.controller.inputs), 1)
        self.assertEqual(self.session.state['pending']['status'], 'attempted')

    def test_execute_preserves_strategy_checks(self):
        self.f.arm()
        request = deepcopy(self.f.before)
        request['operation'] = 'execute'
        request['plan']['steps'][0]['card_id'] = 'card-not-in-hand'
        with self.assertRaises(RuntimeStop):
            self.session.handle(request)
        self.assertEqual(self.f.controller.inputs, [])

    def test_execute_preserves_freshness(self):
        self.f.arm()
        self.f.now += timedelta(seconds=31)
        with self.assertRaisesRegex(RuntimeStop, 'stale'):
            self.execute()
        self.assertEqual(self.f.controller.inputs, [])


if __name__ == '__main__':
    unittest.main()
