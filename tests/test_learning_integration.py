"""Learning policy across compact menus, durable evidence and historical retrieval."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from tests import test_menu_results, test_menu_requests, test_play_telemetry, test_play_lessons
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_requests import validate_menu_draft
from veda.menu_results import validate_menu_result, write_menu_result
from veda.play_telemetry import outcome_request_digest
from veda.play_lessons import read_play_lessons


class LearningIntegrationTests(unittest.TestCase):
    def fixture(self, cls):
        fixture = cls(methodName='runTest'); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_menu_unknown_inventory_and_policy_survive_packaging(self):
        fx = self.fixture(test_menu_requests.MenuRequestTests)
        fx.value['inventory']['coverage'].update(relic='partial', potion='unknown')
        with self.assertRaisesRegex(ValueError, 'complete'):
            validate_menu_draft(fx.value, CONTROL_PROFILE)
        fx.value['decision_policy'] = 'learning'
        self.assertTrue(validate_menu_draft(fx.value, CONTROL_PROFILE)['draft_valid'])
        fx.build()
        packet = json.loads(fx.output.read_text())
        self.assertEqual('learning', packet['decision_policy'])
        self.assertEqual(fx.value['inventory'], packet['inventory'])

    def test_unpredicted_menu_effect_is_observed_not_rejected_or_silently_rewritten(self):
        fx = self.fixture(test_menu_results.MenuResultTests)
        actual = dict(fx.state['pending']['request']['observation']['resources']); actual['hp'] -= 1
        fx.result['resources'] = actual
        with self.assertRaisesRegex(ValueError, 'resource outcome differs'):
            validate_menu_result(fx.result, session=fx.session, action_id=fx.action, control_profile=CONTROL_PROFILE)
        fx.state['pending']['request']['decision_policy'] = 'learning'; fx.save_state()
        original = fx.session.read_bytes()
        self.assertTrue(validate_menu_result(fx.result, session=fx.root, action_id=fx.action,
                                             control_profile=CONTROL_PROFILE)['result_valid'])
        write_menu_result(fx.result, session=fx.root, action_id=fx.action, capture=fx.image,
                          reviewer='Fixture', evidence_note='Synthetic observed menu outcome.', reviewed=True,
                          control_profile=CONTROL_PROFILE, output=fx.output, now=fx.now)
        packet = json.loads(fx.output.read_text())
        self.assertEqual('learning', packet['after']['decision_policy'])
        self.assertEqual(actual, packet['after']['observation']['resources'])
        self.assertEqual(original, fx.session.read_bytes())

    def test_verified_learning_metadata_persists_and_is_retrievable_idempotently(self):
        fx = self.fixture(test_play_telemetry.PlayTelemetryTests)
        fx.request['prediction'] = {'decision_policy':'learning', 'forecast':None,
                                   'assessment':{'warnings':['Unmodeled effect'], 'forecast_status':'unknown'}}
        decision = fx.prepare()['decision_id']
        outcome = fx.outcome(decision)
        learning = {'decision_policy':'learning', 'assessment':fx.request['prediction']['assessment'],
                    'observed_mismatches':[{'field':'choice_postconditions',
                                           'expected':{'resources':{'energy':2}},
                                           'observed':{'resources':{'energy':1}}}]}
        proof = {'basis':'fresh_verified_result', 'action_id':fx.request['operation_id'],
                 'outcome_sha256':outcome_request_digest(outcome), 'source':deepcopy(outcome['source']),
                 'reviewed_at':(fx.now+timedelta(seconds=2)).isoformat(),
                 'review':{'reviewer':'Fixture', 'complete':True, 'frame_id':'synthetic-after',
                           'image_sha256':outcome['source']['sha256']}, **learning}
        first = fx.api.record_outcome(outcome, now=fx.now+timedelta(seconds=3), verified_evidence=proof)
        again = fx.api.record_outcome(outcome, now=fx.now+timedelta(seconds=4), verified_evidence=proof)
        self.assertEqual(first['event_id'], again['event_id'])
        with fx.db._connection() as con:
            actual = json.loads(con.execute('SELECT actual_outcome_json FROM decisions WHERE id=?', (decision,)).fetchone()[0])
        self.assertEqual(learning, actual['learning'])
        self.assertEqual(outcome['state'], actual['state'])
        case = read_play_lessons(fx.db.path, action='potion')['cases'][0]
        self.assertEqual('mismatch', case['comparison']['status'])
        self.assertTrue(case['comparison']['mismatches'][0]['values_omitted'])
        self.assertEqual('unknown', case['comparison']['forecast_status'])
        self.assertIn('Unmodeled effect', case['uncertainties'])
        self.assertEqual(1, fx.table_count('decisions'))

    def test_absent_enemy_state_remains_absent_and_database_open_failure_is_bounded(self):
        fx = self.fixture(test_play_lessons.PlayLessonsTests)
        fx.add(after={'hp':29})
        case = fx.read()['cases'][0]
        self.assertNotIn('enemies', case['observed_after'])
        import sqlite3
        with patch('veda.play_lessons.sqlite3.connect', side_effect=sqlite3.OperationalError('synthetic')):
            with self.assertRaisesRegex(ValueError, 'lesson retrieval unavailable'):
                fx.read()


if __name__ == '__main__':
    unittest.main()
