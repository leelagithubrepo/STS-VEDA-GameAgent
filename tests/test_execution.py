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
from veda.runtime_frames import BoundedCaptureSource, retain_snapshot
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


class EphemeralFixtureSource(BoundedCaptureSource):
    """Fake capture only: copies synthetic bytes into a two-frame working cache."""
    def __init__(self, manifest, directory, *, fail_after=None):
        self.rows = iter(manifest['frames'])
        self.base, self.current = Path(directory), None
        self.capture_calls, self.closed = 0, False
        self.fail_after = fail_after
        super().__init__(capture=self._capture_fixture, max_frames=2)

    def _capture_fixture(self, directory):
        self.capture_calls += 1
        if self.fail_after is not None and self.capture_calls > self.fail_after:
            raise RuntimeError('synthetic capture unavailable')
        self.current = next(self.rows)
        path = directory / f'frame-{self.capture_calls}.dat'
        path.write_bytes((self.base / self.current['image']).read_bytes())
        return path

    def close(self):
        self.closed = True
        super().close()


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

class ActionEvidenceTests(unittest.TestCase):
    def execute(self, directory, manifest, *, source=None, controller=None,
                journal=None, **options):
        source = source or EphemeralFixtureSource(manifest, directory)
        controller = controller or ReplayController(manifest['expected_commands'])
        journal = journal or ActionJournal(Path(directory) / 'actions.jsonl')
        loop = ExecutionLoop(source, RecordedInterpreter(source), controller,
            calibration=CalibrationReport({key: 1.0 for key in RUNTIME_FIELDS}, 12),
            run_id=manifest['run_id'], max_inputs=options.pop('max_inputs', 3),
            evidence_directory=Path(directory) / 'evidence', journal=journal, **options)
        return loop.run(), source, controller, journal

    def assert_durable(self, identity):
        self.assertTrue(identity['durable'])
        path = Path(identity['image_path'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), identity['sha256'])
        self.assertFalse(Path(identity['original_image_path']).exists())

    def test_consequential_pairs_survive_cache_eviction_and_close(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            result, source, controller, journal = self.execute(directory, manifest)
            self.assertEqual(result['unresolved_actions'], [])
            self.assertTrue(source.closed)
            self.assertEqual(len(controller.commands), 3)
            attempted = [row for row in journal.rows if row['status'] == 'attempted']
            verified = [row for row in journal.rows if row['status'] == 'verified']
            self.assertFalse(attempted[0]['frame']['durable'], 'successful focus is not archived')
            for before, after in zip(attempted[1:], verified[1:]):
                self.assert_durable(before['frame'])
                self.assert_durable(after['frame'])
                self.assertEqual(before['action_id'], after['action_id'])
            self.assertEqual(len(list((Path(directory) / 'evidence').iterdir())), 3,
                             'shared action boundaries and final retention are deduplicated')

    def test_failed_navigation_retains_both_sides_without_replaying_input(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            manifest['frames'][1]['reading']['ui']['focused_card_id'] = 'd'
            result, source, controller, journal = self.execute(directory, manifest)
            self.assertEqual(len(controller.commands), 1)
            self.assertEqual(len(result['unresolved_actions']), 1)
            evidence = next(row for row in journal.rows if row['status'] == 'action_evidence')
            self.assertIsNone(evidence['action_id'], 'evidence cannot resolve/reopen an action')
            self.assert_durable(evidence['frames']['before'])
            self.assert_durable(evidence['frames']['after'])

    def test_unknown_input_retains_pair_and_is_never_replayed(self):
        class Uncertain(ReplayController):
            def call(self, command):
                self.commands.append(command)
                return {'status': 'unknown_outcome', 'request_id': command['request_id']}
        with TemporaryDirectory() as directory:
            result, source, controller, journal = self.execute(directory, contract_manifest(directory),
                controller=Uncertain())
            self.assertEqual(len(controller.commands), 1)
            self.assertEqual(len(result['unresolved_actions']), 1)
            evidence = next(row for row in journal.rows if row['status'] == 'action_evidence')
            for identity in evidence['frames'].values():
                self.assert_durable(identity)

    def test_pending_before_survives_animation_eviction_on_failed_navigation(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            for row in manifest['frames'][1:3]:
                row['reading']['ui']['phase'] = 'animating'
            manifest['frames'][3]['reading'] = copy.deepcopy(manifest['frames'][0]['reading'])
            result, source, controller, journal = self.execute(directory, manifest)
            self.assertEqual(source.capture_calls, 4)
            self.assertEqual(len(controller.commands), 1)
            self.assertIn('mismatch', result['reason'])
            evidence = next(row for row in journal.rows if row['status'] == 'action_evidence')
            self.assert_durable(evidence['frames']['before'])
            self.assertEqual(evidence['frames']['before']['sha256'], manifest['frames'][0]['sha256'])
            self.assert_durable(evidence['frames']['after'])

    def test_evidence_does_not_reopen_a_not_sent_action(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            result, source, controller, journal = self.execute(directory, manifest,
                controller=ReplayController([['right']]))
            self.assertEqual(len(controller.commands), 1)
            self.assertEqual(result['unresolved_actions'], [])
            evidence = next(row for row in journal.rows if row['status'] == 'action_evidence')
            self.assertEqual(set(evidence['frames']), {'before'})
            self.assert_durable(evidence['frames']['before'])

    def test_archive_failure_before_consequential_input_prevents_send(self):
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            manifest['frames'] = manifest['frames'][1:]
            with patch('veda.execution.retain_snapshot', side_effect=OSError('synthetic disk failure')):
                result, source, controller, journal = self.execute(directory, manifest)
            self.assertEqual(controller.commands, [])
            self.assertEqual(result['unresolved_actions'], [])
            self.assertIn('disk failure', result['reason'])
            self.assertFalse(any(row['status'] == 'attempted' for row in journal.rows))

    def test_archive_failure_after_sent_input_stays_unresolved(self):
        calls = 0
        def fail_after_before(snapshot, directory):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError('synthetic after-evidence failure')
            return retain_snapshot(snapshot, directory)
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            manifest['frames'] = manifest['frames'][1:]
            manifest['expected_commands'] = [['cross']]
            with patch('veda.execution.retain_snapshot', side_effect=fail_after_before):
                result, source, controller, journal = self.execute(directory, manifest)
            self.assertEqual(len(controller.commands), 1)
            self.assertEqual(len(result['unresolved_actions']), 1)
            self.assertFalse(any(row['status'] == 'verified' for row in journal.rows))
            self.assertTrue(result['evidence_errors'])

    def test_capture_failures_return_report_and_release_source(self):
        for successful_captures, expected_inputs in ((0, 0), (1, 1)):
            with self.subTest(after=successful_captures), TemporaryDirectory() as directory:
                manifest = contract_manifest(directory)
                source = EphemeralFixtureSource(manifest, directory, fail_after=successful_captures)
                result, source, controller, journal = self.execute(directory, manifest, source=source)
                self.assertEqual(len(controller.commands), expected_inputs)
                self.assertEqual(source.capture_calls, successful_captures + 2)
                self.assertTrue(source.closed)
                self.assertIn('capture failed after one retry', result['reason'])
                self.assertEqual(len(result['unresolved_actions']), expected_inputs)
                self.assertIn('metrics', result)

    def test_disk_failure_after_send_and_diagnostic_journal_failure_return_report(self):
        class FailingJournal(ActionJournal):
            def append(self, **row):
                if row['status'] != 'attempted':
                    raise OSError('synthetic journal disk full')
                return super().append(**row)
        calls = 0
        def fail_after_before(snapshot, directory):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError('synthetic evidence disk full')
            return retain_snapshot(snapshot, directory)
        with TemporaryDirectory() as directory:
            manifest = contract_manifest(directory)
            manifest['frames'] = manifest['frames'][1:]
            manifest['expected_commands'] = [['cross']]
            journal = FailingJournal(Path(directory) / 'actions.jsonl')
            with patch('veda.execution.retain_snapshot', side_effect=fail_after_before):
                result, source, controller, journal = self.execute(directory, manifest, journal=journal)
            self.assertEqual(len(controller.commands), 1)
            self.assertEqual(len(result['unresolved_actions']), 1)
            self.assertEqual(ActionJournal(journal.path).unresolved(), result['unresolved_actions'])
            self.assertTrue(source.closed)
            self.assertIn('evidence disk full', result['reason'])
            self.assertTrue(any(error['side'] == 'journal' for error in result['evidence_errors']))
            self.assertIn('metrics', result)



if __name__ == '__main__':
    unittest.main()
