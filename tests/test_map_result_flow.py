"""Map inspection, choice and room entry through the real adapter; fake input only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_menu_requests import make_capture
from tests.test_reviewed_play import FakeController
from veda.execution import ARM_PHRASE
from veda.combat_input import RuntimeStop
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_requests import validate_menu_draft, write_menu_request
from veda.menu_results import validate_menu_result, write_menu_result
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import ReviewedPlaySession
from veda.telemetry_database import TelemetryDatabase


class MapResultFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = TelemetryDatabase(self.root / 'memory.sqlite3')
        self.run = self.db.start_or_resume_run(ascension=2)
        self.floor = self.db.record_floor(run_id=self.run, act=1, floor=0, node_type='event', outcome=None)
        self.context = dict(run_id=self.run, floor_id=self.floor, combat_id=None, turn_id=None)
        self.inventory = {'current': {'card': ['Strike'] * 5 + ['Defend'] * 4 + ['Bash+'],
                                     'relic': ['Burning Blood'], 'potion': []},
                          'coverage': {k: 'complete' for k in ('card', 'relic', 'potion')}, 'properties': {}}
        self.db.record_inventory_baseline(run_id=self.run, floor_id=self.floor,
            items=[{'kind': k, 'item': n} for k, names in self.inventory['current'].items() for n in names],
            coverage=self.inventory['coverage'], source='Synthetic reviewed fixture')
        self.resources = dict(hp=80, max_hp=80, gold=99, deck_size=10)
        self.facts = dict(act=1, floor=0, current_node_id='start', character='Ironclad', ascension=2)
        self.now = datetime.now(timezone.utc) + timedelta(seconds=1)
        self.serial = 0
        self.controller = FakeController()
        self.session = self.new_session()
        self.addCleanup(lambda: self.session.close())

    def new_session(self):
        return ReviewedPlaySession(self.root/'session', run_id=self.run, telemetry=PlayTelemetry(self.db),
            mode='codex', controller_factory=lambda: self.controller, clock=lambda: self.now)

    def capture(self, color=None):
        self.serial += 1; self.now += timedelta(seconds=1)
        return make_capture(self.root, self.now-timedelta(milliseconds=200),
            color=color or (self.serial, 40, 70), index=str(self.serial))

    def conditions(self, screen, facts=None):
        return {'screen': screen, 'phase': 'result', 'context': 'next_room' if screen != 'map' else self.context,
                'resources': deepcopy(self.resources), 'inventory': 'unchanged',
                'facts': deepcopy(self.facts if facts is None else facts), 'allow_changed_facts': []}

    def draft(self, *, focused='center', wanted='center', room='enemy', direction=None):
        if direction:
            identity = 'inspect-' + direction
            ui = {'menu_family': 'map_inspect', 'choice_id': 'survey', 'focused_id': identity,
                  'map_inspection': {'direction': direction, 'purpose': 'survey',
                                     'evidence_note': 'Synthetic map-only inspection.'},
                  'options': [{'id': identity, 'label': 'Inspect upper map' if direction == 'up' else 'Inspect lower map',
                               'enabled': True, 'costs': {}, 'role': 'map_inspection'}]}
            choice = {'kind': 'map', 'option_ids': [identity], 'postconditions': self.conditions('map')}
        else:
            names = ['left', 'center', 'right']
            ui = {'menu_family': 'map_nodes', 'choice_id': 'floor-one', 'focused_id': focused,
                  'map_siblings': {'complete': True, 'selectable_count': 3, 'from_node_id': 'start',
                                   'evidence_note': 'All three synthetic starting options inspected.'},
                  'options': [{'id': name, 'label': room, 'enabled': True, 'costs': {},
                      'node': {'node_id': name, 'kind': room, 'act': 1, 'floor': 1,
                               'x': x, 'y': 800, 'reachable': True,
                               'reachability_evidence': 'Synthetic starting row is available.',
                               'classification_evidence': 'Synthetic room icon.'}}
                      for name, x in zip(names, (680, 919, 1280))]}
            facts = dict(self.facts, floor=1, current_node_id=wanted, node_type=room)
            screen = {'enemy':'combat', 'rest':'rest', 'merchant':'shop', 'treasure':'treasure'}[room]
            choice = {'kind': 'map', 'option_ids': [wanted], 'postconditions': self.conditions(screen, facts)}
        return {'schema': 'veda.menu-draft.v1', 'context': self.context, 'inventory': self.inventory,
                'resources': self.resources, 'facts': self.facts, 'ui': ui, 'choice': choice,
                'reasoning': 'Synthetic map workflow acceptance, no live strategy claim.'}

    def prepare(self, draft, color=None):
        validate_menu_draft(draft, CONTROL_PROFILE)
        path = self.capture(color); output = self.root/f'prepare-{self.serial}.json'
        write_menu_request(draft, capture=path, reviewer='Fixture', evidence_note='Synthetic exact image review.',
            reviewed=True, control_profile=CONTROL_PROFILE, output=output, now=self.now)
        packet = json.loads(output.read_text())
        if not self.session.armed:
            self.session.handle({'operation': 'arm', 'phrase': ARM_PHRASE, 'run_id': self.run,
                'game': 'Slay the Spire', 'screen': 'map', 'exclusive_client_confirmed': True,
                'source': packet['source'], 'review': packet['review'], 'frame_id': packet['review']['frame_id']})
        prepared = self.session.handle(packet)
        self.session.handle({'operation': 'send', 'action_id': prepared['action_id']})
        return prepared

    def verify(self, prepared, result, *, facts='unchanged', resources='unchanged', color=None):
        draft = {'schema': 'veda.menu-result.v1', 'action_id': prepared['action_id'],
                 'inventory': 'unchanged', 'resources': resources, 'facts': facts,
                 'result': result, 'observed_result': 'Synthetic actual map action result inspected.'}
        validate_menu_result(draft, session=self.session.path, action_id=prepared['action_id'], control_profile=CONTROL_PROFILE)
        path = self.capture(color); output = self.root/f'result-{self.serial}.json'
        write_menu_result(draft, session=self.session.path, action_id=prepared['action_id'], capture=path,
            reviewer='Fixture', evidence_note='Synthetic exact result image reviewed.', reviewed=True,
            control_profile=CONTROL_PROFILE, output=output, now=self.now)
        packet = json.loads(output.read_text())
        answer = self.session.handle(packet)
        self.assertEqual('verified', answer['status']); self.assertIsNone(self.session.state['pending'])
        return answer, packet

    def view(self, effect='viewport_changed'):
        return {'kind': 'map_view', 'view': {'top_visible': False, 'bottom_visible': True,
            'focused_node_id': 'center', 'visible_node_ids': ['left', 'center', 'right'],
            'effect': effect, 'evidence_note': 'Synthetic viewport review.'}}

    def arrival(self, room='enemy'):
        if room != 'enemy':
            return {'kind':'room_entry', 'screen':{'rest':'rest','merchant':'shop','treasure':'treasure'}[room],
                    'node_id':'center'}
        return {'kind':'room_entry', 'screen':'combat', 'node_id':'center',
                'encounter': {'name':'Jaw Worm', 'type':'enemy',
                    'opening_state': {'screen':'combat', 'turn':1, **self.resources,
                                      'energy':3, 'block':0, 'hand':['Strike']*3+['Defend']*2}},
                'opening_hand':['Strike']*3+['Defend']*2}

    def test_survey_focus_select_and_enter_combat_records_canonical_context_and_zones(self):
        self.verify(self.prepare(self.draft(direction='up')), self.view())
        self.verify(self.prepare(self.draft(wanted='right')), {'kind':'focus','focused_id':'right'})
        self.verify(self.prepare(self.draft(focused='right')), {'kind':'focus','focused_id':'center'})
        prepared = self.prepare(self.draft())
        answer, packet = self.verify(prepared, self.arrival(),
            facts=dict(self.facts,floor=1,current_node_id='center',node_type='enemy'))
        self.assertEqual(['up','right','left','cross'], [c['buttons'][0] for c in self.controller.inputs])
        self.assertEqual(['advance_floor','start_combat','start_turn'],
                         [t['kind'] for t in packet['telemetry']['transitions']])
        context = answer['next_context']
        self.assertNotEqual(self.floor, context['floor_id'])
        self.assertNotEqual(packet['after']['context'], context)
        with self.db._connection() as con:
            self.assertEqual((1,'enemy'), tuple(con.execute('SELECT floor,node_type FROM floors WHERE id=?',
                                                           (context['floor_id'],)).fetchone()))
            self.assertEqual('Jaw Worm', con.execute('SELECT encounter_name FROM combats WHERE id=?',
                                                     (context['combat_id'],)).fetchone()[0])
            self.assertEqual(1, con.execute('SELECT turn_number FROM combat_turns WHERE id=?',
                                            (context['turn_id'],)).fetchone()[0])
            self.assertEqual(4, con.execute("SELECT count(*) FROM decisions WHERE status='resolved'").fetchone()[0])
        self.assertEqual(['Burning Blood'], self.db.inventory_ledger(run_id=self.run)['current']['relic'])
        zones = self.db.combat_zone_state(combat_id=context['combat_id'])
        self.assertTrue(zones['known'])

    def test_room_arrival_does_not_invent_combat_for_rest_shop_or_treasure(self):
        for room in ('rest','merchant','treasure'):
            with self.subTest(room=room):
                # Separate test case setup keeps floor identity and ledger independent.
                if room != 'rest':
                    self.session.close(); self.setUp()
                prepared = self.prepare(self.draft(room=room))
                answer, packet = self.verify(prepared,self.arrival(room),
                    facts=dict(self.facts,floor=1,current_node_id='center',node_type=room))
                self.assertIsNone(answer['next_context']['combat_id'])
                self.assertEqual(['advance_floor'], [t['kind'] for t in packet['telemetry']['transitions']])

    def test_no_progress_inspection_clears_pending_and_caps_repeated_attempts_across_restart(self):
        for _ in range(2):
            prepared = self.prepare(self.draft(direction='up'), color=(9,9,9))
            self.verify(prepared,self.view('unchanged'),color=(9,9,9))
        self.session.close(); self.controller = FakeController(); self.session = self.new_session()
        with self.assertRaisesRegex(RuntimeStop,'no viewport progress twice'):
            self.prepare(self.draft(direction='up'))
        self.assertIsNone(self.session.state['pending']); self.assertEqual([],self.controller.inputs)
        # The exhausted inspection direction does not disable a supported room choice.
        self.prepare(self.draft())
        self.assertEqual('cross',self.controller.inputs[-1]['buttons'][0])

    def test_wrong_arrival_or_changed_inspection_resources_remains_pending_without_repeat(self):
        prepared=self.prepare(self.draft(direction='up'))
        with self.assertRaises(ValueError):
            self.verify(prepared,self.view(),resources=dict(self.resources,hp=79))
        self.assertEqual('attempted',self.session.state['pending']['status'])
        self.assertEqual(1,len(self.controller.inputs))
        self.verify(prepared,self.view())
        prepared=self.prepare(self.draft())
        result=self.arrival();result['node_id']='right'
        with self.assertRaises(ValueError):
            self.verify(prepared,result,facts=dict(self.facts,floor=1,current_node_id='right',node_type='enemy'))
        self.assertEqual('attempted',self.session.state['pending']['status'])
        self.assertEqual(2,len(self.controller.inputs))
