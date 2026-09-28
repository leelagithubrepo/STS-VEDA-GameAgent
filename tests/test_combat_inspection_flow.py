"""Tooltip inspection through real reviewed adapter and temporary SQLite."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_combat_inspection import draft
from tests.test_menu_requests import make_capture
from tests.test_reviewed_play import FakeController
from veda.combat_input import RuntimeStop
from veda.combat_inspection import (CONTROL_PROFILE, RESULT_SCHEMA, write_inspection_request,
                                    write_inspection_result)
from veda.execution import ARM_PHRASE
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import ReviewedPlaySession
from veda.telemetry_database import TelemetryDatabase


class CombatInspectionFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = TelemetryDatabase(self.root/'memory.sqlite3')
        self.run = self.db.start_or_resume_run(ascension=2)
        floor = self.db.record_floor(run_id=self.run, act=1, floor=1, node_type='enemy', outcome=None)
        combat = self.db.start_combat(run_id=self.run, floor_id=floor, encounter_name='Two enemies',
            encounter_type='enemy', opening_state={})
        turn = self.db.start_combat_turn(combat_id=combat, turn_number=1, phase='combat', opening_state={})
        self.context = dict(run_id=self.run, floor_id=floor, combat_id=combat, turn_id=turn)
        self.value = draft(); self.value['context'] = self.context
        inventory = self.value['inventory']
        self.db.record_inventory_baseline(run_id=self.run, floor_id=floor,
            items=[{'kind':kind,'item':name} for kind,names in inventory['current'].items() for name in names],
            coverage=inventory['coverage'], source='Synthetic inspected inventory')
        self.db.start_combat_zones(combat_id=combat, deck=inventory['current']['card'],
                                  hand=inventory['current']['card'], source='Synthetic inspected zones')
        self.now = datetime.now(timezone.utc)+timedelta(seconds=1)
        self.serial = 0
        self.controller = FakeController()
        self.session = self.new_session()
        self.addCleanup(lambda:self.session.close())

    def new_session(self):
        return ReviewedPlaySession(self.root/'session',run_id=self.run,telemetry=PlayTelemetry(self.db),
            mode='codex',controller_factory=lambda:self.controller,clock=lambda:self.now)

    def capture(self):
        self.serial += 1; self.now += timedelta(seconds=1)
        return make_capture(self.root,self.now-timedelta(milliseconds=200),
                            color=(self.serial,30,60),index=str(self.serial))

    def packet(self, execute=True):
        source=self.capture(); output=self.root/f'prepare-{self.serial}.json'
        write_inspection_request(self.value,capture=source,reviewer='Fixture',evidence_note='Synthetic tooltip reviewed.',
            reviewed=True,output=output,execute=execute,now=self.now)
        return json.loads(output.read_text())

    def arm(self, packet):
        self.session.handle({'operation':'arm','phrase':ARM_PHRASE,'run_id':self.run,'game':'Slay the Spire',
            'screen':'combat','exclusive_client_confirmed':True,'source':packet['source'],
            'review':packet['review'],'frame_id':packet['review']['frame_id']})

    def send(self):
        packet=self.packet();self.arm(packet)
        return self.session.handle(packet)

    def result(self, action_id, unchanged=False):
        ui=deepcopy(self.value['ui'])
        if not unchanged:
            ui.update(phase='hand',tooltip_visible=False,tooltip_subject_id=None,focused_card_id='c1',
                      focus_domain='hand',tooltip_kind='none',focused_subject_id=None,
                      focus_evidence_note='First Defend visibly raised; hand focus confirmed.')
        result={'schema':RESULT_SCHEMA,'action_id':action_id,'ui':ui,
                'observed_result':'Actual focus inspected; known game state unchanged.'}
        source=self.capture();output=self.root/f'result-{self.serial}.json'
        write_inspection_result(result,session=self.session.path,action_id=action_id,capture=source,
            reviewer='Fixture',evidence_note='Synthetic actual hand UI reviewed.',reviewed=True,output=output,now=self.now)
        return json.loads(output.read_text())

    def test_execute_one_down_then_verify_retains_unknowns_inventory_and_known_zones(self):
        inventory=self.db.inventory_ledger(run_id=self.run)
        zones=self.db.combat_zone_state(combat_id=self.context['combat_id'])
        sent=self.send()
        self.assertEqual('awaiting_fresh_review',sent['status'])
        self.assertEqual([['down']],[r['buttons'] for r in self.controller.inputs])
        action=sent['action_id'];packet=self.result(action)
        verified=self.session.handle(packet)
        self.assertEqual('verified',verified['status']);self.assertFalse(verified['logical_action_complete'])
        self.assertIsNone(self.session.state['pending'])
        self.assertEqual(inventory,self.db.inventory_ledger(run_id=self.run))
        self.assertEqual(zones,self.db.combat_zone_state(combat_id=self.context['combat_id']))
        with self.db._connection() as con:
            observed=json.loads(con.execute("SELECT state_json FROM evidence_events WHERE kind='play_outcome'").fetchone()[0])
        self.assertEqual(self.value['facts'],observed['facts'])

    def test_attempt_budget_survives_restart_and_fresh_source_without_sending_again(self):
        sent=self.send()
        self.session.handle(self.result(sent['action_id'], unchanged=True))
        self.session.close();self.controller=FakeController();self.session=self.new_session()
        replacement=self.packet();self.arm(replacement)
        sent=self.session.handle(replacement)
        self.assertEqual([['circle']],[r['buttons'] for r in self.controller.inputs])
        self.session.handle(self.result(sent['action_id'], unchanged=True))
        replacement=self.packet()
        with self.assertRaisesRegex(ValueError,'already attempted'):
            self.session.handle(replacement)
        self.assertEqual([['circle']],[r['buttons'] for r in self.controller.inputs])
        self.assertIsNone(self.session.state['pending'])

    def test_attempted_pending_blocks_rearm_replay_and_survives_reopen(self):
        sent=self.send();self.session.close();self.controller=FakeController();self.session=self.new_session()
        self.assertEqual('attempted',self.session.state['pending']['status'])
        with self.assertRaises(RuntimeStop):
            self.session.handle(self.packet())
        self.assertEqual([],self.controller.inputs)
        self.assertEqual(sent['action_id'],self.session.state['pending']['action_id'])
        self.assertEqual('verified',self.session.handle(self.result(sent['action_id']))['status'])

    def test_inspection_cannot_log_inventory_or_turn_effects(self):
        sent=self.send();result=self.result(sent['action_id'])
        result['telemetry']={'inventory_events':[{'kind':'card','action':'removed','item':'Defend',
                                                'evidence_note':'Fabricated inspection effect'}]}
        with self.assertRaises(RuntimeStop):self.session.handle(result)
        self.assertEqual('attempted',self.session.state['pending']['status'])
        self.assertEqual(1,len(self.controller.inputs))

    def test_wrong_result_kind_cannot_become_a_combat_action(self):
        sent=self.send();result=self.result(sent['action_id']);result['after']['kind']='combat'
        with self.assertRaises((ValueError,RuntimeStop,KeyError)):self.session.handle(result)
        self.assertEqual('attempted',self.session.state['pending']['status'])
        self.assertEqual(1,len(self.controller.inputs))

    def test_prepare_without_arm_has_no_game_input(self):
        packet=self.packet(execute=False)
        result=self.session.handle(packet)
        self.assertEqual('prepared',result['status'])
        self.assertEqual([],self.controller.inputs)
        with self.assertRaises(RuntimeStop):self.session.handle({'operation':'send','action_id':result['action_id']})
        self.assertEqual([],self.controller.inputs)

    def test_visible_floor_must_match_canonical_floor_before_dispatch(self):
        # Reproduce the attached failure's combat incorrectly attached to Neow.
        with self.db._connection() as con:
            con.execute('UPDATE floors SET floor=0,node_type=? WHERE id=?',('event',self.context['floor_id']))
        with self.assertRaises((ValueError,RuntimeStop)):
            self.send()
        self.assertEqual([],self.controller.inputs)


if __name__=='__main__':unittest.main()
