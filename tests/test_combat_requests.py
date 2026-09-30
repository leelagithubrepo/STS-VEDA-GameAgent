"""Compact reviewed combat contracts, synthetic images/SQLite and fake inputs only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_execution import context as combat_context
from tests.test_menu_requests import make_capture
from tests import test_reviewed_play as runtime
from veda.combat_requests import (read_combat_draft, validate_combat_draft, validate_combat_result,
                                  write_combat_request, write_combat_result)
from veda.execution import Reading
import veda.combat_requests as requests


def draft(ids=None):
    """Canonical synthetic example for documentation; never a current-game claim."""
    context = combat_context(); state = context['state']
    state.pop('schema'); state.pop('observed_at')
    state.update(character='Ironclad', ascension=0, act=1, floor=1, gold=99, turn=1)
    for enemy in state['enemies']:
        enemy['intent_damage_confidence'] = 1.0
    inventory = {'current': {'card': ['Strike', 'Defend'], 'relic': [], 'potion': []},
        'coverage': {'card': 'complete', 'relic': 'complete', 'potion': 'complete'}, 'properties': {}}
    return {'schema': 'veda.combat-draft.v1',
        'context': ids or {'run_id': 'example-run', 'floor_id': 'example-floor', 'combat_id': 'example-combat', 'turn_id': 'example-turn'},
        'state': state, 'inventory': inventory, 'encounter': {'name': 'Cultist', 'type': 'enemy', 'confidence': 1.0},
        'perception': {'confidence': 1.0, 'end_turn_damage_confidence': 1.0}, 'unknowns': [],
        'ui': {'phase': 'hand', 'focused_card_id': 's', 'selected_card_id': None,
               'focus_domain': 'hand', 'tooltip_kind': 'none', 'focused_subject_id': None,
               'focus_evidence_note': 'Synthetic focused card declaration.',
               'focused_target_id': None, 'hand_order': ['s', 'd'], 'target_order': ['enemy']},
        'plan': {'steps': [{'kind': 'card', 'card_id': 'd'}]},
        'reasoning': 'Synthetic example only: focus and then play the observed Defend; not game evidence.'}


def result(action_id, *, focus='d', state='unchanged', phase='hand'):
    return {'schema': 'veda.combat-result.v1', 'action_id': action_id, 'state': deepcopy(state),
        'inventory': 'unchanged', 'encounter': 'unchanged',
        'perception': {'confidence': 1.0, 'end_turn_damage_confidence': 1.0}, 'unknowns': [],
        'ui': {'phase': phase, 'focused_card_id': focus, 'selected_card_id': 'd' if phase == 'card_selected' else None,
               'focus_domain': 'hand' if focus else 'none', 'tooltip_kind': 'none', 'focused_subject_id': None,
               'focus_evidence_note': 'Synthetic actual focused card declaration.',
               'focused_target_id': None, 'hand_order': ['s', 'd'], 'target_order': ['enemy']},
        'observed_result': 'Synthetic inspected after-state and UI; no live game was controlled.'}


class CombatDraftTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name).resolve()
        self.now = datetime.now(timezone.utc) - timedelta(seconds=2)
        self.image = make_capture(self.root, self.now - timedelta(seconds=2))
        self.output = self.root / 'request.json'; self.value = draft()

    def bind(self, **kwargs):
        args = dict(capture=self.image, reviewer='Synthetic reviewer', evidence_note='Exact synthetic image reviewed.',
                    reviewed=True, output=self.output, now=self.now)
        args.update(kwargs)
        return write_combat_request(self.value, **args)

    def test_validation_before_capture_is_non_authorizing_and_previews_atomic_input(self):
        before = deepcopy(self.value)
        with patch('veda.combat_requests.reviewed_capture_source', side_effect=AssertionError('capture accessed')), \
             patch('pathlib.Path.open', side_effect=AssertionError('file accessed')), \
             patch('veda.bridge_client.BridgeClient', side_effect=AssertionError('controller accessed')):
            checked = validate_combat_draft(self.value)
        self.assertEqual(before, self.value)
        self.assertEqual({'buttons': ['right'], 'expected_kind': 'card_focus'}, checked['next_atomic_input_preview'])
        self.assertTrue(checked['draft_valid']); self.assertFalse(checked['dispatchable'])
        self.assertFalse(set(checked) & {'reading', 'source', 'operation', 'command', 'request_file'})

    def test_one_state_derives_matching_reading_hand_statuses_inventory_and_source(self):
        self.bind(); packet = json.loads(self.output.read_text()); reading = Reading.from_dict(packet['reading'])
        self.assertEqual(('Strike', 'Defend'), reading.state.hand)
        self.assertEqual(self.value['state']['strength'], reading.state.player_strength)
        self.assertEqual(packet['source']['captured_at'], reading.context['state']['observed_at'])
        self.assertEqual(packet['source']['sha256'], reading.image_sha256)
        self.assertEqual(packet['review']['frame_id'], reading.frame_id)
        self.assertEqual(self.value['inventory'], reading.context['inventory'])
        self.assertEqual('combat', reading.ui['screen_type'])
        self.assertEqual(6, reading.state.enemies[0].intent_total_damage)

    def test_schema_source_and_duplicate_state_fields_cannot_be_injected(self):
        edits = [lambda v:v.update(source={}), lambda v:v.update(review={}), lambda v:v.update(reading={}),
                 lambda v:v['state'].update(observed_at=self.now.isoformat()),
                 lambda v:v['state'].update(schema='spire.advisory.v1'),
                 lambda v:v['ui'].update(buttons=['cross']), lambda v:v['context'].update(state={})]
        for edit in edits:
            value = deepcopy(self.value); edit(value)
            with self.subTest(edit=edit), self.assertRaises((ValueError, RuntimeError)): validate_combat_draft(value)

    def test_illegal_card_ui_cost_or_missing_confidence_is_diagnosed_before_source(self):
        edits = [lambda v:v['plan']['steps'][0].update(card_id='missing'),
                 lambda v:v['state'].update(energy=0), lambda v:v['ui'].update(hand_order=['d','s']),
                 lambda v:v['ui'].update(focused_card_id=None), lambda v:v['ui'].update(phase='animating'),
                 lambda v:v['state']['enemies'][0].pop('intent_damage_confidence'),
                 lambda v:v['state']['enemies'][0].update(intent_total_damage=8),
                 lambda v:v['perception'].update(confidence=.1),
                 lambda v:v['state']['hand'][1].update(cost=None),
                 lambda v:v['inventory']['coverage'].update(relic='unknown')]
        for edit in edits:
            value = deepcopy(self.value); edit(value)
            with (self.subTest(edit=edit), patch('veda.combat_requests.reviewed_capture_source', side_effect=AssertionError('capture')),
                  self.assertRaises((ValueError, RuntimeError))):
                write_combat_request(value, capture='', reviewer='', evidence_note='', reviewed=False, output=self.output)

    def test_generic_tooltip_cannot_be_mistaken_for_a_clearable_hand_focus(self):
        self.value['ui'].update(phase='tooltip', focused_card_id=None)
        with self.assertRaisesRegex(RuntimeError, 'focus domain'):
            validate_combat_draft(self.value)

    def test_exact_image_review_original_age_and_exclusive_output_remain_required(self):
        for args in ({'reviewed':False}, {'now':self.now+timedelta(seconds=31)},
                     {'now':self.now-timedelta(seconds=10)}):
            with self.subTest(args=args), self.assertRaises(ValueError): self.bind(**args)
            self.assertFalse(self.output.exists())
        self.bind()
        with self.assertRaises(FileExistsError): self.bind()
        with self.assertRaises(ValueError): self.bind(output=self.image)

    def test_execute_only_changes_operation_and_still_never_sends(self):
        with patch('socket.socket', side_effect=AssertionError('network')):
            self.bind(execute=True)
        packet = json.loads(self.output.read_text()); self.assertEqual('execute', packet['operation'])
        self.assertNotIn('armed', packet)

    def test_upgrade_confirmation_uses_observed_triangle_button(self):
        value = deepcopy(self.value)
        value['ui'].update(
            phase='card_selected', focused_card_id='d', selected_card_id='d',
            upgrade_confirm_button='triangle', hand_order=['s', 'd'])
        value['plan'] = {'steps': [{'kind': 'card', 'card_id': 'd'}]}
        checked = validate_combat_draft(value)
        self.assertEqual({'buttons': ['triangle'], 'expected_kind': 'upgrade_confirm'},
                         checked['next_atomic_input_preview'])

    def test_duplicate_or_nonfinite_or_oversized_json_is_rejected(self):
        path = self.root / 'bad.json'
        for payload in ('{"a":1,"a":2}', '{"a":NaN}', ' ' * 256001):
            path.write_text(payload)
            with self.assertRaises(ValueError): read_combat_draft(path)

    def test_source_change_during_binding_does_not_publish(self):
        original = requests.reviewed_capture_source; count = 0
        def changed(**kwargs):
            nonlocal count
            count += 1
            if count == 2:
                self.image.write_bytes(runtime.png_bytes((91, 22, 33)))
            return original(**kwargs)
        with patch.object(requests, 'reviewed_capture_source', side_effect=changed), self.assertRaises(ValueError):
            self.bind()
        self.assertFalse(self.output.exists())

    def test_cli_validates_before_capture_and_reports_a_specific_blocker(self):
        from scripts.veda_combat import main
        from contextlib import redirect_stdout
        from io import StringIO
        path = self.root / 'draft.json'; path.write_text(json.dumps(self.value))
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--draft', str(path), '--validate'])
        self.assertEqual(0, code); self.assertTrue(json.loads(output.getvalue())['draft_valid'])
        self.value['ui']['focused_card_id'] = None; path.write_text(json.dumps(self.value)); output = StringIO()
        with redirect_stdout(output):
            code = main(['--draft', str(path), '--validate'])
        self.assertEqual(2, code)
        self.assertIn('focus', json.loads(output.getvalue())['reason'])


class CombatResultTests(unittest.TestCase):
    def setUp(self):
        self.fx = runtime.ReviewedPlayTests(methodName='runTest'); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)
        self.fx.base_time -= timedelta(seconds=40)
        earlier = (self.fx.base_time - timedelta(seconds=1)).isoformat()
        with self.fx.db._connection() as con:
            con.execute('UPDATE runs SET started_at=?', (earlier,))
            con.execute('UPDATE combats SET opened_at=?', (earlier,))
            con.execute('UPDATE combat_turns SET opened_at=?', (earlier,))
            con.execute('UPDATE inventory_baselines SET observed_at=?', (earlier,))
        self.root = self.fx.root.resolve(); self.number = 0
        self.value = draft(self.fx.context_ids)
        self.session = self.fx.create()

    def packet(self, value, *, result_mode=False):
        self.number += 1
        # Fixtures always precede the injected adapter clock, including receipt completion.
        self.fx.now = self.fx.base_time + timedelta(seconds=3 * self.number)
        image = make_capture(self.root, self.fx.now-timedelta(seconds=1), index=format(self.number % 16, 'x'),
                             color=(30+self.number, 40, 50))
        output = self.root / f'packet-{self.number}.json'
        kwargs = dict(capture=image, reviewer='Synthetic reviewer', evidence_note='Synthetic exact-image result reviewed.',
                      reviewed=True, output=output, now=self.fx.now)
        if result_mode:
            write_combat_result(value, session=self.session.path, action_id=value['action_id'], **kwargs)
        else:
            write_combat_request(value, **kwargs)
        return json.loads(output.read_text())

    def prepare_send(self, value=None, *, arm=False):
        request = self.packet(value or self.value)
        if arm:
            self.fx.before = request
            arm_request = self.fx.arm_request(); arm_request['frame_id'] = request['reading']['frame_id']
            self.session.handle(arm_request)
        prepared = self.session.handle(request)
        self.session.handle({'operation':'send','action_id':prepared['action_id']})
        return prepared

    def test_defend_focus_selection_play_and_zone_logging_through_real_adapter(self):
        self.fx.db.start_combat_zones(combat_id=self.fx.context_ids['combat_id'], deck=['Strike','Defend'],
                                     hand=['Strike','Defend'], source='Synthetic opening')
        focus = self.prepare_send(arm=True)
        after = result(focus['action_id'])
        self.assertTrue(validate_combat_result(after, session=self.session.path, action_id=focus['action_id'])['result_valid'])
        response = self.session.handle(self.packet(after, result_mode=True)); self.assertFalse(response['logical_action_complete'])
        self.assertTrue(self.fx.db.combat_zone_state(combat_id=self.fx.context_ids['combat_id'])['known'])
        self.value['ui']['focused_card_id'] = 'd'
        selection = self.prepare_send()
        response = self.session.handle(self.packet(result(selection['action_id'], phase='card_selected'), result_mode=True))
        self.assertFalse(response['logical_action_complete'])
        self.assertTrue(self.fx.db.combat_zone_state(combat_id=self.fx.context_ids['combat_id'])['known'])
        self.value['ui'].update(phase='card_selected', selected_card_id='d')
        played = self.prepare_send()
        state = deepcopy(self.value['state']); state['hand'] = state['hand'][:1]; state.update(energy=0, block=5)
        state['piles']['discard'] = ['Defend']
        observed = result(played['action_id'], focus='s', state=state)
        observed['ui']['hand_order'] = ['s']; observed['card_destination'] = 'discard'
        observed['telemetry'] = {'zone_coverage':'complete'}
        response = self.session.handle(self.packet(observed, result_mode=True))
        self.assertTrue(response['logical_action_complete']); self.assertIsNone(self.session.state['pending'])
        self.assertEqual([['right'],['cross'],['cross']], [c['buttons'] for c in self.fx.controller.inputs])
        zones = self.fx.db.combat_zone_state(combat_id=self.fx.context_ids['combat_id'])
        self.assertEqual(['Defend'], zones['zones']['discard'])

    def test_wrong_focus_mutations_or_state_preserve_attempted_action_without_repeat(self):
        prepared = self.prepare_send(arm=True)
        for edit in (lambda v:v['ui'].update(focused_card_id='s'),
                     lambda v:v.update(state=dict(self.value['state'], hp=39)),
                     lambda v:v.update(telemetry={'zone_events':[{'kind':'play'}]}),
                     lambda v:v.update(card_destination='discard')):
            observed = result(prepared['action_id']); edit(observed)
            with self.subTest(edit=edit), self.assertRaises((ValueError, RuntimeError)):
                validate_combat_result(observed, session=self.session.path, action_id=prepared['action_id'])
            self.assertEqual('attempted', self.session.state['pending']['status'])
        self.assertEqual(1, len(self.fx.controller.inputs))

    def test_result_explanation_accepts_normal_paragraph_and_names_oversize_error(self):
        prepared=self.prepare_send(arm=True)
        observed=result(prepared['action_id'])
        observed['observed_result']='Observed focus changed; no game effects. ' * 10
        self.assertGreater(len(observed['observed_result'].encode()),256)
        self.assertTrue(validate_combat_result(observed,session=self.session.path,
                                              action_id=prepared['action_id'])['result_valid'])
        for note in ('', 'x'*2049, 'é'*1025):
            observed['observed_result']=note
            with self.subTest(note_length=len(note)),self.assertRaisesRegex(ValueError,'2048 UTF-8 bytes'):
                validate_combat_result(observed,session=self.session.path,action_id=prepared['action_id'])
        self.assertEqual(1,len(self.fx.controller.inputs))

    def test_wrong_action_or_unattempted_or_finalization_state_cannot_be_packaged(self):
        prepared = self.prepare_send(arm=True); observed = result(prepared['action_id'])
        with self.assertRaises(ValueError): validate_combat_result(observed, session=self.session.path, action_id='other')
        copy_path = self.root / 'other-state.json'; state = json.loads(self.session.path.read_text())
        for status in ('prepared', 'verified_pending_log'):
            state['pending']['status'] = status; copy_path.write_text(json.dumps(state))
            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_combat_result(observed, session=copy_path, action_id=prepared['action_id'])

    def test_combat_reward_boundary_closes_rows_without_inventing_next_floor(self):
        self.value['plan']['steps'][0] = {'kind':'card','card_id':'s','target':'enemy'}
        self.value['state']['enemies'][0]['hp'] = 6
        self.value['ui'].update(phase='targeting', selected_card_id='s', focused_target_id='enemy', focus_domain='enemy')
        prepared = self.prepare_send(arm=True)
        observed = {'schema':'veda.combat-result.v1','action_id':prepared['action_id'], 'inventory':'unchanged',
            'observed_result':'Synthetic reward screen confirms this combat victory.',
            'boundary':{'screen':'reward','resources':{'hp':40,'max_hp':80,'gold':99},
                'facts':{'combat_outcome':'win'},'choice_id':'reward-after-combat','layout_id':'synthetic-reward',
                'options':[],'focused_id':None}}
        response = self.session.handle(self.packet(observed, result_mode=True))
        self.assertEqual({**self.fx.context_ids,'combat_id':None,'turn_id':None}, response['next_context'])
        self.assertIsNone(self.session.state['pending'])
        with self.fx.db._connection() as con:
            self.assertEqual(1, con.execute('SELECT COUNT(*) FROM floors').fetchone()[0])
            self.assertIsNotNone(con.execute('SELECT closed_at FROM combats').fetchone()[0])

    def test_headbutt_boundary_accepts_observed_selection_without_inventing_its_choice(self):
        self.value['state']['hand'][0]['name'] = 'Headbutt'
        self.value['state']['piles']['discard'] = ['Strike']
        self.value['inventory']['current']['card'] = ['Headbutt', 'Defend', 'Strike']
        self.value['plan']['steps'][0] = {'kind':'card','card_id':'s','target':'enemy','return_card':'Strike'}
        self.value['ui'].update(phase='targeting', selected_card_id='s', focused_target_id='enemy', focus_domain='enemy')
        prepared = self.prepare_send(arm=True)
        observed = {'schema':'veda.combat-result.v1','action_id':prepared['action_id'], 'inventory':'unchanged',
            'observed_result':'Synthetic Headbutt selection prompt appeared with Strike selectable.',
            'boundary':{'screen':'selection','resources':{'hp':40,'energy':0},
                'facts':{'selection_cause_card_id':'s'},'choice_id':'headbutt-return','layout_id':'synthetic-discard',
                'options':[{'id':'discard-strike','label':'Strike','enabled':True,'costs':{}}],
                'focused_id':'discard-strike'}}
        packet = self.packet(observed, result_mode=True)
        self.assertEqual({}, packet['telemetry']); self.assertEqual(0, packet['after']['observation']['ui']['required_count'])
        response = self.session.handle(packet)
        self.assertEqual(self.fx.context_ids, response['next_context'])
        self.assertTrue(response['logical_action_complete'])

    def test_boundary_strips_semantic_option_annotations(self):
        self.value['plan']['steps'][0] = {'kind':'card','card_id':'s','target':'enemy'}
        self.value['state']['enemies'][0]['hp'] = 1
        self.value['ui'].update(phase='targeting', selected_card_id='s', focused_target_id='enemy', focus_domain='enemy')
        prepared = self.prepare_send(arm=True)
        observed = {'schema':'veda.combat-result.v1','action_id':prepared['action_id'], 'inventory':'unchanged',
            'observed_result':'Synthetic reward boundary with annotated options.',
            'boundary':{'screen':'reward','resources':{'hp':40,'max_hp':80,'gold':99},
                'facts':{'combat_outcome':'win'},'choice_id':'reward-after-combat','layout_id':'synthetic-reward',
                'options':[{'id':'gold','label':'16 Gold','enabled':True,'costs':{},'role':'gold',
                            'reward':{'amount':16}}],'focused_id':'gold'}}
        response = self.session.handle(self.packet(observed, result_mode=True))
        self.assertTrue(response['logical_action_complete'])

    def test_result_requires_distinct_post_dispatch_capture_and_explicit_review(self):
        prepared = self.prepare_send(arm=True); observed = result(prepared['action_id'])
        source = self.session.state['pending']['request']['source']
        current = self.fx.now + timedelta(seconds=3)
        for label, captured, color, reviewed in (
                ('old', self.fx.now-timedelta(seconds=2), (99, 2, 3), True),
                ('same', self.fx.now+timedelta(seconds=1), (31, 40, 50), True),
                ('unreviewed', self.fx.now+timedelta(seconds=1), (99, 2, 3), False)):
            image = make_capture(self.root, captured, index=label[0], color=color); output = self.root/(label+'.json')
            with self.subTest(label=label), self.assertRaises(ValueError):
                write_combat_result(observed, session=self.session.path, action_id=prepared['action_id'],
                    capture=image, reviewer='Synthetic reviewer', evidence_note='Synthetic result', reviewed=reviewed,
                    output=output, now=current)
            self.assertFalse(output.exists())

    def test_pending_cas_change_prevents_output(self):
        prepared = self.prepare_send(arm=True); observed = result(prepared['action_id'])
        original = requests.read_pending; calls = 0
        def changed(*args):
            nonlocal calls
            calls += 1
            result = original(*args)
            return (result[0], 'changed') if calls >= 3 else result
        with patch.object(requests, 'read_pending', side_effect=changed), self.assertRaisesRegex(ValueError, 'pending action changed'):
            self.packet(observed, result_mode=True)
        self.assertFalse((self.root / 'packet-2.json').exists())

    def test_next_turn_derives_lifecycle_from_actual_state_and_retains_before_as_historical(self):
        self.value['state']['block'] = 6
        self.value['ui']['focused_card_id'] = None
        self.value['ui']['focus_domain'] = 'none'
        self.value['plan']['steps'] = [{'kind':'end_turn'}]
        prepared = self.prepare_send(arm=True)
        state = deepcopy(self.value['state']); state.update(turn=2, energy=3, block=0)
        observed = result(prepared['action_id'], state=state, focus='s')
        observed['next_turn'] = {'turn_number':2}
        packet = self.packet(observed, result_mode=True)
        self.assertNotEqual(self.fx.context_ids['turn_id'], packet['after']['context']['turn_id'])
        events = packet['telemetry']['transitions']
        self.assertEqual(['end_turn','start_turn'], [e['kind'] for e in events])
        self.assertIn('last_observed_before_end_turn', events[0]['closing_state'])
        response = self.session.handle(packet)
        self.assertTrue(response['logical_action_complete']); self.assertIsNone(self.session.state['pending'])
        self.assertNotEqual(packet['after']['context']['turn_id'], response['next_context']['turn_id'])
        from veda.combat_flow import last_snapshot
        reused = last_snapshot(self.session.path)
        self.assertEqual(response['next_context'], reused['context'])
        self.assertNotIn('plan', reused)
        with self.fx.db._connection() as con:
            self.assertEqual(2, con.execute('SELECT MAX(turn_number) FROM combat_turns').fetchone()[0])

    def test_next_turn_cannot_mask_navigation_or_skip_known_turn(self):
        prepared = self.prepare_send(arm=True)
        state = deepcopy(self.value['state']); state.update(turn=2)
        observed = result(prepared['action_id'], state=state); observed['next_turn'] = {'turn_number':2}
        with self.assertRaisesRegex(ValueError, 'next_turn requires'):
            validate_combat_result(observed, session=self.session.path, action_id=prepared['action_id'])


if __name__ == '__main__':
    unittest.main()
