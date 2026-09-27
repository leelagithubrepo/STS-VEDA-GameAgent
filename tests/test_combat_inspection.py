"""Independent bounded tooltip contract fixtures; no real screen or controller."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_inspect import main
from tests.test_menu_requests import make_capture
from veda import combat_inspection as inspection
from veda.reviewed_play import inventory_digest


def draft():
    return {'schema': inspection.DRAFT_SCHEMA,
        'context': {'run_id': 'run', 'floor_id': 'floor-one', 'combat_id': 'combat', 'turn_id': 'turn-one'},
        'inventory': {'current': {'card': ['Defend', 'Bash+', 'Strike', 'Defend'],
                                 'relic': ['Burning Blood'], 'potion': []},
                      'coverage': {k: 'complete' for k in ('card', 'relic', 'potion')}, 'properties': {}},
        'resources': {'hp': 80, 'max_hp': 80, 'energy': 2, 'block': 0, 'gold': 99},
        'facts': {'act': 1, 'floor': 1, 'ascension': 2, 'character': 'Ironclad',
                  'player': {'strength': 0, 'dexterity': 0, 'powers': None},
                  'hand': [{'id': 'c1', 'name': 'Defend'}, {'id': 'c2', 'name': 'Bash+'},
                           {'id': 'c3', 'name': 'Strike'}, {'id': 'c4', 'name': 'Defend'}],
                  'enemies': [{'id': 'left', 'name': 'Louse', 'hp': 9, 'max_hp': 15, 'block': 4, 'intent_hits': [8]},
                              {'id': 'right', 'name': None, 'hp': 17, 'max_hp': 17, 'block': None, 'intent_hits': [7]}],
                  'unknowns': ['Right enemy species and blue icon meaning are occluded.']},
        'ui': {'screen': 'combat', 'phase': 'tooltip', 'tooltip_visible': True,
               'focused_card_id': None, 'selected_card_id': None, 'focused_target_id': None,
               'selected_target_id': None, 'tooltip_subject_id': 'left'},
        'reasoning': 'Dismiss observed tooltip, then inspect the newly visible UI before selecting any card.'}


class CombatInspectionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.now = datetime(2026, 9, 27, 22, 0, 0, tzinfo=timezone.utc)
        self.value = draft()

    def observation(self, value=None, after=False):
        value = deepcopy(self.value if value is None else value)
        frame = {'frame_id': 'after' if after else 'before', 'image_sha256': ('b' if after else 'a') * 64,
                 'observed_at': (self.now + timedelta(seconds=1 if after else 0)).isoformat()}
        obs = {'schema': inspection.OBSERVATION_SCHEMA, 'frame': frame,
               'review': {'kind': 'reviewed_choice_ui', 'complete': True, 'reviewer': 'Fixture',
                          'frame_id': frame['frame_id'], 'image_sha256': frame['image_sha256']},
               'context': value['context'], 'inventory_digest': inventory_digest(value['inventory']),
               'resources': value['resources'], 'facts': value['facts'], 'ui': value['ui']}
        if after:
            obs['ui'].update(phase='hand', tooltip_visible=False, tooltip_subject_id=None, focused_card_id='c1')
            obs['review']['outcome'] = {'action_id': 'action', 'before_frame_id': 'before', 'before_sha256': 'a' * 64,
                                       'inspection': 'clear_tooltip', 'observed_result': 'Tooltip visibly cleared.'}
        return obs

    def plan(self, value=None, **kwargs):
        return inspection.plan_tooltip_clear(self.observation(value), control_profile=inspection.CONTROL_PROFILE,
                                            action_id='action', now=self.now, **kwargs)

    def test_unknown_enemy_does_not_block_one_up_or_authorize_card_or_end_turn(self):
        obs = self.observation()
        before = deepcopy(obs)
        plan = self.plan()
        self.assertEqual({'action': 'tap', 'buttons': ['up'], 'request_id': 'action'}, plan['command'])
        self.assertEqual('clear_tooltip', plan['step_kind'])
        self.assertFalse(plan['controller_authorized']); self.assertFalse(plan['runtime_authorized'])
        result = inspection.verify_tooltip_clear(plan, obs, self.observation(after=True), now=self.now+timedelta(seconds=1))
        self.assertTrue(result['step_verified']); self.assertFalse(result['logical_action_complete'])
        self.assertTrue(result['requires_new_combat_review']); self.assertEqual(before, obs)

    def test_selected_card_or_target_false_tooltip_and_assumed_focus_rejected(self):
        variants = [{'selected_card_id': 'c1'}, {'focused_target_id': 'left'}, {'selected_target_id': 'left'},
                    {'focused_card_id': 'c1'}, {'tooltip_visible': False}, {'tooltip_subject_id': None},
                    {'screen': 'map'}, {'phase': 'targeting'}]
        for change in variants:
            value = deepcopy(self.value); value['ui'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.plan(value)

    def test_wrong_profile_or_disabled_freshness_rejected(self):
        for profile in ('unknown', None):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                inspection.plan_tooltip_clear(self.observation(), control_profile=profile, now=self.now)
        for age in (None, 31, 0, True, float('inf')):
            with self.subTest(age=age), self.assertRaises(ValueError):
                self.plan(max_age_seconds=age)
        with self.assertRaises(ValueError):
            inspection.plan_tooltip_clear(self.observation(), control_profile=inspection.CONTROL_PROFILE,
                                         now=self.now+timedelta(seconds=31))

    def test_fresh_capture_does_not_reset_consumed_one_attempt_budget(self):
        before = self.observation()
        progress = inspection.record_inspection_attempt(None, before, 'action')
        replacement = deepcopy(before); replacement['frame'].update(frame_id='new', image_sha256='c'*64)
        replacement['review'].update(frame_id='new', image_sha256='c'*64)
        self.assertEqual(inspection.inspection_scope(before), inspection.inspection_scope(replacement))
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            inspection.plan_tooltip_clear(replacement, control_profile=inspection.CONTROL_PROFILE,
                                         now=self.now, progress=json.loads(json.dumps(progress)))
        self.assertEqual(progress, inspection.record_inspection_attempt(progress, before, 'action'))
        with self.assertRaises(ValueError):
            inspection.record_inspection_attempt(progress, before, 'other-action')

    def test_result_must_leave_tooltip_and_preserve_every_known_fact(self):
        plan, before = self.plan(), self.observation()
        mutations = [lambda a: a['resources'].update(energy=1),
                     lambda a: a['facts']['enemies'][1].update(name='Louse'),
                     lambda a: a['facts']['player'].update(strength=1),
                     lambda a: a['facts'].update(unknowns=[]),
                     lambda a: a['context'].update(turn_id='later-turn'),
                     lambda a: a.update(inventory_digest='d'*64),
                     lambda a: a['ui'].update(selected_card_id='c1'),
                     lambda a: a['ui'].update(phase='tooltip', tooltip_visible=True, tooltip_subject_id='left', focused_card_id=None)]
        for mutation in mutations:
            after = self.observation(after=True); mutation(after)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                inspection.verify_tooltip_clear(plan, before, after, now=self.now+timedelta(seconds=1))

    def test_result_and_proposal_require_exact_source_and_action(self):
        before, plan = self.observation(), self.plan()
        for change in ({'action_id': 'wrong'}, {'before_sha256': 'e'*64}, {'before_frame_id': 'wrong'}):
            after = self.observation(after=True); after['review']['outcome'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                inspection.verify_tooltip_clear(plan, before, after, now=self.now+timedelta(seconds=1))
        after = self.observation(after=True); after['frame']['image_sha256'] = 'a'*64; after['review']['image_sha256'] = 'a'*64
        with self.assertRaises(ValueError):
            inspection.verify_tooltip_clear(plan, before, after, now=self.now+timedelta(seconds=1))
        changed = deepcopy(plan); changed['command']['buttons'] = ['triangle']
        with self.assertRaises(ValueError):
            inspection.validate_inspection_proposal(changed, before, now=self.now)

    def request(self):
        image = make_capture(self.root, self.now-timedelta(milliseconds=200), index='a')
        output = self.root/'request.json'
        result = inspection.write_inspection_request(self.value, capture=image, reviewer='Fixture', evidence_note='Synthetic inspected tooltip.',
            reviewed=True, output=output, execute=True, now=self.now)
        self.assertEqual(str(output), result['request_file'])
        return json.loads(output.read_text()), image

    def pending(self):
        request, _ = self.request()
        proposal = inspection.plan_tooltip_clear(request['observation'], control_profile=inspection.CONTROL_PROFILE,
                                               action_id='action', now=self.now)
        pending = {'action_id': 'action', 'status': 'attempted', 'request': request, 'proposal': proposal,
                   'attempted_at': self.now.isoformat()}
        session = self.root/'state.json'
        session.write_text(json.dumps({'schema': 'veda.reviewed-play.v1', 'run_id': 'run', 'pending': pending}))
        return session

    def test_source_free_validation_has_no_source_or_controller_authority(self):
        with patch('veda.play_requests.reviewed_capture_source', side_effect=AssertionError('capture access')):
            result = inspection.validate_inspection_draft(self.value)
        self.assertTrue(result['validation_only']); self.assertFalse(result['dispatchable'])
        self.assertFalse(set(result)&{'source','command','request_file','operation'})
        bad = deepcopy(self.value); bad['source'] = {}
        with self.assertRaises(ValueError): inspection.validate_inspection_draft(bad)

    def test_request_receipt_binding_is_exclusive_and_declares_execute_without_sending(self):
        packet, image = self.request()
        self.assertEqual('execute', packet['operation']); self.assertEqual('combat_inspection', packet['kind'])
        self.assertEqual(packet['source']['sha256'], packet['observation']['frame']['image_sha256'])
        self.assertEqual(packet['review'], packet['observation']['review'])
        with self.assertRaises(FileExistsError):
            inspection.write_inspection_request(self.value, capture=image, reviewer='Fixture', evidence_note='Synthetic',
                reviewed=True, output=self.root/'request.json', now=self.now)
        with self.assertRaises(ValueError):
            inspection.write_inspection_request(self.value, capture=image, reviewer='Fixture', evidence_note='Synthetic',
                reviewed=True, output=self.root/'stale.json', now=self.now+timedelta(seconds=31))
        self.assertFalse((self.root/'stale.json').exists())

    def test_compact_result_derives_exact_pending_and_preserves_unknowns(self):
        session = self.pending()
        result = {'schema': inspection.RESULT_SCHEMA, 'action_id': 'action',
                  'ui': self.observation(after=True)['ui'], 'observed_result': 'Tooltip cleared with no game-state change.'}
        self.assertTrue(inspection.validate_inspection_result(result, session=session, action_id='action')['result_valid'])
        later = self.now + timedelta(seconds=2)
        image = make_capture(self.root, later-timedelta(milliseconds=200), color=(90,80,70), index='b')
        output = self.root/'result.json'; original = session.read_bytes()
        inspection.write_inspection_result(result, session=session, action_id='action', capture=image,
            reviewer='Fixture', evidence_note='Synthetic cleared hand review.', reviewed=True, output=output, now=later)
        packet = json.loads(output.read_text())
        self.assertEqual('verify', packet['operation']); self.assertEqual({}, packet['telemetry'])
        self.assertEqual(self.value['facts'], packet['after']['observation']['facts'])
        self.assertEqual(original, session.read_bytes())
        with self.assertRaises(ValueError):
            inspection.validate_inspection_result(result, session=session, action_id='other')
        bad = deepcopy(result); bad['facts'] = 'invented'
        with self.assertRaises(ValueError):
            inspection.validate_inspection_result(bad, session=session, action_id='action')

    def test_verified_pending_cannot_be_packaged_as_a_second_verification(self):
        session = self.pending(); data=json.loads(session.read_text()); data['pending']['status']='verified_pending_log'
        session.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            inspection.validate_inspection_result({'schema': inspection.RESULT_SCHEMA}, session=session, action_id='action')

    def test_cli_validation_and_invalid_combinations(self):
        source = self.root/'draft.json'; source.write_text(json.dumps(self.value))
        output = io.StringIO()
        with patch('sys.stdout', output):
            self.assertEqual(0, main(['--draft',str(source),'--validate','--control-profile',inspection.CONTROL_PROFILE]))
        self.assertFalse(json.loads(output.getvalue())['dispatchable'])
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            main(['--draft',str(source),'--validate','--execute','--control-profile',inspection.CONTROL_PROFILE])


if __name__ == '__main__':
    unittest.main()
