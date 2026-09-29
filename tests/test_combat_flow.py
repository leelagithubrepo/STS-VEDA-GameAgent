from copy import deepcopy
import unittest
from tests import test_combat_requests as fixtures
from tests.test_combat_requests import draft
from veda.combat_requests import validate_combat_draft
from veda.combat_input import FOCUS_CONTROL_PROFILE
from veda.combat_flow import last_snapshot, observed_result, snapshot_from_result


class CombatFlowTests(unittest.TestCase):
    def fixture(self):
        f = fixtures.CombatResultTests(methodName='runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        return f

    def test_bounded_navigation_caps_at_four_and_selection_stays_separate(self):
        v = draft(); template = v['state']['hand'][0]
        v['state']['hand'] = [dict(template, id='s'+str(i)) for i in range(7)]
        v['ui'].update(hand_order=['s'+str(i) for i in range(7)], focused_card_id='s0',
                       navigation_mode='bounded_hand', control_profile=FOCUS_CONTROL_PROFILE)
        v['plan'] = {'steps':[{'kind':'card','card_id':'s6','target':'enemy'}]}
        self.assertEqual(['right']*4, validate_combat_draft(v)['next_atomic_input_preview']['buttons'])
        v['ui']['focused_card_id'] = 's4'
        self.assertEqual(['right']*2, validate_combat_draft(v)['next_atomic_input_preview']['buttons'])
        v['ui']['focused_card_id'] = 's6'
        self.assertEqual(['cross'], validate_combat_draft(v)['next_atomic_input_preview']['buttons'])
        v['ui']['navigation_mode'] = 'single'; v['ui']['focused_card_id'] = 's0'
        self.assertEqual(['right'], validate_combat_draft(v)['next_atomic_input_preview']['buttons'])

    def test_reuse_through_focus_select_play_and_decision_expires(self):
        f = self.fixture()
        f.fx.db.start_combat_zones(combat_id=f.fx.context_ids['combat_id'], deck=['Strike','Defend'],
                                  hand=['Strike','Defend'], source='Synthetic opening')
        f.prepare_send(arm=True)
        review = observed_result(f.session.path, note='Observed Defend focus.', unchanged=True, focus='d')
        packet = f.packet(review, result_mode=True); f.session.handle(packet)
        current = last_snapshot(f.session.path)
        self.assertEqual(f.value['plan'], current['plan']); self.assertEqual('d',current['ui']['focused_card_id'])
        tampered = deepcopy(packet); tampered['after']['reading']['context']['state']['hp'] -= 1
        with self.assertRaisesRegex(ValueError, 'sealed'): snapshot_from_result(tampered, f.session.path)
        f.prepare_send(current)
        with self.assertRaisesRegex(ValueError, 'already-verified'): last_snapshot(f.session.path)
        f.session.handle(f.packet(observed_result(f.session.path, note='Defend selected.', unchanged=True, selected='d'), result_mode=True))
        current = last_snapshot(f.session.path); self.assertEqual('card_selected',current['ui']['phase'])
        f.prepare_send(current)
        state = deepcopy(current['state']); state['hand'] = state['hand'][:1]; state.update(energy=0, block=5)
        state['piles']['discard'] = ['Defend']
        ui = deepcopy(current['ui']); ui.update(phase='hand',selected_card_id=None,focused_card_id='s',hand_order=['s'])
        actual = {'state':state,'ui':ui,'others_unchanged':True,'card_destination':'discard'}
        after = observed_result(f.session.path, note='Defend resolved: 0 energy, 5 Block.', actual=actual)
        response = f.session.handle(f.packet(after,result_mode=True))
        self.assertTrue(response['logical_action_complete'])
        current = last_snapshot(f.session.path); self.assertNotIn('plan',current)
        self.assertEqual(0,current['state']['energy'])
        self.assertEqual([['right'],['cross'],['cross']], [c['buttons'] for c in f.fx.controller.inputs])

    def test_compact_result_requires_actual_unchanged_attestation(self):
        f = self.fixture(); f.prepare_send(arm=True)
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            observed_result(f.session.path, note='Focus.', focus='d')
        with self.assertRaisesRegex(ValueError, 'actual state'):
            observed_result(f.session.path,note='Effect.',actual={'state':'unchanged','ui':{},'others_unchanged':True})

    def test_four_right_then_two_completed_card_plays(self):
        f=self.fixture(); v=f.value
        strike,defend=deepcopy(v['state']['hand'])
        bash=dict(strike,id='b',name='Bash+',cost=2,upgraded=True,title_color='green')
        v['state'].update(energy=3)
        v['state']['hand']=[dict(defend,id='d0'),dict(defend,id='d1'),dict(defend,id='d2'),strike,bash]
        v['state']['enemies'][0]['hp']=30
        v['inventory']['current']['card']=['Defend']*3+['Strike','Bash+']
        v['ui'].update(hand_order=['d0','d1','d2','s','b'],focused_card_id='d0',
            navigation_mode='bounded_hand',control_profile=FOCUS_CONTROL_PROFILE)
        v['plan']={'steps':[{'kind':'card','card_id':'b','target':'enemy'}]}
        f.prepare_send(arm=True)
        self.assertEqual(['right']*4,f.fx.controller.inputs[0]['buttons'])
        def verify(**kwargs):
            return f.session.handle(f.packet(observed_result(f.session.path,note='Synthetic actual after-image reviewed.',**kwargs),result_mode=True))
        verify(unchanged=True,focus='b')
        f.prepare_send(last_snapshot(f.session.path)); verify(unchanged=True,selected='b',target='enemy')
        current=last_snapshot(f.session.path); f.prepare_send(current)
        state=deepcopy(current['state']); state.update(energy=1); state['hand']=state['hand'][:-1]
        state['enemies'][0].update(hp=20,vulnerable=3); state['piles']['discard']=['Bash+']
        ui=deepcopy(v['ui']); ui.update(hand_order=['d0','d1','d2','s'],focused_card_id='s')
        self.assertTrue(verify(actual={'state':state,'ui':ui,'others_unchanged':True})['logical_action_complete'])
        current=last_snapshot(f.session.path); self.assertNotIn('plan',current)
        current.update(plan={'steps':[{'kind':'card','card_id':'s','target':'enemy'}]},reasoning='Strike uses remaining energy.')
        f.prepare_send(current); verify(unchanged=True,selected='s',target='enemy')
        current=last_snapshot(f.session.path); f.prepare_send(current)
        state=deepcopy(current['state']); state.update(energy=0); state['hand']=state['hand'][:-1]
        state['enemies'][0]['hp']=11; state['piles']['discard'].append('Strike')
        ui=deepcopy(ui); ui.update(hand_order=['d0','d1','d2'],focused_card_id='d2')
        self.assertTrue(verify(actual={'state':state,'ui':ui,'others_unchanged':True})['logical_action_complete'])
        self.assertEqual([['right']*4]+[['cross']]*4,[c['buttons'] for c in f.fx.controller.inputs])
        self.assertIsNone(f.session.state['pending'])

    def test_uncertain_batch_remains_pending_and_cannot_replay(self):
        f=self.fixture(); v=f.value; v['ui'].update(navigation_mode='bounded_hand',control_profile=FOCUS_CONTROL_PROFILE)
        first=deepcopy(v['state']['hand'][0])
        v['state']['hand']=[dict(first,id='s'+str(i)) for i in range(5)]
        v['ui'].update(hand_order=['s'+str(i) for i in range(5)],focused_card_id='s0')
        v['plan']={'steps':[{'kind':'card','card_id':'s4','target':'enemy'}]}
        f.fx.controller.uncertain=True
        with self.assertRaisesRegex(RuntimeError,'delivery uncertain'): f.prepare_send(arm=True)
        self.assertEqual(['right']*4,f.fx.controller.inputs[0]['buttons'])
        action=f.session.state['pending']['action_id']
        with self.assertRaises(RuntimeError): f.session.handle({'operation':'send','action_id':action})
        self.assertEqual(1,len(f.fx.controller.inputs)); self.assertEqual('attempted',f.session.state['pending']['status'])

    def test_cli_reuses_exact_verified_image_and_generates_packet_paths(self):
        import json
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.veda_combat import main
        f=self.fixture(); f.prepare_send(arm=True)
        review=observed_result(f.session.path,focus='d',unchanged=True,note='Actual focus inspected.')
        packet=f.packet(review,result_mode=True); f.session.handle(packet)
        output=StringIO()
        with redirect_stdout(output):
            code=main(['--last-result','--session',str(f.session.path),
                '--capture',packet['after']['source']['path'],'--reviewer','Fixture',
                '--evidence-note','Same verified state inspected.','--reviewed','--execute'])
        self.assertEqual(0,code,output.getvalue())
        from pathlib import Path
        path=Path(json.loads(output.getvalue())['request_file'])
        self.assertEqual('combat-packets',path.parent.name)
        request=json.loads(path.read_text()); self.assertEqual('execute',request['operation'])
        self.assertEqual('d',request['reading']['ui']['focused_card_id'])
        f.session.handle(request)
        output=StringIO()
        with redirect_stdout(output):
            code=main(['--selection-result','d','--session',str(f.session.path),
                '--unchanged','--observed-result','Actual selected Defend.','--validate'])
        self.assertEqual(0,code,output.getvalue()); self.assertTrue(json.loads(output.getvalue())['result_valid'])
