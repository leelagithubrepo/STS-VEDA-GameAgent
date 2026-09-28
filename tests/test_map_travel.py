"""Map fast path: saved plans, material changes and fake-adapter outcomes only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_map_result_flow as flow
from tests.test_map_survey import SurveyFixture, edge, make_view, node
from veda.map_travel import SNAPSHOT_SCHEMA, focus_result, focus_snapshot, plan_map
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_results import validate_menu_result
from veda.reviewed_play import ReviewedPlaySession


def snapshot():
    return {'schema': SNAPSHOT_SCHEMA,
        'context': {'run_id': 'test-run', 'floor_id': 'floor-zero', 'combat_id': None, 'turn_id': None},
        'inventory': {'current': {'card': ['Strike', 'Defend', 'Bash'], 'relic': ['Burning Blood'], 'potion': []},
                      'coverage': {k: 'complete' for k in ('card', 'relic', 'potion')}, 'properties': {}},
        'resources': {'hp': 80, 'max_hp': 80, 'gold': 99, 'deck_size': 3},
        'facts': {'act': 1, 'floor': 0, 'current_node_id': 'current', 'ascension': 2},
        'ui': {'menu_family': 'map_nodes', 'choice_id': 'floor-one', 'focused_id': 'middle',
               'map_siblings': {'complete': True, 'selectable_count': 3, 'from_node_id': 'current',
                                'evidence_note': 'All three synthetic selectable siblings are visible.'},
               'options': [{'id': name, 'label': kind, 'enabled': True, 'costs': {},
                            'node': {'node_id': name, 'kind': kind, 'act': 1, 'floor': 1, 'x': x, 'y': 700,
                                     'reachable': True, 'reachability_evidence': 'Synthetic connected starting node.',
                                     'classification_evidence': 'Synthetic room icon.'}}
                           for name, kind, x in [('left', 'event', 500), ('middle', 'enemy', 800), ('right', 'merchant', 1100)]]}}


def decision(*nodes, **extra):
    return {'node_ids': list(nodes or ('current', 'right')), 'reason': 'Reviewed route tradeoff for this fixture.', **extra}


class MapTravelTests(SurveyFixture):
    def setUp(self):
        super().setUp()
        self.value = snapshot()

    def view(self):
        view = make_view(self.source, 'routes',
            [node('current', 0), node('left', 1, 0, 'event', complete=False),
             node('middle', 1, 1, complete=False), node('right', 1, 2, 'merchant'),
             node('rest', 2, 0, 'rest', complete=False)],
            [edge('current', n) for n in ('left', 'middle', 'right')] + [edge('right', 'rest')],
            bottom=True, rows=[0, 1])
        return {'schema': 'veda.bound-map-view.v1', 'run_id': 'test-run', 'act': 1,
                'current_node_id': 'current', 'view': view, 'controller_authorized': False, 'runtime_authorized': False}

    def after_arrival(self):
        value = deepcopy(self.value)
        value['context']['floor_id'] = 'actual-verified-floor-one'
        value['facts'].update(floor=1, current_node_id='right')
        value['ui']['choice_id'] = 'floor-two'
        value['ui']['focused_id'] = 'rest'
        value['ui']['map_siblings'].update(from_node_id='right', selectable_count=1)
        value['ui']['options'] = [deepcopy(value['ui']['options'][0])]
        value['ui']['options'][0].update(id='rest', label='rest')
        value['ui']['options'][0]['node'].update(node_id='rest', kind='rest', floor=2)
        return value

    def test_partial_midrun_map_needs_one_choice_not_full_survey_or_boss(self):
        self.value['facts']['floor'] = 5
        for option in self.value['ui']['options']:
            option['node']['floor'] = 6
        result = plan_map(self.value)
        self.assertEqual('strategy_required', result['status'])
        self.assertFalse(result['survey_required'])
        self.assertIsNone(result['expected_boss'])
        result = plan_map(self.value, decision=decision())
        self.assertEqual('planned', result['status'])
        self.assertEqual('right', result['destination'])
        self.assertEqual('shop', result['draft']['choice']['postconditions']['screen'])
        self.assertFalse(result['controller_input_sent'])

    def test_saved_route_survives_json_roundtrip_focus_coordinates_and_small_resource_changes(self):
        saved = plan_map(self.value, decision=decision())['cache']
        cache = json.loads(json.dumps(saved))
        self.value = focus_snapshot(self.value, 'right', unchanged=True)
        self.value['resources'].update(hp=77, gold=119)
        self.value['ui']['options'][0]['node']['y'] = 650
        self.value['inventory']['current']['card'].reverse()
        with patch('veda.bridge_client.BridgeClient', side_effect=AssertionError('controller touched')), \
             patch('veda.telemetry_database.TelemetryDatabase', side_effect=AssertionError('database touched')):
            result = plan_map(self.value, cache)
        self.assertEqual('planned', result['status'])
        self.assertTrue(result['reused_route'])
        self.assertEqual('right', result['destination'])
        self.assertEqual(saved, cache)

    def test_material_changes_reassess_but_preserve_topology(self):
        cache = plan_map(self.value, decision=decision(), views=[self.view()])['cache']
        for field in ('hp', 'gold', 'max_hp', 'card', 'potion', 'relic', 'options'):
            value = deepcopy(self.value)
            if field in {'hp', 'gold', 'max_hp'}:
                value['resources'][field] = {'hp': 30, 'gold': 150, 'max_hp': 90}[field]
            elif field == 'options':
                value['ui']['options'][0]['node']['kind'] = 'elite'
            else:
                value['inventory']['current'][field].append({'card': 'Shrug It Off', 'potion': 'Block Potion', 'relic': 'Anchor'}[field])
            with self.subTest(field=field):
                result = plan_map(value, cache)
                self.assertEqual('strategy_required', result['status'])
                self.assertFalse(result['survey_required'])
                self.assertEqual(cache['survey']['views'], result['cache']['survey']['views'])
                self.assertNotIn('draft', result)
                self.assertEqual('planned', plan_map(value, result['cache'], decision())['status'])

    def test_budget_threshold_reassesses_even_for_small_gold_change(self):
        cache = plan_map(self.value, decision=decision(reassess={'gold_thresholds': [100]}))['cache']
        self.value['resources']['gold'] = 100
        result = plan_map(self.value, cache)
        self.assertIn('gold_threshold_crossed', result['reasons'])

    def test_cache_does_not_reset_baseline_after_each_small_change(self):
        cache = plan_map(self.value, decision=decision())['cache']
        self.value['resources']['hp'] = 73
        result = plan_map(self.value, cache)
        self.assertEqual('planned', result['status'])
        self.value['resources']['hp'] = 66
        self.assertIn('material_hp_change', plan_map(self.value, result['cache'])['reasons'])

    def test_saved_future_route_advances_only_from_actual_next_snapshot(self):
        cache = plan_map(self.value, decision=decision('current', 'right', 'rest'), views=[self.view()])['cache']
        self.assertEqual(0, cache['route']['cursor'])
        again = plan_map(self.value, cache)
        self.assertEqual('right', again['destination'])
        arrived = plan_map(self.after_arrival(), cache)
        self.assertEqual('rest', arrived['destination'])
        self.assertTrue(arrived['reused_route'])
        self.assertEqual(1, arrived['cache']['route']['cursor'])
        self.assertIn('off_saved_route', plan_map(self.value, arrived['cache'])['reasons'])

    def test_future_edges_cannot_be_invented_and_partial_cache_is_useful(self):
        with self.assertRaisesRegex(ValueError, 'future route edges'):
            plan_map(self.value, decision=decision('current', 'right', 'rest'))
        cache = plan_map(self.value, decision=decision(), views=[self.view()])['cache']
        # The old archive doesn't contain this new current node; no forced rescan.
        value = self.after_arrival()
        value['facts']['current_node_id'] = 'later-node'
        value['ui']['map_siblings']['from_node_id'] = 'later-node'
        result = plan_map(value, cache, decision('later-node', 'rest'))
        self.assertEqual('planned', result['status'])
        self.assertEqual(cache['survey']['views'], result['cache']['survey']['views'])

    def test_exhausted_or_unavailable_route_invites_next_choice(self):
        cache = plan_map(self.value, decision=decision())['cache']
        self.assertIn('saved_route_complete', plan_map(self.after_arrival(), cache)['reasons'])
        self.value['ui']['options'] = self.value['ui']['options'][:2]
        self.value['ui']['map_siblings']['selectable_count'] = 2
        self.assertIn('saved_destination_unavailable', plan_map(self.value, cache)['reasons'])

    def test_new_images_of_same_topology_do_not_reassess_but_new_information_does(self):
        cache = plan_map(self.value, decision=decision(), views=[self.view()])['cache']
        same = self.view()
        same['view']['view_id'] = 'same-graph'
        self.assertEqual('planned', plan_map(self.value, cache, views=[same])['status'])
        new = self.view()
        new['view']['view_id'] = 'more-route'
        new['view']['nodes'].append(node('boss', 3, 0, 'boss'))
        new['view']['edges'].append(edge('rest', 'boss'))
        result = plan_map(self.value, cache, views=[new])
        self.assertIn('map_knowledge_changed', result['reasons'])

    def test_contradictory_view_cannot_erase_old_route_evidence(self):
        cache = plan_map(self.value, decision=decision(), views=[self.view()])['cache']
        original = deepcopy(cache)
        conflict = self.view()
        conflict['view']['view_id'] = 'conflict'
        conflict['view']['nodes'][3]['kind'] = 'elite'
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            plan_map(self.value, cache, views=[conflict])
        self.assertEqual(original, cache)

    def test_no_cache_or_bound_view_reuse_across_run_or_act(self):
        cache = plan_map(self.value, decision=decision())['cache']
        for key in ('run_id', 'act'):
            bad = deepcopy(cache)
            bad[key] = 'other-run' if key == 'run_id' else 2
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'another run or act'):
                plan_map(self.value, bad)
        view = self.view(); view['act'] = 2
        with self.assertRaisesRegex(ValueError, 'another run or act'):
            plan_map(self.value, views=[view])

    def test_focus_shortcut_requires_observation_and_cannot_change_other_facts(self):
        with self.assertRaisesRegex(ValueError, 'confirm other map'):
            focus_snapshot(self.value, 'right')
        with self.assertRaisesRegex(ValueError, 'actual focus'):
            focus_snapshot(self.value, 'hidden-node', unchanged=True)
        updated = focus_snapshot(self.value, 'right', unchanged=True)
        expected = deepcopy(self.value); expected['ui']['focused_id'] = 'right'
        self.assertEqual(expected, updated)

    def test_event_generates_distinct_room_alternatives_without_inventing_encounter(self):
        result = plan_map(self.value, decision=decision('current', 'left'))
        branches = result['draft']['choice']['postconditions']['alternatives']
        self.assertEqual({'event', 'combat', 'shop', 'treasure', 'reward'}, {b['id'] for b in branches})
        self.assertNotIn('encounter', str(branches))

    def test_explicit_entry_resource_prediction_is_preserved(self):
        self.value['entry_resources'] = {'right': dict(self.value['resources'], hp={'min': 80, 'max': 80}, gold=111)}
        result = plan_map(self.value, decision=decision())
        self.assertEqual(111, result['draft']['choice']['postconditions']['resources']['gold'])

    def test_cli_saves_private_cache_and_reuses_it_without_strategy_or_controller(self):
        snap, choice, cache = (self.root / n for n in ('snapshot.json', 'decision.json', 'cache.json'))
        snap.write_text(json.dumps(self.value)); choice.write_text(json.dumps(decision()))
        command = [sys.executable, 'scripts/veda_map_step.py', '--snapshot', str(snap)]
        first = subprocess.run(command + ['--decision', str(choice), '--cache-output', str(cache)], capture_output=True, text=True)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        again = subprocess.run(command + ['--cache', str(cache), '--focus', 'right', '--unchanged'], capture_output=True, text=True)
        self.assertEqual(0, again.returncode, again.stdout + again.stderr)
        self.assertTrue(json.loads(again.stdout)['reused_route'])
        self.assertFalse(json.loads(again.stdout)['controller_input_sent'])
        before = cache.read_bytes()
        overwrite = subprocess.run(command + ['--decision', str(choice), '--cache-output', str(cache)], capture_output=True, text=True)
        self.assertEqual(2, overwrite.returncode)
        self.assertEqual(before, cache.read_bytes())


class MapTravelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.flow = flow.MapResultFlowTests()
        with patch('veda.telemetry_database._now', return_value=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()):
            self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.flow.session.close()
        self.flow.now = datetime.now(timezone.utc) - timedelta(seconds=15)
        self.flow.session = ReviewedPlaySession(self.flow.root/'fast-session', run_id=self.flow.run,
            telemetry=self.flow.session.telemetry, mode='codex', decision_policy='learning',
            controller_factory=lambda: self.flow.controller, clock=lambda: self.flow.now)
        draft = self.flow.draft(wanted='right', room='merchant')
        self.snapshot = {'schema': SNAPSHOT_SCHEMA, **{k: draft[k] for k in ('context', 'inventory', 'resources', 'facts', 'ui')}}

    def test_focus_then_entry_uses_saved_decision_and_real_verification(self):
        f = self.flow
        plan = plan_map(self.snapshot, decision=decision('start', 'right'))
        prepared = f.prepare(plan['draft'])
        draft = focus_result(f.session.path, prepared['action_id'], 'right', unchanged=True,
                             observed_result='Observed right node focus; all other map facts unchanged.')
        validate_menu_result(draft, session=f.session.path, action_id=prepared['action_id'], control_profile=CONTROL_PROFILE)
        f.verify(prepared, draft['result'])
        value = focus_snapshot(self.snapshot, 'right', unchanged=True)
        reused = plan_map(value, json.loads(json.dumps(plan['cache'])))
        self.assertTrue(reused['reused_route'])
        prepared = f.prepare(reused['draft'])
        f.verify(prepared, {'kind': 'room_entry', 'node_id': 'right', 'screen': 'shop'},
                 facts=dict(f.facts, floor=1, current_node_id='right', node_type='merchant'))
        self.assertEqual(['right', 'cross'], [c['buttons'][0] for c in f.controller.inputs])
        self.assertIsNone(f.session.state['pending'])
        with f.db._connection() as con:
            self.assertEqual(2, con.execute("SELECT count(*) FROM decisions WHERE status='resolved'").fetchone()[0])
            self.assertEqual(1, con.execute("SELECT count(*) FROM floors WHERE node_type='merchant'").fetchone()[0])

    def test_unverified_focus_cannot_be_submitted_as_entry_and_wrong_result_does_not_repeat(self):
        f = self.flow
        plan = plan_map(self.snapshot, decision=decision('start', 'right'))
        pending = f.prepare(plan['draft'])
        with self.assertRaises(ValueError):
            f.verify(pending, {'kind': 'room_entry', 'node_id': 'right', 'screen': 'shop'},
                     facts=dict(f.facts, floor=1, current_node_id='right', node_type='merchant'))
        with self.assertRaises(ValueError):
            focus_result(f.session.path, pending['action_id'], 'right', unchanged=False, observed_result='Not inspected.')
        self.assertEqual(1, len(f.controller.inputs))
        self.assertEqual('attempted', f.session.state['pending']['status'])

    def test_terminal_focus_result_and_cached_entry_envelopes_bind_exact_epoch(self):
        f = self.flow
        # Real helper subprocess clocks see settled synthetic captures in the past.
        f.now = datetime.now(timezone.utc) - timedelta(seconds=10)
        planned = plan_map(self.snapshot, decision=decision('start', 'right'))
        prepared = f.prepare(planned['draft'])
        after = f.capture()
        snap, cache = f.root/'snapshot.json', f.root/'cache.json'
        snap.write_text(json.dumps(self.snapshot)); cache.write_text(json.dumps(planned['cache']))
        output = f.root/'focus-result-request.json'
        command = [sys.executable, 'scripts/veda_map_step.py', '--session', str(f.session.path),
                   '--reviewer', 'Fixture', '--evidence-note', 'Exact inspected synthetic frame.', '--reviewed']
        result = subprocess.run(command + ['--focus-result', 'right', '--action-id', prepared['action_id'],
            '--unchanged', '--observed-result', 'Right node focused; other facts unchanged.',
            '--capture', str(after), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual({'request_file'}, set(json.loads(result.stdout)))
        packet = json.loads(output.read_text())
        answer = f.session.handle(packet)
        self.assertEqual('verified', answer['status'])
        self.assertEqual(1, len(f.controller.inputs))
        # The inspected after-image is also the next before-image. No extra
        # capture, map scan or strategy decision is required while unchanged.
        output = f.root/'entry-request.json'
        result = subprocess.run(command + ['--snapshot', str(snap), '--cache', str(cache),
            '--focus', 'right', '--unchanged', '--capture', str(after), '--execute',
            '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual({'request_file'}, set(json.loads(result.stdout)))
        packet = json.loads(output.read_text())
        self.assertIn('evidence_binding', packet)
        answer = f.session.handle(packet)
        self.assertEqual(2, len(f.controller.inputs))
        action_id = f.session.state['pending']['action_id']
        f.verify({'action_id': action_id}, {'kind': 'room_entry', 'node_id': 'right', 'screen': 'shop'},
                 facts=dict(f.facts, floor=1, current_node_id='right', node_type='merchant'))
        self.assertEqual(['right', 'cross'], [c['buttons'][0] for c in f.controller.inputs])


if __name__ == '__main__':
    unittest.main()
