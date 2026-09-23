import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from veda.advisory import check_plan, campfire_comparison, boss_manifest, validate_snapshot
from veda.combat import CardEffect, CombatEnemy, CombatSnapshot, validate_and_predict
from veda.combat_state import verify_combat_state
from veda.preflight import preflight_combat
from veda.telemetry_database import TelemetryDatabase
from veda.vision import StructuredGameState, VisibleEnemy
from veda.knowledge import KnowledgeBase
from veda.research_catalog import load_catalog


def card(name, kind='Attack', cost=1, ident='c1'):
    return dict(id=ident, name=name, type=kind, cost=cost, upgraded=name.endswith('+'),
                title_color='green' if name.endswith('+') else 'white', playable=True)


def state(hand=None):
    return dict(schema='spire.advisory.v1', observed_at=datetime.now(timezone.utc).isoformat(),
        hp=50, max_hp=80, energy=3, block=10, strength=0, dexterity=0, weak=0, vulnerable=0,
        frail=0, no_block=0, powers={}, powers_complete=True, hand_complete=True,
        hand=hand if hand is not None else [card('Strike')],
        piles=dict(draw=None, discard=None, exhaust=None, draw_order=None), counters={},
        enemies=[dict(name='Cultist', hp=40, block=0, vulnerable=0, strength=0, weak=0,
                      intent='attack 6', intent_hits=[6])], end_turn_damage=0, unmodeled_effects=[])


def context(hand=None):
    return dict(fresh=True, unknowns=[], state=state(hand), encounter_type='enemy',
                inventory={'coverage': {'relic':'complete','potion':'complete'},
                           'current': {'relic': [], 'potion': []}})


def play(ident='c1', target='Cultist', **kwargs):
    return dict(kind='card', card_id=ident, target=target, **kwargs)


class AdvisoryTests(unittest.TestCase):
    def test_corruption_skill_cost_does_not_make_barricade_free(self):
        c=context([card('Defend+', 'Skill', 1), card('Barricade+', 'Power', 2,'bar')])
        c['state']['powers']['Corruption']=True;c['state']['energy']=1
        ok=check_plan(c, {'steps':[play()]})
        self.assertTrue(ok['allowed']); self.assertEqual(ok['steps'][0]['energy_after'],1)
        self.assertFalse(check_plan(c, {'steps':[play('bar')]})['allowed'])
        c['state']['hand'][1]['type']='Skill'
        self.assertFalse(check_plan(c, {'steps':[play('bar')]})['allowed'])
        self.assertFalse(check_plan(c, {'steps':[play(),play('bar')]})['allowed'])

    def test_headbutt_needs_current_discard_and_reobservation(self):
        c=context([card('Headbutt')]);p={'steps':[play(return_card='Carnage')]}
        self.assertFalse(check_plan(c,p)['allowed'])
        c['state']['piles']['discard']=['Defend'];self.assertFalse(check_plan(c,p)['allowed'])
        c['state']['piles']['discard']=['Carnage'];r=check_plan(c,p)
        self.assertTrue(r['allowed']);self.assertTrue(r['steps'][0]['observe_after'])
        p['steps'].append({'kind':'end_turn'});self.assertFalse(check_plan(c,p)['allowed'])

    def test_true_grit_upgrade_and_shame_target(self):
        c=context([card('True Grit','Skill'),card('Shame','Curse',0,'shame')])
        self.assertFalse(check_plan(c,{'steps':[play(exhaust_card_id='shame')]})['allowed'])
        c['state']['hand'][0]=card('True Grit+','Skill')
        self.assertTrue(check_plan(c,{'steps':[play(exhaust_card_id='shame')]})['allowed'])
        c['state']['hand'][0]['title_color']=None
        self.assertFalse(check_plan(c,{'steps':[play(exhaust_card_id='shame')]})['allowed'])

    def test_pyramid_requires_draw_and_conditional_setup_review(self):
        c=context([card('Corruption+','Power',2)]); c['inventory']['current']['relic']=['Runic Pyramid']
        p={'steps':[play()]};self.assertFalse(check_plan(c,p)['allowed'])
        c['state']['piles']['draw']=['Barricade+'];self.assertFalse(check_plan(c,p)['allowed'])
        p['setup_reason']='Incoming threat requires free defense now; retain Barricade for the following safe turn.'
        self.assertTrue(check_plan(c,p)['allowed'])

    def test_zero_cost_body_slam_and_empty_hand_end_turn(self):
        c=context([card('Body Slam+', cost=0)])
        self.assertFalse(check_plan(c,{'steps':[{'kind':'end_turn'}]})['allowed'])
        r=check_plan(c,{'steps':[play()], 'claims_lethal':False})
        self.assertTrue(r['allowed']);self.assertEqual(r['forecast']['enemies'][0]['hp'],30)
        self.assertTrue(check_plan(context([]),{'steps':[{'kind':'end_turn'}]})['allowed'])
        c['state']['end_turn_damage']=None
        self.assertFalse(check_plan(c,{'steps':[{'kind':'end_turn'}], 'zero_cost_review':{'c1':'Counter constraint'}})['allowed'])

    def test_time_warp_twelfth_resolves_then_forces_observation_and_strength(self):
        c=context([card('Strike')]);c['state']['enemies']=[dict(name='Time Eater',hp=200,block=0,
            vulnerable=0,strength=0,weak=0,intent='7x3',intent_hits=[7,7,7],move='Reverberate')]
        c['state']['counters']['time_warp']=11;c['encounter_type']='boss'
        c['boss_manifest']=boss_manifest('Time Eater',1)
        r=check_plan(c,{'steps':[play(target='Time Eater')]})
        self.assertTrue(r['allowed']);self.assertEqual(r['forecast']['incoming_displayed'],27)
        self.assertTrue(r['steps'][0]['observe_after'])
        self.assertFalse(check_plan(c,{'steps':[play(target='Time Eater'),{'kind':'end_turn'}]})['allowed'])
        c['state']['hp']=10
        self.assertFalse(check_plan(c,{'steps':[play(target='Time Eater')]})['allowed'])

    def test_dual_wield_capacity_potion_boundary_and_hp_loss(self):
        c=context([card('Dual Wield+','Skill',1)] + [card('Strike',ident=f's{i}') for i in range(9)])
        self.assertFalse(check_plan(c,{'steps':[play(copy_card_id='s0')]})['allowed'])
        c['state']['hand'].pop();self.assertTrue(check_plan(c,{'steps':[play(copy_card_id='s0')]})['allowed'])
        c['inventory']['current']['potion']=['Energy Potion','Fairy in a Bottle']
        self.assertTrue(check_plan(c,{'steps':[{'kind':'potion','name':'Energy Potion'}]})['allowed'])
        self.assertFalse(check_plan(c,{'steps':[{'kind':'potion','name':'Fairy in a Bottle'}]})['allowed'])
        self.assertFalse(check_plan(c,{'steps':[{'kind':'potion','name':'Energy Potion'},play()]})['allowed'])
        c=context([card('Offering','Skill',0)]);c['state']['hp']=6
        self.assertFalse(check_plan(c,{'steps':[play()]})['allowed'])

    def test_boss_manifest_and_campfire_boundaries(self):
        self.assertEqual(boss_manifest('Bronze Automaton',3)['boost_strength'],3)
        self.assertEqual(boss_manifest('Bronze Automaton',4)['boost_strength'],4)
        self.assertEqual(boss_manifest('Bronze Automaton',9)['boost_block'],12)
        self.assertIn('7: Boost',boss_manifest('Bronze Automaton',19)['timeline'])
        self.assertEqual(boss_manifest('Time Eater',1)['haste_trigger_max_hp'],227)
        self.assertEqual(boss_manifest('Time Eater',9)['haste_heal_target'],240)
        s=dict(hp=45,max_hp=74,deck_size=25,entry_heal_applied=False,next_node_is_boss=True)
        r=campfire_comparison(s,['Eternal Feather','Pantograph','Burning Blood'])
        self.assertEqual(r['smith']['boss_start_hp'],74);self.assertEqual(r['burning_blood_future_heal'],0)
        s['entry_heal_applied']=True;self.assertEqual(campfire_comparison(s,['Eternal Feather','Pantograph'])['entry_heal'],0)
        s['entry_heal_applied']=False;s['deck_size']=24
        self.assertEqual(campfire_comparison(s,['Eternal Feather'])['entry_heal'],12)

    def test_unknown_immediate_effect_and_invalid_enemy_do_not_get_approval(self):
        c=context([card('Hemokinesis')]);c['state']['hp']=1
        self.assertFalse(check_plan(c,{'steps':[play()]})['allowed'])
        c=context();c['state']['enemies'][0]['block']=-99
        self.assertFalse(check_plan(c,{'steps':[play()],'claims_lethal':True})['allowed'])
        c=context();c['state']['enemies'].append(copy.deepcopy(c['state']['enemies'][0]))
        self.assertFalse(check_plan(c,{'steps':[play()]})['allowed'])

    def test_end_turn_uses_current_block_without_triggering_corruption_or_kunai(self):
        c=context([]);c['state']['powers']={'Corruption':True,'Feel No Pain':True}
        c['inventory']['current']['relic']=['Kunai'];c['state']['block']=30
        result=check_plan(c,{'steps':[{'kind':'end_turn'}]})
        self.assertTrue(result['allowed']);self.assertEqual(result['forecast']['player_hp'],50)
        c['state']['unmodeled_effects']=['unknown status']
        self.assertFalse(check_plan(c,{'steps':[{'kind':'end_turn'}]})['allowed'])

    def test_catalog_preserves_research_date(self):
        claims=load_catalog(Path('data/sts_initial_research.json'),KnowledgeBase())
        self.assertEqual(claims[0].sources[0].captured_at.date().isoformat(),'2026-09-02')

    def test_damage_order_rounding_and_exhaust_regressions(self):
        s=CombatSnapshot(3,player_hp=10,incoming_damage=20,end_turn_damage=0,
                         hand=('Bash',),enemies=(CombatEnemy('Slime',10),))
        r=validate_and_predict(s,(CardEffect('Bash',2,'Attack',attack_damage=8,vulnerable=2,target='Slime'),))
        self.assertEqual(r.enemies[0].hp,2);self.assertFalse(r.legal)
        s=CombatSnapshot(3,hand=('Fiend Fire','Defend'),enemies=(CombatEnemy('Slime',30),))
        r=validate_and_predict(s,(CardEffect('Fiend Fire',2,'Attack',target='Slime',exhausts_hand=True,damage_per_exhausted=7),CardEffect('Defend',1,'Skill',block=5)))
        self.assertFalse(r.legal);self.assertIn('Defend is not in the confirmed hand',r.reasons)
        s=CombatSnapshot(1,player_weak=1,enemies=(CombatEnemy('Slime',20,vulnerable=1),))
        r=validate_and_predict(s,(CardEffect('Strike',1,'Attack',attack_damage=7,target='Slime'),))
        self.assertEqual(r.enemies[0].hp,13) # floor(7 * .75 * 1.5) = 7

    def test_stale_arithmetic_and_prediction_phase(self):
        o=StructuredGameState('COMBAT',1,hp=50,max_hp=80,energy=0,block=0,player_vulnerable=0,
            hand=('Strike',),end_turn_damage=0,end_turn_damage_confidence=1,
            enemies=(VisibleEnemy('Slime',40,40,'attack6',block=0,intent_total_damage=6,intent_damage_confidence=1),))
        stale=CombatSnapshot(3,player_hp=50,incoming_damage=6,end_turn_damage=0,enemies=(CombatEnemy('Slime',40),))
        self.assertFalse(preflight_combat(o,stale,(CardEffect('Strike',1,'Attack',attack_damage=6,target='Slime'),)).allowed)
        o=StructuredGameState(**{**o.__dict__,'energy':1})
        r=preflight_combat(o,verify_combat_state(o).snapshot,(CardEffect('Strike',1,'Attack',attack_damage=6,target='Slime'),))
        self.assertTrue(r.allowed);self.assertEqual(r.predicted_state['player_hp'],50)
        self.assertEqual(r.combat.projected_player_hp,44)


class AdvisoryMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.path=Path(self.tmp.name)/'memory.sqlite3';self.db=TelemetryDatabase(self.path)
        self.run=self.db.start_new_run(ascension=1)
        self.floor=self.db.record_floor(run_id=self.run,act=1,floor=1,node_type='enemy',outcome=None,starting_state={})
        self.combat=self.db.start_combat(run_id=self.run,floor_id=self.floor,opening_state={},encounter_name='Cultist',encounter_type='enemy')
        self.turn=self.db.start_combat_turn(combat_id=self.combat,turn_number=1,phase='combat',opening_state={})
        self.db.record_inventory_baseline(run_id=self.run,items=[{'kind':'potion','item':'Energy Potion'}],
            coverage={'card':'unknown','relic':'complete','potion':'complete'},source='visible inventory')

    def tearDown(self): self.tmp.cleanup()

    def observe(self):
        return self.db.record_advisory_snapshot(run_id=self.run,floor_id=self.floor,combat_id=self.combat,
            turn_id=self.turn,state=state(),source='verified current frame')

    def test_linked_consumption_invalidates_snapshot_and_double_use_refused(self):
        self.observe();self.assertTrue(self.db.advisory_context(combat_id=self.combat)['fresh'])
        event=self.db.record_potion_use(run_id=self.run,floor_id=self.floor,combat_id=self.combat,turn_id=self.turn,
            item_name='Energy Potion',source='user confirmed drink',observed_effect={'energy':5})
        self.assertEqual(self.db.inventory_ledger(run_id=self.run)['current']['potion'],[])
        self.assertFalse(self.db.advisory_context(combat_id=self.combat)['fresh'])
        with self.db._connection() as db:
            self.assertEqual(db.execute('SELECT combat_id FROM evidence_events WHERE id=?',(event,)).fetchone()[0],self.combat)
        with self.assertRaises(ValueError):
            self.db.record_potion_use(run_id=self.run,floor_id=self.floor,item_name='Energy Potion',source='again',observed_effect={})

    def test_competing_consumers_only_one_succeeds(self):
        def consume(_):
            try:
                TelemetryDatabase(self.path).record_potion_use(run_id=self.run,floor_id=self.floor,item_name='Energy Potion',source='confirmed',observed_effect={})
                return True
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sum(pool.map(consume,range(2))),1)

    def test_failed_context_link_rolls_back_both_potion_records(self):
        with self.assertRaises(ValueError):
            self.db.record_potion_use(run_id=self.run,floor_id=self.floor,combat_id='missing',item_name='Energy Potion',source='confirmed',observed_effect={})
        self.assertEqual(self.db.inventory_ledger(run_id=self.run)['current']['potion'],['Energy Potion'])

    def test_old_frame_cannot_be_certified_with_new_watermark(self):
        old=state();self.db.record_event(run_id=self.run,floor_id=self.floor,combat_id=self.combat,turn_id=self.turn,
            kind='observed_action',phase='combat',state={},source='user played')
        with self.assertRaisesRegex(ValueError,'predates'):
            self.db.record_advisory_snapshot(run_id=self.run,floor_id=self.floor,combat_id=self.combat,turn_id=self.turn,state=old,source='old screenshot')
        self.observe();self.assertTrue(self.db.advisory_context(combat_id=self.combat)['fresh'])

    def test_decision_consumes_snapshot_and_new_turn_requires_observation(self):
        snap=self.observe();result=self.db.record_checked_advice(combat_id=self.combat,snapshot_id=snap,plan={'steps':[play()]},reasoning='Safe direct attack')
        self.assertIn('decision_id',result);self.assertFalse(self.db.advisory_context(combat_id=self.combat)['fresh'])
        self.db.resolve_decision(decision_id=result['decision_id'],chosen_action={'card':'Strike'},actual_outcome={'hp':50})
        self.observe();self.db.start_combat_turn(combat_id=self.combat,turn_number=2,phase='combat',opening_state={})
        self.assertFalse(self.db.advisory_context(combat_id=self.combat)['fresh'])

    def test_resolved_action_invalidates_image_captured_before_resolution(self):
        snap=self.observe()
        result=self.db.record_checked_advice(combat_id=self.combat,snapshot_id=snap,plan={'steps':[play()]},reasoning='Observed attack')
        old=state()
        self.db.resolve_decision(decision_id=result['decision_id'],chosen_action={'card':'Strike'},actual_outcome={'hp':50})
        with self.assertRaisesRegex(ValueError,'predates'):
            self.db.record_advisory_snapshot(run_id=self.run,floor_id=self.floor,combat_id=self.combat,turn_id=self.turn,state=old,source='old image')

    def test_route_classification_needs_named_evidence(self):
        snap=self.db.record_route_snapshot(run_id=self.run,act=1,floor=1,current_node_id='here',
            legal_next_nodes=[{'node_id':'next','kind':'elite','confidence':1}],resource_context={})
        with self.assertRaisesRegex(ValueError,'classification'):
            self.db.record_route_recommendation(snapshot_id=snap,selected_node_id='next',safety_rationale='HP',reward_tradeoff='Relic')

    def test_campfire_persists_comparison_and_evidence(self):
        self.db.record_inventory_baseline(run_id=self.run,items=[{'kind':'relic','item':'Eternal Feather'},
            {'kind':'relic','item':'Pantograph'}],coverage={'card':'unknown','relic':'complete','potion':'unknown'},source='confirmed')
        result=self.db.record_campfire_advice(run_id=self.run,floor_id=self.floor,state=dict(hp=45,max_hp=74,
            deck_size=25,entry_heal_applied=False,next_node_is_boss=True,healing_modifiers_verified=True,options_verified=True),
            choice='Smith',reasoning='Both reach maximum boss-start HP',source='visible rest screen')
        with self.db._connection() as db:
            prediction=json.loads(db.execute('SELECT prediction_json FROM decisions WHERE id=?',(result,)).fetchone()[0])
        self.assertEqual(prediction['smith']['boss_start_hp'],74)

    def test_partial_snapshot_preserves_unaffected_categories_and_duplicates(self):
        self.db.record_inventory_baseline(run_id=self.run,items=[{'kind':'card','item':'Strike'},{'kind':'relic','item':'Kunai'},
            {'kind':'potion','item':'Energy Potion'},{'kind':'potion','item':'Energy Potion'}],
            coverage={k:'complete' for k in ('card','relic','potion')},source='full inspection')
        self.db.record_inventory_baseline(run_id=self.run,items=[{'kind':'potion','item':'Energy Potion'}],
            coverage={'card':'unknown','relic':'unknown','potion':'partial'},source='partial inspection')
        compact=self.db.inventory_ledger(run_id=self.run,include_history=False)
        self.assertEqual(compact['current'],{'card':['Strike'],'relic':['Kunai'],'potion':['Energy Potion','Energy Potion']})
        self.assertEqual(compact['history'],[]);self.assertEqual(compact['baseline_history'],[])


if __name__ == '__main__': unittest.main()
