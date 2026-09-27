import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from veda.calibration import CalibrationReport
from veda.execution import ActionJournal, ExecutionLoop, RUNTIME_FIELDS, RuntimeStop
from veda.execution_adapters import RecordedInterpreter, ReplayController, ReplaySource
from veda.vision import StructuredGameState, VisibleEnemy


def context():
    cards = [dict(id='s', name='Strike', type='Attack', cost=1, upgraded=False,
                  title_color='white', playable=True),
             dict(id='d', name='Defend', type='Skill', cost=1, upgraded=False,
                  title_color='white', playable=True)]
    return {'fresh': True, 'unknowns': [], 'encounter_type': 'enemy',
            'inventory': {'coverage': {'relic': 'complete', 'potion': 'complete'},
                          'current': {'relic': [], 'potion': []}},
            'state': {'schema': 'spire.advisory.v1', 'observed_at': datetime.now(timezone.utc).isoformat(),
                      'hp': 40, 'max_hp': 80, 'energy': 1, 'block': 0, 'strength': 0,
                      'dexterity': 0, 'weak': 0, 'vulnerable': 0, 'frail': 0,
                      'no_block': 0, 'powers': {}, 'powers_complete': True,
                      'hand_complete': True, 'hand': cards, 'counters': {},
                      'piles': {'draw': None, 'discard': [], 'exhaust': [], 'draw_order': None},
                      'enemies': [{'id': 'enemy', 'name': 'Cultist', 'hp': 12, 'max_hp': 50,
                                   'block': 0, 'vulnerable': 0, 'weak': 0, 'strength': 0, 'artifact': 0,
                                   'intent': 'attack 6', 'intent_hits': [6], 'intent_effects': []}],
                      'end_turn_damage': 0, 'unmodeled_effects': []}}


def reading(c, ui, turn='turn-1'):
    s = c['state']
    visual = StructuredGameState('COMBAT', 1.0, act=1, hp=s['hp'], max_hp=s['max_hp'],
        energy=s['energy'], block=s['block'], player_strength=s['strength'], player_weak=s['weak'],
        player_vulnerable=s['vulnerable'], player_frail=s['frail'], hand=tuple(x['name'] for x in s['hand']),
        hand_complete=True, hand_details=tuple({'name': x['name'], 'title_color': x['title_color'],
            'upgraded': x['upgraded'], 'current_cost': x['cost']} for x in s['hand']),
        end_turn_damage=0, end_turn_damage_confidence=1.0,
        enemies=tuple(VisibleEnemy(x['name'], x['hp'], x['max_hp'], x['intent'], x['block'],
                                  tuple(x['intent_hits']), sum(x['intent_hits']), 1.0) for x in s['enemies']))
    return dict(state=asdict(visual), context=copy.deepcopy(c), ui=ui,
                encounter_name='Cultist', run_id='fixture-run', floor_id='fixture-floor', turn_id=turn)


def contract_manifest(directory):
    """Synthetic state-machine contract fixture; never vision calibration evidence."""
    directory = Path(directory)
    base = context()
    ui = {'screen_type': 'combat', 'phase': 'hand', 'hand_order': ['s', 'd'],
          'focused_card_id': 'd', 'selected_card_id': None, 'focused_target_id': None}
    sequence = [reading(base, dict(ui)), reading(base, {**ui, 'focused_card_id': 's'}),
                reading(base, {**ui, 'phase': 'targeting', 'focused_card_id': 's',
                               'selected_card_id': 's', 'focused_target_id': 'enemy', 'target_order': ['enemy']})]
    resolved = copy.deepcopy(base)
    resolved['state']['hand'] = resolved['state']['hand'][1:]
    resolved['state']['energy'] = 0
    resolved['state']['enemies'][0]['hp'] = 6
    sequence.append(reading(resolved, {**ui, 'hand_order': ['d']}))
    frames = []
    for index, item in enumerate(sequence):
        path = directory / f'synthetic-{index}.dat'
        path.write_text(f'Synthetic state-machine fixture {index}; not a gameplay image.')
        frames.append(dict(frame_id=f'fixture-frame-{index}', image=path.name,
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           observed_at=base['state']['observed_at'], reading=item))
    return dict(run_id='fixture-run', evidence_kind='synthetic_contract_not_vision_validation',
                frames=frames, expected_commands=[['left'], ['cross'], ['cross']],
                calibration={'field_accuracy': {k: 1.0 for k in RUNTIME_FIELDS}, 'total_frames': 12})


class ExecutionTests(unittest.TestCase):
    def execute(self, directory, manifest, *, controller=None, calibration=None, journal=None, **kw):
        source = ReplaySource(manifest, Path(directory))
        controller = controller or ReplayController(manifest['expected_commands'])
        calibration = calibration or CalibrationReport(manifest['calibration']['field_accuracy'], 12)
        loop = ExecutionLoop(source, RecordedInterpreter(source), controller,
            calibration=calibration, run_id=manifest['run_id'], max_inputs=3,
            journal=journal, **kw)
        return loop.run(), controller

    def test_real_planner_machine_journal_pipeline_without_hardware_or_model(self):
        with TemporaryDirectory() as d:
            manifest = contract_manifest(d)
            journal = ActionJournal(Path(d) / 'journal.jsonl')
            with patch('subprocess.Popen', side_effect=AssertionError('no external process')), \
                 patch('socket.socket', side_effect=AssertionError('no network')):
                result, controller = self.execute(d, manifest, journal=journal)
            self.assertEqual([x['buttons'] for x in controller.commands], manifest['expected_commands'], result)
            self.assertEqual(result['unresolved_actions'], [])
            self.assertEqual(result['decisions'], 1, 'focus inputs must not replan strategy')
            self.assertEqual(len([x for x in journal.rows if x['status'] == 'verified']), 3)
            self.assertFalse(result['live_performance_validated'])
            self.assertIn('controller_round_trip', result['metrics']['stages'])

    def test_navigation_mismatch_stops_without_second_input(self):
        with TemporaryDirectory() as d:
            manifest = contract_manifest(d)
            manifest['frames'][1]['reading']['ui']['focused_card_id'] = 'd'
            result, controller = self.execute(d, manifest)
            self.assertEqual(len(controller.commands), 1)
            self.assertIn('mismatch', result['reason'])
            self.assertEqual(result['metrics']['coverage']['decision_errors'], 1)

    def test_uncertain_send_is_reobserved_but_never_replayed(self):
        class Uncertain(ReplayController):
            def call(self, command):
                self.commands.append(command)
                return {'status': 'unknown_outcome', 'request_id': command['request_id']}
        with TemporaryDirectory() as d:
            result, controller = self.execute(d, contract_manifest(d), controller=Uncertain())
            self.assertEqual(len(controller.commands), 1)
            self.assertEqual(len(result['unresolved_actions']), 1)
            self.assertIn('no automatic replay', result['reason'])

    def test_unresolved_restart_observes_then_stops(self):
        with TemporaryDirectory() as d:
            journal = ActionJournal(Path(d) / 'actions.jsonl')
            journal.append(status='attempted', action_id='old')
            restarted = ActionJournal(journal.path)
            result, controller = self.execute(d, contract_manifest(d), journal=restarted)
            self.assertEqual(controller.commands, [])
            self.assertIn('reconciliation', result['reason'])

    def test_unvalidated_vision_has_zero_inputs(self):
        with TemporaryDirectory() as d:
            result, controller = self.execute(d, contract_manifest(d), calibration=CalibrationReport({}, 0))
            self.assertEqual(controller.commands, [])
            self.assertEqual(result['metrics']['coverage']['fast_path_coverage'], 0)

    def test_shadow_uses_same_plan_without_input(self):
        with TemporaryDirectory() as d:
            result, controller = self.execute(d, contract_manifest(d), mode='shadow')
            self.assertEqual(result['outcome'], 'shadow')
            self.assertEqual(controller.commands, [])

    def test_stale_frame_identity_and_wrong_run_reject(self):
        with TemporaryDirectory() as d:
            manifest = contract_manifest(d)
            manifest['frames'][1]['frame_id'] = manifest['frames'][0]['frame_id']
            result, controller = self.execute(d, manifest)
            self.assertEqual(len(controller.commands), 1)
            self.assertIn('stale', result['reason'])
            manifest = contract_manifest(d)
            manifest['frames'][0]['reading']['run_id'] = 'another-run'
            result, controller = self.execute(d, manifest)
            self.assertEqual(controller.commands, [])
            self.assertIn('identity changed', result['reason'])

    def test_user_stop_sends_nothing(self):
        with TemporaryDirectory() as d:
            result, controller = self.execute(d, contract_manifest(d), should_stop=lambda: True)
            self.assertEqual(controller.commands, [])
            self.assertIn('user stop', result['reason'])

    def test_live_requires_separate_arming_before_any_adapter_use(self):
        with self.assertRaises(RuntimeStop):
            ExecutionLoop(None, None, None, calibration=CalibrationReport({}, 0),
                          run_id='any', mode='live')

    def test_fresh_navigation_visual_cannot_reuse_stale_resources_or_costs(self):
        for mismatch in ('energy', 'current_cost', 'hp'):
            with self.subTest(field=mismatch), TemporaryDirectory() as d:
                manifest = contract_manifest(d)
                visual = manifest['frames'][1]['reading']['state']
                if mismatch == 'current_cost':
                    visual['hand_details'][0]['current_cost'] = 2
                else:
                    visual[mismatch] = 0 if mismatch == 'energy' else 30
                result, controller = self.execute(d, manifest)
                self.assertEqual(len(controller.commands), 1, result)
                self.assertIn('inconsistent', result['reason'])
                self.assertEqual(len(result['unresolved_actions']), 1)

    def test_selection_frame_is_checked_before_target_confirmation(self):
        with TemporaryDirectory() as d:
            manifest = contract_manifest(d)
            manifest['frames'][2]['reading']['state']['hand_details'][0]['upgraded'] = True
            result, controller = self.execute(d, manifest)
            self.assertEqual(len(controller.commands), 2, result)
            self.assertIn('upgrade', result['reason'])

    def test_recording_metadata_does_not_break_unchanged_navigation(self):
        with TemporaryDirectory() as d:
            manifest = contract_manifest(d)
            for index, frame in enumerate(manifest['frames']):
                context = frame['reading']['context']
                context.update(snapshot_id=f'snapshot-{index}', age_seconds=index / 100,
                               screenshot_path=f'frame-{index}.png')
                context['state']['observed_at'] = datetime.now(timezone.utc).isoformat()
                context['inventory'].update(history=[{'id': f'event-{index}'}],
                    latest_baseline={'id': f'baseline-{index}'},
                    properties={'relic:anchor': {'property': 'Start combat with 10 Block.',
                                                'source': f'capture-{index}'}})
                context['rules'] = {'version': 'same-reviewed-version', 'reviewed_at': f'recorded-{index}'}
            result, controller = self.execute(d, manifest)
            self.assertEqual(len(controller.commands), 3, result)
            self.assertEqual(result['unresolved_actions'], [])

    def test_semantic_inventory_change_is_not_ignored_during_navigation(self):
        for field in ('current', 'coverage', 'properties'):
            with self.subTest(field=field), TemporaryDirectory() as d:
                manifest = contract_manifest(d)
                inventory = manifest['frames'][1]['reading']['context']['inventory']
                if field == 'current':
                    inventory['current']['potion'].append('Energy Potion')
                elif field == 'coverage':
                    inventory['coverage']['potion'] = 'partial'
                else:
                    inventory['properties'] = {'relic:anchor': {'property': 'Conflicting effect'}}
                result, controller = self.execute(d, manifest)
                self.assertEqual(len(controller.commands), 1, result)
                self.assertEqual(len(result['unresolved_actions']), 1)


if __name__ == '__main__':
    unittest.main()
