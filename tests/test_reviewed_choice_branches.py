"""Alternative outcome integration through real temporary telemetry and fake input."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests import test_reviewed_play
from veda.reviewed_play import RuntimeStop, inventory_digest


class ReviewedChoiceBranchTests(unittest.TestCase):
    def setUp(self):
        self.f = test_reviewed_play.ReviewedPlayTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        f.db.complete_combat_turn(turn_id=f.context_ids['turn_id'], closing_state={})
        f.db.complete_combat(combat_id=f.context_ids['combat_id'], outcome='victory', closing_state={})
        f.context_ids.update(combat_id=None, turn_id=None)
        f.base_time = datetime.now(timezone.utc)
        f.now = f.base_time
        f.before = f.choice_request(0)
        f.before['observation']['ui']['screen'] = 'event'
        f.before['choice']['kind'] = 'event'
        first = deepcopy(f.before['choice']['postconditions'])
        first.update(screen='event', facts={'stage': 'dialogue-finished'})
        second = deepcopy(first)
        second.update(screen='reward', facts={'stage': 'reward-revealed'})
        f.before['choice']['postconditions'] = {'alternatives': [
            {'id': 'dialogue', 'postconditions': first}, {'id': 'reward', 'postconditions': second}]}
        f.create()
        f.session.handle(dict(f.arm_request(), screen='event'))

    def result(self, prepared, branch, *, mixed=False):
        f = self.f
        # Construct a separately reviewed result without mutating the durable
        # pending request, its proposal or either declared branch.
        before = f.session.state['pending']['request']
        original = before['choice']['postconditions']
        post = original['alternatives'][branch]['postconditions']
        after = f.choice_request(1)
        after.pop('choice')
        obs = after['observation']
        after['context'] = deepcopy(post['context'])
        obs['context'] = deepcopy(post['context'])
        obs['resources'] = {key: value if type(value) is int else value['min']
                            for key, value in post['resources'].items()}
        obs['facts'] = dict(deepcopy(before['observation']['facts']), **deepcopy(post['facts']))
        obs['ui'].update(screen=post['screen'], phase=post['phase'], choice_id='result-1', options=[], order=[],
            focused_id=None, selected_ids=[], pending_ids=[], required_count=0, navigation=[])
        obs['review']['outcome'] = {'action_id': prepared['action_id'],
            'before_frame_id': before['observation']['frame']['frame_id'],
            'before_sha256': before['source']['sha256'], 'choice_id': before['choice']['choice_id'],
            'option_ids': before['choice']['option_ids'], 'observed_result': 'Synthetic source-reviewed outcome.'}
        if mixed:
            obs['facts']['stage'] = original['alternatives'][1-branch]['postconditions']['facts']['stage']
        return after

    def send_one(self):
        f = self.f
        prepared = f.session.handle(f.before)
        f.session.handle({'operation': 'send', 'action_id': prepared['action_id']})
        f.now = f.base_time + timedelta(seconds=1)
        return prepared

    def verify_request(self, prepared, after):
        return {'operation': 'verify', 'operation_id': str(uuid4()), 'action_id': prepared['action_id'], 'after': after}

    def outcome_states(self):
        with self.f.db._connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT state_json FROM evidence_events WHERE kind='play_outcome' ORDER BY rowid")]

    def assert_pending_without_outcome(self):
        f = self.f
        self.assertEqual('attempted', f.session.summary()['pending']['status'])
        self.assertTrue(f.session.summary()['pending']['must_not_repeat'])
        self.assertFalse(f.session.summary()['armed'])
        self.assertEqual(1, len(f.controller.inputs))
        self.assertEqual('recommended', f.decisions()[0]['status'])
        self.assertEqual([], self.outcome_states())
        disk = json.loads(f.session.path.read_text())
        self.assertEqual('attempted', disk['pending']['status'])
        self.assertNotIn('outcome_request', disk['pending'])

    def verify_branch(self, branch, expected):
        f = self.f; prepared = self.send_one(); after = self.result(prepared, branch)
        after['choice_outcome_id'] = 'caller-invented'
        after['observation']['review']['outcome']['matched_outcome_id'] = 'caller-invented'
        result = f.session.handle(self.verify_request(prepared, after))
        self.assertEqual('verified', result['status'])
        self.assertIsNone(f.session.summary()['pending'])
        self.assertEqual(1, len(f.controller.inputs))
        states = self.outcome_states()
        self.assertEqual(1, len(states))
        self.assertEqual(expected, states[0]['choice_outcome_id'])
        self.assertNotIn('alternatives', states[0])
        decision = f.decisions()[0]
        self.assertEqual('resolved', decision['status'])
        self.assertEqual(expected, json.loads(decision['actual_outcome_json'])['state']['choice_outcome_id'])
        with self.assertRaises(RuntimeStop):
            f.session.handle({'operation': 'send', 'action_id': prepared['action_id']})
        self.assertEqual(1, len(f.controller.inputs))

    def test_reward_branch_is_derived_and_durably_recorded_after_one_fake_tap(self):
        self.verify_branch(1, 'reward')

    def test_dialogue_branch_is_derived_and_durably_recorded_after_one_fake_tap(self):
        self.verify_branch(0, 'dialogue')

    def test_mixed_branch_result_stays_pending_without_replay(self):
        f = self.f; prepared = self.send_one()
        after = self.result(prepared, 1, mixed=True)
        with self.assertRaisesRegex(ValueError, 'no declared outcome'):
            f.session.handle(self.verify_request(prepared, after))
        self.assert_pending_without_outcome()
        f.session.close(); f.create()
        self.assert_pending_without_outcome()
        with self.assertRaises(RuntimeStop):
            f.session.handle(dict(f.arm_request(), screen='event'))
        with self.assertRaises(RuntimeStop):
            f.session.handle({'operation': 'send', 'action_id': prepared['action_id']})
        self.assertEqual(1, len(f.controller.inputs))

    def test_overlapping_outcomes_cannot_be_written_as_a_selected_branch(self):
        f = self.f
        branches = f.before['choice']['postconditions']['alternatives']
        branches[1]['postconditions'] = deepcopy(branches[0]['postconditions'])
        branches[0]['postconditions']['resources']['hp'] = {'min': 20, 'max': 40}
        branches[1]['postconditions']['resources']['hp'] = {'min': 30, 'max': 50}
        prepared = self.send_one(); after = self.result(prepared, 0)
        after['observation']['resources']['hp'] = 35
        with self.assertRaisesRegex(ValueError, 'overlapping outcome'):
            f.session.handle(self.verify_request(prepared, after))
        self.assert_pending_without_outcome()

    def test_matched_branch_still_requires_inventory_mutation_evidence(self):
        f = self.f
        expected = deepcopy(f.before['inventory'])
        expected['current']['potion'] = ['Fire Potion']
        f.before['choice']['postconditions']['alternatives'][1]['postconditions']['inventory_digest'] = inventory_digest(expected)
        prepared = self.send_one(); after = self.result(prepared, 1)
        after['inventory'] = expected
        after['observation']['inventory_digest'] = inventory_digest(expected)
        with self.assertRaisesRegex(RuntimeStop, 'change needs explicit telemetry'):
            f.session.handle(self.verify_request(prepared, after))
        self.assert_pending_without_outcome()

    def test_boolean_cannot_select_a_durable_integer_fact_branch(self):
        f = self.f
        branches = f.before['choice']['postconditions']['alternatives']
        branches[1]['postconditions'] = deepcopy(branches[0]['postconditions'])
        branches[0]['postconditions']['facts']['result_count'] = 1
        branches[1]['postconditions']['facts']['result_count'] = 2
        prepared = self.send_one(); after = self.result(prepared, 0)
        after['observation']['facts']['result_count'] = True
        with self.assertRaisesRegex(ValueError, 'no declared outcome'):
            f.session.handle(self.verify_request(prepared, after))
        self.assert_pending_without_outcome()

    def test_matched_branch_still_requires_matching_lifecycle_transitions(self):
        f = self.f
        f.before['choice']['postconditions']['alternatives'][1]['postconditions']['context']['combat_id'] = 'new-observed-combat'
        prepared = self.send_one(); after = self.result(prepared, 1)
        with self.assertRaisesRegex(RuntimeStop, 'context and lifecycle disagree'):
            f.session.handle(self.verify_request(prepared, after))
        self.assert_pending_without_outcome()

    def test_matched_branch_survives_commit_response_failure_without_input_replay(self):
        f = self.f; prepared = self.send_one(); after = self.result(prepared, 1)
        original = f.telemetry.record_outcome
        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('synthetic response lost after commit')
        with patch.object(f.telemetry, 'record_outcome', side_effect=commit_then_fail):
            with self.assertRaisesRegex(OSError, 'response lost'):
                f.session.handle(self.verify_request(prepared, after))
        disk = json.loads(f.session.path.read_text())
        self.assertEqual('verified_pending_log', disk['pending']['status'])
        self.assertEqual('reward', disk['pending']['outcome_request']['state']['choice_outcome_id'])
        self.assertEqual('resolved', f.decisions()[0]['status'])
        self.assertEqual(1, len(self.outcome_states()))
        f.session.close(); f.create()
        f.now += timedelta(days=1)
        result = f.session.handle({'operation': 'finalize'})
        self.assertEqual('verified', result['status'])
        self.assertEqual(1, f.session.summary()['completed_inputs'])
        self.assertEqual(1, len(self.outcome_states()))
        self.assertEqual('reward', self.outcome_states()[0]['choice_outcome_id'])
        self.assertEqual(1, len(f.controller.inputs))
