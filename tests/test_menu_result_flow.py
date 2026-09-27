"""Whole Neow upgrade flow: real adapter/temporary SQLite, synthetic pixels/input."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_menu_requests import make_capture
from tests.test_reviewed_play import FakeController
from veda.execution import ARM_PHRASE
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_requests import validate_menu_draft, write_menu_request
from veda.menu_results import validate_menu_result, write_menu_result
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import ReviewedPlaySession
from veda.telemetry_database import TelemetryDatabase


class MenuResultFlowTests(unittest.TestCase):
    def test_complete_reward_to_map_uses_helpers_and_records_one_upgrade(self):
        with TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            db=TelemetryDatabase(root/'test.sqlite3')
            run=db.start_or_resume_run(ascension=2)
            floor=db.record_floor(run_id=run,act=1,floor=0,node_type='event',outcome=None)
            context={'run_id':run,'floor_id':floor,'combat_id':None,'turn_id':None}
            inventory={'current':{'card':[],'relic':['Burning Blood'],'potion':[]},
                'coverage':{'card':'unknown','relic':'complete','potion':'complete'},'properties':{}}
            db.record_inventory_baseline(run_id=run,floor_id=floor,
                items=[{'kind':'relic','item':'Burning Blood'}],coverage=inventory['coverage'],source='synthetic')
            now=datetime.now(timezone.utc)+timedelta(seconds=1)
            count=0
            controller=FakeController()
            def capture():
                nonlocal count,now
                count+=1;now+=timedelta(seconds=1)
                return make_capture(root,now-timedelta(milliseconds=200),color=(count,50,60),index=hex(count%16)[2:])
            resources={'hp':80,'max_hp':80,'gold':99,'deck_size':10}
            facts={'event_id':'neow','event_phase':'reward_options','act':1,'floor':0,
                'character':'Ironclad','ascension':2,'dialogue_text':'Choose...'}
            names=['Strike']*5+['Defend']*4+['Bash']
            ids=['strike-'+str(i+1) for i in range(5)]+['defend-'+str(i+1) for i in range(4)]+['bash-1']
            grid={'menu_family':'card_upgrade','choice_id':'upgrade','layout_id':'grid-2x5',
                'focused_id':ids[0],'options':[{'id':i,'label':n,'enabled':True,'costs':{},
                    'card':{'name':n,'upgrade_name':n+'+'}} for i,n in zip(ids,names)],
                'grid':{'complete':True,'cells':[{'id':i,'row':p//5,'column':p%5} for p,i in enumerate(ids)]}}
            def conditions(screen,phase,inv='unchanged',facts_value=None,allowed=None):
                return {'screen':screen,'phase':phase,'inventory':inv,'resources':deepcopy(resources),
                    'facts':deepcopy(facts if facts_value is None else facts_value),
                    'allow_changed_facts':allowed or []}
            draft={'schema':'veda.menu-draft.v1','context':context,'inventory':deepcopy(inventory),
                'resources':resources,'facts':facts,'ui':{'menu_family':'event_options','choice_id':'reward',
                    'focused_id':'upgrade','options':[{'id':'upgrade','label':'Upgrade a Card','enabled':True,'costs':{}}]},
                'choice':{'kind':'event','option_ids':['upgrade'],'postconditions':conditions('selection','choose')},
                'reasoning':'Synthetic complete event-flow acceptance.'}
            with ReviewedPlaySession(root/'session',run_id=run,telemetry=PlayTelemetry(db),mode='codex',
                    controller_factory=lambda:controller,clock=lambda:now) as session:
                def perform(value, result):
                    validate_menu_draft(value,CONTROL_PROFILE)
                    image=capture(); out=root/f'prepare-{count}.json'
                    write_menu_request(value,capture=image,reviewer='Fixture',evidence_note='Synthetic exact-image review.',
                        reviewed=True,control_profile=CONTROL_PROFILE,output=out,now=now)
                    request=json.loads(out.read_text())
                    if not session.armed:
                        session.handle({'operation':'arm','phrase':ARM_PHRASE,'run_id':run,'game':'Slay the Spire',
                            'screen':'event','exclusive_client_confirmed':True,'source':request['source'],
                            'review':request['review'],'frame_id':request['review']['frame_id']})
                    prepared=session.handle(request)
                    before_inputs=len(controller.inputs)
                    session.handle({'operation':'send','action_id':prepared['action_id']})
                    self.assertEqual(before_inputs+1,len(controller.inputs))
                    actual={'schema':'veda.menu-result.v1','action_id':prepared['action_id'],
                        'resources':'unchanged','facts':'unchanged','inventory':'unchanged',
                        'observed_result':'Synthetic inspected action result.',**result}
                    validate_menu_result(actual,session=session.path,action_id=prepared['action_id'],control_profile=CONTROL_PROFILE)
                    image=capture(); output=root/f'verify-{count}.json'
                    write_menu_result(actual,session=session.path,action_id=prepared['action_id'],capture=image,
                        reviewer='Fixture',evidence_note='Synthetic exact after-image review.',reviewed=True,
                        control_profile=CONTROL_PROFILE,output=output,now=now)
                    packet=json.loads(output.read_text())
                    answer=session.handle(packet)
                    self.assertEqual('verified',answer['status'])
                    self.assertIsNone(session.state['pending'])
                    return packet
                perform(draft,{'result':{'kind':'menu','ui':deepcopy(grid)}})
                # Discovery is knowledge enrichment after the opening resolves,
                # not a claim that opening the picker acquired ten cards.
                inventory['current']['card']=names[:];inventory['coverage']['card']='complete'
                baseline=db.record_inventory_baseline(run_id=run,floor_id=floor,
                    items=[{'kind':'card','item':n} for n in names]+[{'kind':'relic','item':'Burning Blood'}],
                    coverage=inventory['coverage'],source='synthetic inspected complete picker')
                with db._connection() as con:con.execute('UPDATE inventory_baselines SET observed_at=? WHERE id=?',(now.isoformat(),baseline))
                upgraded=deepcopy(inventory);upgraded['current']['card'][-1]='Bash+'
                result_facts={**facts,'event_phase':'reward_resolved','dialogue_text':'Granted...',
                    'upgraded_card_id':'bash-1','upgraded_card_name':'Bash+'}
                draft.update(inventory=inventory,ui=deepcopy(grid),choice={'kind':'selection','option_ids':['bash-1'],
                    'postconditions':conditions('event','result',upgraded,result_facts)})
                for focused in ['defend-1','defend-2','defend-3','defend-4','bash-1']:
                    perform(draft,{'result':{'kind':'focus','focused_id':focused}})
                    draft['ui']['focused_id']=focused
                preview={'kind':'upgrade_preview','selected_id':'bash-1','observed_upgrade_text':'Deal 10 damage. Apply 3 Vulnerable.',
                    'confirm_hint':{'button':'triangle','hint_text':'Confirm'}}
                perform(draft,{'result':preview})
                card=deepcopy(draft['ui']['options'][-1])
                draft['ui']={'menu_family':'card_upgrade','choice_id':'upgrade','layout_id':'grid-2x5',
                    'phase':'confirm','focused_id':'bash-1','options':[card],'selected_ids':['bash-1'],'pending_ids':['bash-1'],
                    'upgrade_preview':{'option_id':'bash-1','before_name':'Bash','after_name':'Bash+',
                        'observed_upgrade_text':preview['observed_upgrade_text']},'confirm_hint':preview['confirm_hint']}
                leave={'id':'leave','label':'[Leave]','enabled':True,'costs':{}}
                packet=perform(draft,{'inventory':'selected_upgrade_applied','facts':result_facts,
                    'observed_result':'Inspected Bash+ preview followed by confirmed Granted transition; result screen does not display card text.',
                    'result':{'kind':'menu','ui':{'screen':'event','phase':'result','choice_id':'granted',
                        'layout_id':'granted','options':[leave],'focused_id':'leave'}}})
                self.assertTrue(packet['telemetry']['inventory_events'][0]['evidence_note'])
                draft.update(inventory=upgraded,facts=result_facts,
                    ui={'menu_family':'event_leave','choice_id':'neow-leave','focused_id':'leave','options':[leave]},
                    choice={'kind':'event','option_ids':['leave'],
                        'postconditions':conditions('map','result',facts_value=result_facts)})
                perform(draft,{'result':{'kind':'menu','ui':{'screen':'map','phase':'result','choice_id':'map-arrival',
                    'layout_id':'observed-map-arrival','options':[],'focused_id':None}}})
                self.assertEqual(9,len(controller.inputs))
                self.assertEqual(9,session.state['completed'])
                self.assertFalse(session.summary()['recovery']['pending'])
                ledger=db.inventory_ledger(run_id=run,include_history=False)['current']['card']
                self.assertEqual(10,len(ledger));self.assertEqual(1,ledger.count('Bash+'));self.assertNotIn('Bash',ledger)
                with db._connection() as con:
                    self.assertEqual(1,con.execute("SELECT count(*) FROM inventory_events WHERE action='replaced'").fetchone()[0])
                    self.assertEqual(9,con.execute("SELECT count(*) FROM decisions WHERE status='resolved'").fetchone()[0])
