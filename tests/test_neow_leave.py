"""Synthetic Neow Leave contracts, not live controller calibration."""
from copy import deepcopy
from datetime import timedelta
import unittest

from tests import test_choice_execution as fixtures
from tests import test_menu_controls as menus
from veda.choice_execution import ChoiceError, plan_choice_step, verify_choice_step
from veda.menu_controls import (CONTROL_PROFILE, NEOW_LEAVE_RULE,
                                bind_reviewed_menu_controls)

NOW = fixtures.NOW


def leave():
    obs = menus.event()
    obs['facts'] = {'event_id': 'neow', 'event_phase': 'reward_resolved', 'act': 1,
                    'floor': 0, 'dialogue_text': 'Granted...', 'upgraded_card_name': 'Bash+'}
    obs['ui'].update(menu_family='event_leave', options=[
        {'id': 'leave', 'label': '[Leave]', 'enabled': True, 'costs': {}}],
        order=['leave'], focused_id='leave')
    return obs


def bound(obs=None):
    return bind_reviewed_menu_controls(obs or leave(), control_profile=CONTROL_PROFILE, now=NOW)


def goal(obs, phase='result'):
    result = fixtures.choice(obs, ['leave'], kind='event')
    result['postconditions'].update(screen='map', phase=phase, facts={},
        allow_changed_facts=['event_phase', 'dialogue_text'])
    return result


class NeowLeaveTests(unittest.TestCase):
    def test_named_cross_rule_has_current_source_without_fake_visible_hint(self):
        raw = leave(); original = deepcopy(raw); obs = bound(raw)
        step = plan_choice_step(obs, goal(obs), now=NOW)
        self.assertEqual(['cross'], step['command']['buttons'])
        self.assertEqual('commit', step['step_kind'])
        self.assertEqual(original, raw)
        proof = obs['ui']['options'][0]['activate']['evidence']
        self.assertEqual(NEOW_LEAVE_RULE, proof['rule_id'])
        self.assertEqual('documented_control_profile', proof['kind'])
        self.assertEqual(obs['frame']['image_sha256'], proof['image_sha256'])
        self.assertFalse(set(proof) & {'hint_text', 'reference_id', 'before_sha256', 'after_sha256'})

    def test_granted_literal_variants_only(self):
        for text in ('Granted...', 'Granted…'):
            obs = leave(); obs['facts']['dialogue_text'] = text
            self.assertEqual(NEOW_LEAVE_RULE, bound(obs)['ui']['options'][0]['activate']['evidence']['rule_id'])
        for text in ('', 'Granted', 'Reward selected', 'Granted... Lose HP', None, ['Granted...']):
            obs = leave(); obs['facts']['dialogue_text'] = text
            with self.subTest(text=text), self.assertRaises(ChoiceError): bound(obs)

    def test_exact_neow_phase_act_floor_and_noncombat_context(self):
        for key, value in [('event_id', 'other'), ('event_phase', 'reward_options'),
                           ('event_phase', 'opening_dialogue'), ('event_phase', None),
                           ('act', True), ('act', 2), ('floor', False), ('floor', 1)]:
            obs = leave(); obs['facts'][key] = value
            with self.subTest(key=key,value=value), self.assertRaises(ChoiceError): bound(obs)
        for key in ('combat_id', 'turn_id'):
            obs = leave(); obs['context'][key] = 'unrelated'
            with self.subTest(key=key), self.assertRaises(ChoiceError): bound(obs)

    def test_result_phase_is_not_an_actionable_leave(self):
        obs = leave(); obs['ui'].update(phase='result', required_count=0)
        with self.assertRaises(ChoiceError): bound(obs)

    def test_single_free_enabled_leave_without_grid_or_selection(self):
        changes = [('screen','reward'), ('selection_mode','toggle'), ('selected_ids',['leave']),
                   ('pending_ids',['leave']), ('focused_id',None), ('grid',{'complete':True,'cells':[]}),
                   ('upgrade_preview',{}), ('control_layout','custom')]
        for key,value in changes:
            obs=leave(); obs['ui'][key]=value
            with self.subTest(key=key), self.assertRaises(ChoiceError): bound(obs)
        for key,value in [('enabled',False),('costs',{'gold':1}),('label','Continue'),
                          ('shortcut',fixtures.binding('triangle','activate:leave'))]:
            obs=leave(); obs['ui']['options'][0][key]=value
            with self.subTest(key=key), self.assertRaises(ChoiceError): bound(obs)
        obs=leave(); obs['ui']['options'].append({'id':'other','label':'Reward','enabled':False,'costs':{}})
        obs['ui']['order'].append('other')
        with self.assertRaises(ChoiceError): bound(obs)
        obs=leave(); obs['ui']['confirm']=fixtures.binding('triangle','confirm:choice-1')
        with self.assertRaises(ChoiceError): bound(obs)

    def test_reviewed_living_resources_required(self):
        for update in ({'hp':0},{'hp':81},{'max_hp':None},{'gold':True}):
            obs=leave(); obs['resources'].update(update)
            with self.subTest(update=update), self.assertRaises(ChoiceError): bound(obs)

    def test_unchanged_context_resources_inventory_are_mandatory(self):
        obs=bound()
        for key,value in [('screen','event'),('screen','combat'),('phase','confirm'),
                           ('context',dict(obs['context'],floor_id='next-floor')),
                           ('resources',dict(obs['resources'],gold=101)),
                           ('resources',dict(obs['resources'],hp={'min':30,'max':35})),
                           ('inventory_digest','d'*64)]:
            wanted=goal(obs); wanted['postconditions'][key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ChoiceError):
                plan_choice_step(obs,wanted,now=NOW)
        wanted=goal(obs); wanted['postconditions']['inventory_digest']=obs['inventory_digest']
        self.assertEqual('commit',plan_choice_step(obs,wanted,now=NOW)['step_kind'])

    def test_unrelated_fact_changes_and_alternative_outcomes_rejected(self):
        obs=bound()
        for facts, allowed in [({'act':True},[]),({'floor':1},[]),({'upgraded_card_name':'Strike+'},[]),
                               ({},['upgraded_card_name']),({'new_reward':'potion'},[]),({'new_reward':None},[])]:
            wanted=goal(obs); wanted['postconditions'].update(facts=facts,allow_changed_facts=allowed)
            with self.subTest(facts=facts,allowed=allowed), self.assertRaises(ChoiceError):
                plan_choice_step(obs,wanted,now=NOW)
        wanted=goal(obs); post=deepcopy(wanted['postconditions']); other=deepcopy(post); other['screen']='event'
        wanted['postconditions']={'alternatives':[{'id':'map','postconditions':post},{'id':'other','postconditions':other}]}
        with self.assertRaises(ChoiceError): plan_choice_step(obs,wanted,now=NOW)

    def test_map_result_verifies_but_cannot_dispatch_another_action(self):
        before=bound(); step=plan_choice_step(before,goal(before),now=NOW)
        after=fixtures.outcome(before,step)
        after['ui'].pop('menu_family'); after['facts']['event_phase']='completed'; after['facts'].pop('dialogue_text')
        result=verify_choice_step(step,before,after,now=NOW+timedelta(seconds=1))
        self.assertTrue(result['choice_complete'])
        with self.assertRaises(ChoiceError):
            plan_choice_step(after,fixtures.choice(after,['leave'],kind='map'),now=NOW+timedelta(seconds=1))
        for field,value in [('resources',dict(after['resources'],hp=34)),('inventory_digest','e'*64),
                            ('context',dict(after['context'],floor_id='next-floor'))]:
            changed=deepcopy(after); changed[field]=value
            with self.subTest(field=field), self.assertRaises(ChoiceError):
                verify_choice_step(step,before,changed,now=NOW+timedelta(seconds=1))

    def test_reviewed_map_choose_arrival_does_not_invent_route_controls(self):
        before=bound(); step=plan_choice_step(before,goal(before,'choose'),now=NOW)
        after=fixtures.outcome(before,step)
        after['ui'].pop('menu_family')
        after['ui'].update(required_count=1,options=[{'id':'route-1','label':'Observed node','enabled':True,'costs':{}}],
                           order=['route-1'],focused_id='route-1')
        self.assertTrue(verify_choice_step(step,before,after,now=NOW+timedelta(seconds=1))['choice_complete'])
        with self.assertRaisesRegex(ChoiceError,'control|activation|binding'):
            plan_choice_step(after,fixtures.choice(after,['route-1'],kind='map'),now=NOW+timedelta(seconds=1))

    def test_source_freshness_and_forged_rule_evidence_remain_enforced(self):
        with self.assertRaises(ChoiceError):
            bind_reviewed_menu_controls(leave(),control_profile=CONTROL_PROFILE,now=NOW+timedelta(seconds=31))
        for patch in ({'frame_id':'other'},{'image_sha256':'e'*64},{'hint_text':'Cross Leave'},
                      {'reference_id':'invented'},{'control_profile':'other'}):
            obs=bound(); obs['ui']['options'][0]['activate']['evidence'].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChoiceError): plan_choice_step(obs,goal(obs),now=NOW)
        obs=bound(); obs['ui']['options'][0]['activate']['button']='triangle'
        with self.assertRaises(ChoiceError): plan_choice_step(obs,goal(obs),now=NOW)
        for button in ('cross','triangle'):
            obs=leave(); obs['ui']['options'][0]['activate']=fixtures.binding(button,'activate:leave')
            with self.subTest(button=button),self.assertRaises(ChoiceError): bound(obs)

    def test_reward_options_and_talk_are_not_broadened(self):
        obs=leave(); obs['ui']['menu_family']='event_options'
        with self.assertRaisesRegex(ChoiceError,'reward_options'): bound(obs)
        obs=menus.event(); obs['ui']['menu_family']='event_leave'
        with self.assertRaises(ChoiceError): bound(obs)


if __name__ == '__main__': unittest.main()
