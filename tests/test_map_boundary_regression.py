"""Independent map outcome consistency checks; synthetic images and fake input."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from tests import test_map_result_flow as flow
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_results import validate_menu_result, write_menu_result


class MapBoundaryRegressionTests(unittest.TestCase):
    def fixture(self):
        fixture = flow.MapResultFlowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def result_draft(self, fixture, prepared, result=None):
        return {'schema': 'veda.menu-result.v1', 'action_id': prepared['action_id'],
                'inventory': 'unchanged', 'resources': 'unchanged',
                'facts': dict(fixture.facts, floor=1, current_node_id='center', node_type='enemy'),
                'result': fixture.arrival() if result is None else result,
                'observed_result': 'Synthetic arrival evidence.'}

    def packet(self, fixture, prepared):
        draft = self.result_draft(fixture, prepared)
        source = fixture.capture()
        output = fixture.root / 'independent-result.json'
        write_menu_result(draft, session=fixture.session.path, action_id=prepared['action_id'], capture=source,
            reviewer='Independent fixture', evidence_note='Synthetic inspected result.', reviewed=True,
            control_profile=CONTROL_PROFILE, output=output, now=fixture.now)
        return json.loads(output.read_text())

    def assert_still_attempted(self, fixture):
        self.assertEqual('attempted', fixture.session.state['pending']['status'])
        self.assertEqual(1, len(fixture.controller.inputs))

    def test_opening_hand_cannot_disagree_with_observed_combat_hand(self):
        fixture = self.fixture()
        prepared = fixture.prepare(fixture.draft())
        result = fixture.arrival()
        result['opening_hand'] = ['Bash+']
        with self.assertRaises(ValueError):
            validate_menu_result(self.result_draft(fixture, prepared, result), session=fixture.session.path,
                action_id=prepared['action_id'], control_profile=CONTROL_PROFILE)
        self.assert_still_attempted(fixture)

    def test_wrong_opening_act_rejects_before_durable_log_freeze(self):
        fixture = self.fixture()
        prepared = fixture.prepare(fixture.draft())
        result = fixture.arrival()
        result['encounter']['opening_state']['act'] = 2
        with self.assertRaises(ValueError):
            validate_menu_result(self.result_draft(fixture, prepared, result), session=fixture.session.path,
                action_id=prepared['action_id'], control_profile=CONTROL_PROFILE)
        self.assert_still_attempted(fixture)

    def test_adapter_rejects_lifecycle_node_kind_mismatching_selected_node(self):
        fixture = self.fixture()
        prepared = fixture.prepare(fixture.draft())
        packet = self.packet(fixture, prepared)
        packet['telemetry']['transitions'][0]['node_type'] = 'elite'
        packet['after']['mutation_review']['changes'] = deepcopy(packet['telemetry'])
        with self.assertRaises(ValueError):
            fixture.session.handle(packet)
        self.assert_still_attempted(fixture)

    def test_adapter_rejects_zone_hand_mismatching_observed_opening_hand(self):
        fixture = self.fixture()
        prepared = fixture.prepare(fixture.draft())
        packet = self.packet(fixture, prepared)
        packet['telemetry']['zone_baseline']['hand'] = ['Bash+']
        packet['after']['mutation_review']['changes'] = deepcopy(packet['telemetry'])
        with self.assertRaises(ValueError):
            fixture.session.handle(packet)
        self.assert_still_attempted(fixture)

    def test_same_pixel_inspection_finalizes_after_log_retry_without_input_replay(self):
        fixture = self.fixture()
        prepared = fixture.prepare(fixture.draft(direction='up'), color=(9, 9, 9))
        with patch.object(fixture.session.telemetry, 'record_outcome', side_effect=RuntimeError('Transient log failure')):
            with self.assertRaisesRegex(RuntimeError, 'Transient log failure'):
                fixture.verify(prepared, fixture.view('unchanged'), color=(9, 9, 9))
        self.assertEqual('verified_pending_log', fixture.session.state['pending']['status'])
        fixture.session.close()
        fixture.now += timedelta(days=2)
        fixture.session = fixture.new_session()
        result = fixture.session.finalize()
        self.assertEqual('verified', result['status'])
        self.assertEqual(1, len(fixture.controller.inputs))
        self.assertEqual(1, fixture.session.state['map_inspection_progress']['total'])
        self.assertIsNone(fixture.session.state['pending'])

    def test_committed_inspection_recovery_counts_progress_only_once(self):
        fixture = self.fixture()
        prepared = fixture.prepare(fixture.draft(direction='up'))
        original = fixture.session.telemetry.record_outcome

        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Interrupted after database commit')

        with patch.object(fixture.session.telemetry, 'record_outcome', side_effect=commit_then_fail):
            with self.assertRaisesRegex(RuntimeError, 'Interrupted after database commit'):
                fixture.verify(prepared, fixture.view())
        self.assertEqual('verified_pending_log', fixture.session.state['pending']['status'])
        fixture.session.close()
        fixture.session = fixture.new_session()
        fixture.session.finalize()
        self.assertEqual(1, len(fixture.controller.inputs))
        self.assertEqual(1, fixture.session.state['map_inspection_progress']['total'])
        self.assertIsNone(fixture.session.state['pending'])
        with fixture.db._connection() as con:
            self.assertEqual(1, con.execute("SELECT count(*) FROM decisions WHERE status='resolved'").fetchone()[0])

    def test_room_departure_preserves_previously_completed_floor_result(self):
        fixture = self.fixture()
        ending = {'screen': 'event', 'hp': 80}
        summary = {'upgrade': 'Bash+'}
        fixture.db.complete_floor(floor_id=fixture.floor, outcome='upgraded Bash',
                                  ending_state=ending, summary=summary)
        prepared = fixture.prepare(fixture.draft())
        fixture.verify(prepared, fixture.arrival(),
                       facts=dict(fixture.facts, floor=1, current_node_id='center', node_type='enemy'))
        with fixture.db._connection() as con:
            row = con.execute('SELECT outcome,ending_state_json,summary_json FROM floors WHERE id=?',
                              (fixture.floor,)).fetchone()
        self.assertEqual('upgraded Bash', row['outcome'])
        self.assertEqual(ending, json.loads(row['ending_state_json']))
        self.assertEqual(summary, json.loads(row['summary_json']))


if __name__ == '__main__':
    unittest.main()
