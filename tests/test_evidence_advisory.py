"""Synthetic contracts for joining observations to tactics; not vision accuracy."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from veda.evidence_advisory import advisory_context, check_evidence, partial_observation
from veda.evidence_ledger import EvidenceLedger
from tests.test_evidence_ledger import CONTEXT, TIME, card, enemies, packet, player, receipt, section, statuses, ui


class EvidenceAdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name)/'synthetic.png'
        Image.new('RGB',(20,20),'black').save(self.path)
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.sources = {self.digest:str(self.path)}
        self.ledger = EvidenceLedger(**CONTEXT)

    def tearDown(self):
        self.tmp.cleanup()

    def observe(self, values, **kwargs):
        return self.ledger.observe(packet(values,digest=self.digest,**kwargs))

    def seed(self, *, cards=None, potions=None, relics=None, values=None):
        rows = [] if cards is None else cards
        enemy = enemies();enemy.update(encounter_type='enemy',encounter_name='Cultist')
        data = {'player':section({**player(),'energy':3}),
                'hand':section({'cards':rows,'order':[c['id'] for c in rows]}),
                'enemies':section(enemy),'statuses':section(statuses()),'ui':section(ui())}
        data.update(values or {})
        self.observe(data)
        inventory = dict(current=dict(card=[],relic=relics or [],potion=potions or []),
                         coverage=dict(card='unknown',relic='complete',potion='complete'))
        self.ledger.record_inventory_event(receipt(self.ledger,data=inventory))

    def check(self, plan=None, **kwargs):
        return check_evidence(self.ledger.dump(),source_files=self.sources,plan=plan,
                              mode=kwargs.pop('mode','replay'),as_of=kwargs.pop('as_of',TIME),**kwargs)

    def test_complete_reviewed_state_uses_shared_potion_check_and_sources(self):
        self.seed(potions=['Energy Potion','Power Potion','Fairy in a Bottle'])
        p={'steps':[{'kind':'end_turn'}]}
        result=self.check(p)
        self.assertFalse(result['checked_plan']['allowed'])
        self.assertEqual(6,result['context']['incoming_displayed'])
        self.assertEqual(['reviewer'],result['provenance_kinds'])
        self.assertFalse(result['automatic_recognition_complete'])
        self.assertFalse(result['controller_authorized'])
        p['potion_review']={'Energy Potion':'No cards remain.', 'Power Potion':'Retain for later; this hit is survivable.'}
        self.assertTrue(self.check(p)['checked_plan']['allowed'])
        self.assertFalse(self.check(p)['inspections']['decision_blocked'])

    def test_partial_reader_candidates_never_become_complete_hand_or_incoming(self):
        reading={'schema':'veda.partial-saved-frame.v1','ok':True,'partial':True,
                 'runtime_authorized':False,'controller_authorized':False,'runtime_authorization_eligible':False,
                 'image_path':str(self.path),'image_sha256':self.digest,'source_dimensions':[20,20],
                 'frame_id':'f1','hud':{'hp':50,'max_hp':80,'energy':0},
                 'combat_evidence':{'player_block':0},
                 'cards':{'card_candidates':[{'name':'Strike'}],'hand_complete':None}}
        offered=partial_observation(reading,context=CONTEXT,epoch=0,observed_at=TIME)
        self.ledger.observe(offered)
        result=self.check({'steps':[{'kind':'end_turn'}]})
        self.assertIsNone(result['context']['state'])
        self.assertIsNone(result['snapshot']['sections']['hand']['data'])
        self.assertFalse(result['checked_plan']['allowed'])
        self.assertEqual('current_screen_and_focus',result['inspections']['requests'][0]['view'])
        reading['cards']['hand_complete']=True
        with self.assertRaises(ValueError):
            partial_observation(reading,context=CONTEXT,epoch=0,observed_at=TIME)

    def test_known_incoming_uses_every_confirmed_living_enemy(self):
        e=enemies();e['enemies'] += [dict(e['enemies'][0],id='e2',name='Jaw Worm',intent_hits=[25])]
        e.update(target_order=['e1','e2'],encounter_type='enemy')
        s=statuses();s['enemies']['e2']=deepcopy(s['enemies']['e1'])
        self.seed(values={'enemies':section(e),'statuses':section(s),'player':section({**player(),'block':13})})
        result=self.check({'steps':[{'kind':'end_turn'}]})
        self.assertEqual(31,result['context']['incoming_displayed'])
        self.assertEqual(32,result['checked_plan']['forecast']['player_hp'])

    def test_missing_multiplier_or_roster_cannot_publish_total(self):
        e=enemies();e['enemies'][0]['intent_hits']=None
        self.seed(values={'enemies':section(e,False)})
        result=self.check({'steps':[{'kind':'end_turn'}]})
        self.assertNotIn('incoming_displayed',result['context'])
        self.assertFalse(result['checked_plan']['allowed'])

    def test_corruption_pyramid_needs_complete_draw_and_setup_reason(self):
        c=dict(card(),name='Corruption+',type='Power',cost=2,upgraded=True,title_color='green')
        self.seed(cards=[c],relics=['Runic Pyramid'])
        p={'steps':[{'kind':'card','card_id':'c1'}]}
        self.assertFalse(self.check(p)['checked_plan']['allowed'])
        self.observe({'piles':section({'draw':['Barricade+']},False)})
        self.assertIsNone(self.check(p)['context']['state']['piles']['draw'])
        self.observe({'piles':section({'draw':['Barricade+'],'discard':[],'exhaust':[]})})
        self.assertFalse(self.check(p)['checked_plan']['allowed'])
        p['setup_reason']='Immediate defense is necessary; Barricade remains for the next safe turn.'
        self.assertTrue(self.check(p)['checked_plan']['allowed'])

    def test_true_grit_shame_and_current_energy_pass_through(self):
        grit=dict(card(),name='True Grit+',type='Skill',upgraded=True,title_color='green')
        shame=dict(card('shame'),name='Shame',type='Curse',cost=None,playable=False,unplayable=True,cost_is_absent=True)
        self.seed(cards=[grit,shame])
        p={'steps':[{'kind':'card','card_id':'c1','exhaust_card_id':'shame'}]}
        self.assertTrue(self.check(p)['checked_plan']['allowed'])
        p['steps'][0]['exhaust_card_id']='not-in-hand'
        self.assertFalse(self.check(p)['checked_plan']['allowed'])

    def test_unknown_input_invalidates_inventory_and_piles_too(self):
        self.seed()
        self.ledger.invalidate(reason='unlogged physical controller action',kind='unknown_input')
        result=self.check({'steps':[{'kind':'end_turn'}]})
        self.assertFalse(result['checked_plan']['allowed'])
        self.assertIsNone(result['context']['state'])
        self.assertEqual('stale',result['snapshot']['sections']['inventory']['status'])

    def test_conflicting_same_frame_status_is_withheld(self):
        self.seed()
        changed=statuses();changed['player']['weak']=1
        self.observe({'statuses':section(changed)})
        self.assertIsNone(self.check()['context']['state'])

    def test_duplicate_enemy_status_cannot_override_known_fact(self):
        e=enemies();e.update(encounter_type='enemy');e['enemies'][0]['vulnerable']=2
        self.seed(values={'enemies':section(e)})
        result=self.check({'steps':[{'kind':'end_turn'}]})
        self.assertFalse(result['checked_plan']['allowed'])
        self.assertTrue(any('conflict' in reason or 'contradictory' in reason for reason in result['context']['unknowns']))

    def test_overlay_cannot_be_used_as_playable_combat_screen(self):
        self.seed(values={'ui':section({**ui(),'phase':'draw_pile'})})
        self.assertFalse(self.check({'steps':[{'kind':'end_turn'}]})['checked_plan']['allowed'])

    def test_status_extra_fields_cannot_override_player_resources(self):
        s=statuses();s['player'].update(hp=1,energy=99,ascension=20)
        with self.assertRaises(ValueError): self.seed(values={'statuses':section(s)})
        self.seed()
        forged=self.ledger.snapshot();forged['sections']['statuses']['data']['player']=s['player']
        result=advisory_context(forged,mode='replay',as_of=TIME)
        self.assertTrue(result['unknowns'])
        self.assertEqual(50,result['state']['hp'])
        self.assertEqual(3,result['state']['energy'])

    def test_required_setup_inspection_blocks_overall_advice_even_if_play_legal(self):
        self.seed()
        result=self.check({'steps':[{'kind':'end_turn'}],'long_fight':True})
        self.assertTrue(result['checked_plan']['allowed'])
        self.assertTrue(result['inspections']['decision_blocked'])
        self.assertFalse(result['advisory_ready'])

    def test_replay_clock_cannot_make_archived_evidence_live(self):
        self.seed()
        self.assertTrue(self.check()['context']['fresh'])
        self.assertFalse(self.check(mode='review',as_of=None)['context']['fresh'])
        with self.assertRaises(ValueError):
            self.check(mode='review')
        with self.assertRaises(ValueError):
            self.check(as_of=None)
        with self.assertRaises(ValueError):
            advisory_context(self.ledger.snapshot(),mode='replay',as_of='not-a-date')
        self.assertFalse(self.check(as_of='2025-12-31T23:59:59+00:00')['context']['fresh'])

    def test_missing_changed_and_mutated_during_check_sources_refuse_publication(self):
        self.seed()
        with self.assertRaises(ValueError):
            check_evidence(self.ledger.dump(),source_files={})
        old=self.path.read_bytes();self.path.write_bytes(old+b'changed')
        with self.assertRaises(ValueError): self.check()
        self.path.write_bytes(old)
        from veda.advisory import check_plan
        def mutate(*args):
            result=check_plan(*args);self.path.write_bytes(old+b'changed');return result
        with patch('veda.evidence_advisory.check_plan',side_effect=mutate):
            with self.assertRaises(ValueError): self.check({'steps':[{'kind':'end_turn'}]})

    def test_cli_malformed_bundle_is_explained_and_partial_bundle_exits_two(self):
        from scripts.check_combat_evidence import main
        file=Path(self.tmp.name)/'bundle.json'
        for data in ([], {'journal':{},'source_files':[]}, {'journal':{},'source_files':{'x':None}},
                     {'journal':{'events':[3]},'source_files':{}}):
            file.write_text(json.dumps(data))
            with patch('sys.stderr',new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit) as failure: main([str(file)])
                self.assertEqual(2,failure.exception.code)
                self.assertNotIn('Traceback',err.getvalue())
        self.observe({'player':section({'hp':50},False)})
        file.write_text(json.dumps({'journal':self.ledger.dump(),'source_files':{self.digest:'synthetic.png'}}))
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(2,main([str(file),'--replay-as-of',TIME]))
            self.assertFalse(json.loads(out.getvalue())['advisory_ready'])


class EvidenceMemoryTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        from veda.telemetry_database import TelemetryDatabase
        self.env=EvidenceAdvisoryTests();self.env.setUp()
        self.db=TelemetryDatabase(Path(self.env.tmp.name)/'memory.sqlite3')
        run=self.db.start_new_run(ascension=1)
        floor=self.db.record_floor(run_id=run,act=1,floor=1,node_type='enemy',outcome=None,starting_state={})
        combat=self.db.start_combat(run_id=run,floor_id=floor,opening_state={},encounter_name='Cultist',encounter_type='enemy')
        turn=self.db.start_combat_turn(combat_id=combat,turn_number=1,phase='combat',opening_state={})
        self.binding=dict(run_id=run,floor_id=floor,combat_id=combat,turn_id=turn)
        self.db.record_inventory_baseline(run_id=run,items=[],coverage=dict(card='unknown',relic='complete',potion='complete'),source='synthetic fixture')
        self.env.seed()
        self.journal=self.env.ledger.dump()
        self.journal['initial_context']=deepcopy(self.binding)
        observed=datetime.now(timezone.utc).isoformat()
        for event in self.journal['events']:
            event['payload']['context']=deepcopy(self.binding)
            event['payload']['observed_at']=observed

    def tearDown(self): self.env.tearDown()

    def store(self):
        return self.db.record_evidence_snapshot(journal=self.journal,source_files=self.env.sources,source='synthetic reviewed fixture')

    def test_source_journal_reaches_existing_sqlite_advice_and_inventory_invalidation(self):
        result=self.store()
        context=self.db.advisory_context(combat_id=self.binding['combat_id'])
        self.assertTrue(context['fresh']);self.assertEqual(result['snapshot_id'],context['snapshot_id'])
        checked=self.db.record_checked_advice(combat_id=self.binding['combat_id'],snapshot_id=result['snapshot_id'],
            plan={'steps':[{'kind':'end_turn'}]},reasoning='synthetic integration contract')
        self.assertTrue(checked['check']['allowed'])
        with self.db._connection() as db:
            payload=json.loads(db.execute('SELECT payload_json FROM evidence_events WHERE id=?',(result['snapshot_id'],)).fetchone()[0])
        self.assertEqual(self.journal,payload['evidence_journal'])
        self.assertEqual(['reviewer'],payload['provenance_kinds'])
        self.db.record_inventory_event(run_id=self.binding['run_id'],item_kind='potion',action='acquired',item_name='Energy Potion',source='synthetic change')
        self.assertFalse(self.db.advisory_context(combat_id=self.binding['combat_id'])['fresh'])

    def test_import_rejects_inventory_disagreement_without_writing(self):
        self.journal['events'][-1]['payload']['data']['current']['potion']=['Energy Potion']
        before=self.db.status()['events']
        with self.assertRaisesRegex(ValueError,'inventory disagrees'): self.store()
        self.assertEqual(before,self.db.status()['events'])

    def test_import_rejects_encounter_disagreement_without_writing(self):
        self.journal['events'][0]['payload']['sections']['enemies']['data']['encounter_type']='elite'
        before=self.db.status()['events']
        with self.assertRaisesRegex(ValueError,'encounter or Ascension'): self.store()
        self.assertEqual(before,self.db.status()['events'])

    def test_inventory_comparison_preserves_multiplicity_without_requiring_order(self):
        from datetime import datetime, timezone
        self.db.record_inventory_baseline(run_id=self.binding['run_id'],
            items=[{'kind':'potion','item':name} for name in ['Energy Potion','Power Potion','Energy Potion']],
            coverage=dict(card='unknown',relic='complete',potion='complete'),source='synthetic fixture')
        observed=datetime.now(timezone.utc).isoformat()
        for event in self.journal['events']: event['payload']['observed_at']=observed
        inv=self.journal['events'][-1]['payload']['data']['current']
        inv['potion']=['Power Potion','Energy Potion']
        with self.assertRaisesRegex(ValueError,'inventory disagrees'): self.store()
        inv['potion'].append('Energy Potion')
        self.assertTrue(self.store()['snapshot_id'])

    def test_old_replay_cannot_be_imported_as_current(self):
        for event in self.journal['events']: event['payload']['observed_at']=TIME
        before=self.db.status()['events']
        with self.assertRaisesRegex(ValueError,'current inspected'): self.store()
        self.assertEqual(before,self.db.status()['events'])


if __name__ == '__main__':
    unittest.main()
