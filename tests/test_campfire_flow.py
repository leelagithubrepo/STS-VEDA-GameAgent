"""Complete campfire visits with private SQLite, synthetic images and fake input."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from tests import test_map_travel as fixtures
from tests.test_menu_requests import make_capture
from veda.campfire import SCHEMA, plan_campfire, decision_key, observed_result, last_snapshot
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_results import write_menu_result
from veda.menu_requests import validate_menu_draft


def options():
    return {'menu_family':'campfire_options','choice_id':'campfire','focused_id':'rest',
        'options':[{'id':n,'label':n.title(),'role':n,'enabled':True,'costs':{},
                    **({'reward':{'amount':24}} if n=='rest' else {})} for n in ('rest','smith')],
        'grid':{'complete':True,'cells':[{'id':'rest','row':0,'column':0},{'id':'smith','row':0,'column':1}]}}


def exit_ui():
    return {'menu_family':'campfire_exit','choice_id':'campfire-done','focused_id':'leave',
        'options':[{'id':'leave','label':'Proceed','role':'proceed','enabled':True,'costs':{},
                    'activate_hint':{'button':'circle','hint_text':'Circle Proceed'}}]}


class CampfireFlowTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.MapTravelAdapterTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.f=fixture.flow;self.f.resources['hp']=50
        def capture(color=None):
            f=self.f;f.serial+=1;f.now+=timedelta(seconds=1)
            return make_capture(f.root,f.now-timedelta(milliseconds=200),color=color or (f.serial,50,60),index=hex(f.serial%16)[2:])
        self.f.capture=capture
        draft=self.f.draft(room='rest');draft['decision_policy']='learning'
        answer,_=self.f.verify(self.f.prepare(draft),self.f.arrival('rest'),
            facts=dict(self.f.facts,floor=1,current_node_id='center',node_type='rest'))
        self.snapshot={'schema':SCHEMA,'context':answer['next_context'],'inventory':deepcopy(self.f.inventory),
            'resources':deepcopy(self.f.resources),'facts':dict(self.f.facts,floor=1,current_node_id='center',node_type='rest'),
            'ui':options()}

    def choose(self,value,identity):
        return plan_campfire(value,{'option_id':identity,'reason':'Synthetic strategic decision.',
                                   'decision_key':decision_key(value)})

    def verify(self,kind,**kwargs):
        f=self.f
        draft=observed_result(f.session.path,kind,note='Synthetic actual campfire result inspected.',**kwargs)
        image=f.capture();output=f.root/f'campfire-result-{f.serial}.json'
        write_menu_result(draft,session=f.session.path,action_id=draft['action_id'],capture=image,
            reviewer='Fixture',evidence_note='Synthetic after-image.',reviewed=True,control_profile=CONTROL_PROFILE,
            output=output,now=f.now)
        packet=json.loads(output.read_text());response=f.session.handle(packet)
        self.assertEqual('verified',response['status']);self.assertIsNone(f.session.state['pending'])
        return response,packet

    def test_map_rest_heal_proceed_map(self):
        planned=self.choose(self.snapshot,'rest');self.f.prepare(planned['draft'])
        self.verify('healed',ui=exit_ui(),actual={'hp':74,'others_unchanged':True})
        actual=last_snapshot(self.f.session.path);self.assertEqual(74,actual['resources']['hp'])
        self.f.prepare(plan_campfire(actual)['draft'])
        self.verify('map',unchanged=True)
        self.assertEqual([['cross'],['cross'],['circle']],[x['buttons'] for x in self.f.controller.inputs])
        with self.f.db._connection() as con:
            self.assertEqual(0,con.execute('SELECT count(*) FROM inventory_events').fetchone()[0])
            self.assertEqual(3,con.execute("SELECT count(*) FROM decisions WHERE status='resolved'").fetchone()[0])

    def test_map_smith_focus_picker_preview_upgrade_proceed_map(self):
        selected=self.choose(self.snapshot,'smith');self.f.prepare(selected['draft'])
        self.verify('focus',focused_id='smith',unchanged=True)
        actual=last_snapshot(self.f.session.path)
        self.f.prepare(plan_campfire(actual,selected['decision'])['draft'])
        grid={'menu_family':'card_upgrade','choice_id':'smith-grid','focused_id':'bash','options':[
            {'id':'bash','label':'Strike','enabled':True,'costs':{},'card':{'name':'Strike','upgrade_name':'Strike+'}}],
            'grid':{'complete':True,'cells':[{'id':'bash','row':0,'column':0}]}}
        self.verify('menu',ui=grid,unchanged=True)
        actual=last_snapshot(self.f.session.path);self.f.prepare(self.choose(actual,'bash')['draft'])
        self.verify('upgrade_preview',unchanged=True,preview={'selected_id':'bash',
            'observed_upgrade_text':'Deal 9 damage.',
            'confirm_hint':{'button':'triangle','hint_text':'Triangle Confirm'}})
        actual=last_snapshot(self.f.session.path);self.f.prepare(plan_campfire(actual)['draft'])
        self.verify('upgraded',ui=exit_ui(),actual={'upgraded_card_id':'bash','upgraded_card_name':'Strike+','others_unchanged':True})
        actual=last_snapshot(self.f.session.path);self.f.prepare(plan_campfire(actual)['draft'])
        self.verify('map',unchanged=True)
        self.assertEqual([['cross'],['right'],['cross'],['cross'],['triangle'],['circle']],
                         [x['buttons'] for x in self.f.controller.inputs])
        with self.f.db._connection() as con:
            self.assertEqual(1,con.execute("SELECT count(*) FROM inventory_events WHERE action='replaced'").fetchone()[0])
        self.assertEqual(1,self.f.db.inventory_ledger(run_id=self.f.run,include_history=False)['current']['card'].count('Strike+'))

    def test_disabled_choices_and_changed_state_invalidate_decision(self):
        selected=self.choose(self.snapshot,'rest')
        self.snapshot['ui']['options'][0]['enabled']=False
        with self.assertRaises(ValueError): self.choose(self.snapshot,'rest')
        with self.assertRaises(ValueError): plan_campfire(self.snapshot,selected['decision'])
        self.snapshot['ui']['options'][0]['enabled']=True;self.snapshot['resources']['hp']=30
        with self.assertRaises(ValueError): plan_campfire(self.snapshot,selected['decision'])

    def test_healing_needs_actual_hp_and_cannot_repeat_pending_input(self):
        self.f.prepare(self.choose(self.snapshot,'rest')['draft'])
        count=len(self.f.controller.inputs)
        with self.assertRaises(ValueError): observed_result(self.f.session.path,'healed',note='Not inspected.',unchanged=True,ui=exit_ui())
        answer=self.f.session.handle({'operation':'send','action_id':self.f.session.state['pending']['action_id']})
        self.assertEqual('recoverable_review',answer['status'])
        self.assertEqual(count,len(self.f.controller.inputs));self.assertEqual('attempted',self.f.session.state['pending']['status'])

    def test_cli_sealed_reuse_and_environment_session(self):
        from scripts.veda_campfire import main
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        path=self.f.root/'snapshot.json'; value=deepcopy(self.snapshot);value['context']='session'
        path.write_text(json.dumps(value))
        image=self.f.capture();out=StringIO()
        with patch.dict('os.environ',{'VEDA_PLAY_SESSION':str(self.f.session.path)}), redirect_stdout(out):
            code=main(['--snapshot',str(path),'--choose','rest','--reason','Synthetic health tradeoff.'])
        self.assertEqual(0,code,out.getvalue());self.assertEqual('planned',json.loads(out.getvalue())['status'])
        self.assertEqual(1,len(self.f.controller.inputs))  # Only prior fake map entry.

    def test_max_hp_caps_forecast_and_actual_different_healing_is_logged(self):
        self.snapshot['resources']['hp']=70
        planned=self.choose(self.snapshot,'rest')
        self.assertEqual(80,planned['draft']['choice']['postconditions']['resources']['hp'])
        self.f.prepare(planned['draft'])
        self.verify('healed',ui=exit_ui(),actual={'hp':75,'others_unchanged':True})
        self.assertEqual(75,last_snapshot(self.f.session.path)['resources']['hp'])
        with self.f.db._connection() as con:
            row=con.execute("SELECT actual_outcome_json FROM decisions ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertTrue(json.loads(row[0])['learning']['observed_mismatches'])

    def test_exit_needs_actual_hint_and_cannot_claim_resolution_from_focus(self):
        invalid=deepcopy(self.snapshot);invalid['ui']=exit_ui();invalid['facts']['campfire_phase']='resolved'
        invalid['ui']['options'][0].pop('activate_hint')
        with self.assertRaisesRegex(ValueError,'hint'): plan_campfire(invalid)
        self.f.prepare(self.choose(self.snapshot,'smith')['draft'])  # Right focus only.
        with self.assertRaises(ValueError): observed_result(self.f.session.path,'healed',note='Wrong stage.',
            ui=exit_ui(),actual={'hp':74,'others_unchanged':True})
        self.assertEqual('attempted',self.f.session.state['pending']['status'])

    def test_existing_event_screen_cannot_use_campfire_profile(self):
        value=deepcopy(self.snapshot);value['facts']['node_type']='event'
        with self.assertRaisesRegex(ValueError,'rest site'): self.choose(value,'rest')
