from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from veda.loot import plan_loot, decision_key, canonical_reward_role
from veda.menu_requests import write_menu_request
from veda.menu_results import write_menu_result
from veda.menu_controls import CONTROL_PROFILE
from veda.choice_execution import plan_choice_step
from tests.test_menu_requests import make_capture


def snapshot(cards=False):
    options = ([{'id':'strike','label':'Strike','enabled':True,'costs':{},'role':'card','reward':{'name':'Strike'}},
                {'id':'skip','label':'Skip','enabled':True,'costs':{},'role':'skip'}] if cards else
               [{'id':'gold','label':'27 Gold','enabled':True,'costs':{},'role':'gold','reward':{'amount':27}},
                {'id':'cards','label':'Card Reward','enabled':True,'costs':{},'role':'card_reward'}])
    return {'schema':'veda.loot-snapshot.v1','context':{'run_id':'run','floor_id':'floor','combat_id':None,'turn_id':None},
            'inventory':{'current':{'card':['Strike'],'relic':['Burning Blood'],'potion':[]},
                         'coverage':{'card':'complete','relic':'complete','potion':'complete'},'properties':{}},
            'resources':{'hp':60,'max_hp':80,'gold':100},
            'facts':{'reward_source':'combat','potion_capacity':3},
            'ui':{'menu_family':'loot_cards' if cards else 'loot_rewards','choice_id':'reward-1',
                  'focused_id':options[0]['id'],'options':options,
                  'grid':{'complete':True,'cells':[{'id':o['id'],'row':i,'column':0} for i,o in enumerate(options)]}}}

class LootTests(unittest.TestCase):
    def test_reward_aliases_normalize_without_changing_the_visible_id(self):
        self.assertEqual('potion', canonical_reward_role({'id': 'potion-reward', 'label': 'Smoke Potion'}))
        self.assertEqual('potion', canonical_reward_role({'id': 'colorless-potion', 'label': 'Colorless Potion'}))
        self.assertEqual('skip', canonical_reward_role({'id': 'skip-potion', 'label': 'Skip Potion'}))
        self.assertEqual('gold', canonical_reward_role({'id': 'gold', 'label': '15 Gold'}))
        self.assertIsNone(canonical_reward_role({'id': 'relic-1', 'label': 'Potion Belt'}))

    def test_decision_key_matches_plan_when_role_metadata_is_omitted(self):
        source = snapshot(True)
        source['ui']['options'][1].pop('role')
        decision = {'option_id': 'skip', 'reason': 'Skip once.', 'decision_key': decision_key(source)}
        self.assertEqual('planned', plan_loot(source, decision)['status'])

    def test_gold_is_deterministic_and_keeps_card_reward_for_later(self):
        source=snapshot();original=deepcopy(source)
        result=plan_loot(source)
        self.assertTrue(result['routine']); self.assertEqual(['gold'],result['draft']['choice']['option_ids'])
        self.assertEqual('Collect available gold.',result['draft']['reasoning'])
        self.assertEqual(127,result['draft']['choice']['postconditions']['resources']['gold'])
        self.assertEqual({'kind': 'routine_loot', 'option_id': 'gold', 'focused': True,
                          'next_step': 'commit',
                          'after_commit': 'inspect_one_settled_after_frame_and_continue_from_it',
                          'decision_budget_seconds': 10}, result['fast_path'])
        self.assertEqual(original,source)

    def test_unfocused_free_reward_names_focus_then_commit_fast_path(self):
        source = snapshot()
        source['ui']['focused_id'] = 'cards'
        result = plan_loot(source)
        self.assertEqual('gold', result['fast_path']['option_id'])
        self.assertFalse(result['fast_path']['focused'])
        self.assertEqual('focus_then_commit', result['fast_path']['next_step'])

    def test_spoils_title_uses_the_same_gold_hot_path(self):
        source = snapshot()
        source['ui']['menu_family'] = 'Spoils!'
        result = plan_loot(source)
        self.assertEqual('planned', result['status'])
        self.assertTrue(result['routine'])
        self.assertEqual(['gold'], result['draft']['choice']['option_ids'])
        self.assertEqual('loot_rewards', result['draft']['ui']['menu_family'])

    def test_card_choice_or_skip_is_made_once_and_reused_across_focus(self):
        source=snapshot(True); self.assertEqual('strategy_required',plan_loot(source)['status'])
        decision={'option_id':'skip','reason':'This Strike does not improve the deck.','decision_key':decision_key(source)}
        first=plan_loot(source,decision)
        source['ui']['focused_id']='skip'
        second=plan_loot(source,first['decision'])
        self.assertEqual(first['decision'],second['decision'])
        self.assertEqual('unchanged',second['draft']['choice']['postconditions']['inventory'])
        source['resources']['hp']-=1
        with self.assertRaisesRegex(ValueError,'changed'):plan_loot(source,decision)

    def test_full_unknown_or_sozu_potion_inventory_does_not_discard(self):
        for mode in ('empty','full','unknown','sozu'):
            source=snapshot();source['ui']['options'][0].update(id='potion',label='Energy Potion',role='potion',reward={'name':'Energy Potion'})
            source['ui']['focused_id']='potion';source['ui']['grid']['cells'][0]['id']='potion'
            if mode=='full':source['inventory']['current']['potion']=['Fire Potion']*3
            if mode=='unknown':source['inventory']['coverage']['potion']='unknown'
            if mode=='sozu':source['inventory']['current']['relic'].append('Sozu')
            result=plan_loot(source)
            self.assertEqual(['potion'] if mode=='empty' else ['cards'],result['draft']['choice']['option_ids'])

    def test_full_potion_belt_exposes_a_bounded_skip_fallback(self):
        source = snapshot()
        source['inventory']['current']['potion'] = ['Energy Potion', 'Smoke Bomb', 'Ancient Potion']
        source['ui']['options'] = [
            {'id': 'colorless-potion', 'label': 'Colorless Potion', 'enabled': True,
             'costs': {}, 'role': 'potion', 'reward': {'name': 'Colorless Potion'}},
            {'id': 'skip-potion', 'label': 'Skip Potion', 'enabled': True,
             'costs': {}, 'role': 'skip',
             'shortcut_hint': {'button': 'triangle', 'hint_text': 'Triangle Skip Potion'}},
        ]
        source['ui']['focused_id'] = 'colorless-potion'
        source['ui']['grid']['cells'] = [
            {'id': 'colorless-potion', 'row': 0, 'column': 0},
            {'id': 'skip-potion', 'row': 0, 'column': 1},
        ]
        blocked = plan_loot(source)
        self.assertEqual('strategy_required', blocked['status'])
        self.assertEqual('skip-potion', blocked['fallback']['option_id'])
        fallback = plan_loot(source, fallback=True)
        self.assertEqual('planned', fallback['status'])
        self.assertEqual(['skip-potion'], fallback['draft']['choice']['option_ids'])
        self.assertEqual('map', fallback['draft']['choice']['postconditions']['screen'])
        self.assertEqual('commit', fallback['fast_path']['next_step'])

    def test_full_potion_fallback_uses_visible_triangle_without_focus_tap(self):
        source = snapshot()
        source['inventory']['current']['potion'] = ['Energy Potion', 'Smoke Bomb', 'Ancient Potion']
        source['ui']['options'] = [
            {'id': 'colorless-potion', 'label': 'Colorless Potion', 'enabled': True,
             'costs': {}, 'role': 'potion', 'reward': {'name': 'Colorless Potion'}},
            {'id': 'skip-potion', 'label': 'Skip Potion', 'enabled': True, 'costs': {},
             'role': 'skip', 'shortcut_hint': {'button': 'triangle', 'hint_text': 'Triangle Skip Potion'}},
        ]
        source['ui'].update(focused_id='colorless-potion', choice_id='combat-spoils',
                            grid={'complete': True, 'cells': [
                                {'id': 'colorless-potion', 'row': 0, 'column': 0},
                                {'id': 'skip-potion', 'row': 0, 'column': 1}]})
        planned = plan_loot(source, fallback=True)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(timezone.utc) - timedelta(seconds=2)
            capture = make_capture(root, now)
            request = root / 'action.json'
            write_menu_request(planned['draft'], capture=capture, reviewer='Fixture',
                               evidence_note='Visible triangle fallback.', reviewed=True,
                               control_profile=CONTROL_PROFILE, output=request, now=now + timedelta(seconds=1))
            packet = json.loads(request.read_text())
            proposal = plan_choice_step(packet['observation'], packet['choice'],
                                        now=now + timedelta(seconds=1), action_id='skip-potion')
            self.assertEqual('commit', proposal['step_kind'])
            self.assertEqual(['triangle'], proposal['command']['buttons'])

    def test_no_free_gold_assumption_for_paid_or_disabled_option(self):
        for patch in ({'costs':{'hp':10}}, {'enabled':False}):
            source=snapshot();source['ui']['options'][0].update(patch);source['ui']['focused_id']='cards'
            self.assertEqual(['cards'],plan_loot(source)['draft']['choice']['option_ids'])

    def test_free_gold_packages_single_cross_and_verifies_actual_total(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);now=datetime.now(timezone.utc)-timedelta(seconds=5)
            source=snapshot();planned=plan_loot(source)
            image=make_capture(root,now)
            request=root/'action.json'
            write_menu_request(planned['draft'],capture=image,reviewer='Fixture',evidence_note='Synthetic loot',
                reviewed=True,control_profile=CONTROL_PROFILE,output=request,now=now+timedelta(seconds=1))
            packet=json.loads(request.read_text())
            proposal=plan_choice_step(packet['observation'],packet['choice'],now=now+timedelta(seconds=1),action_id='gold-action')
            self.assertEqual(['cross'],proposal['command']['buttons'])
            session=root/'state.json';session.write_text(json.dumps({'schema':'veda.reviewed-play.v1','run_id':'run',
                'pending':{'status':'attempted','action_id':'gold-action','attempted_at':(now+timedelta(seconds=1)).isoformat(),
                    'request':packet,'proposal':proposal,'decision_id':'decision'}}))
            after=deepcopy(source['ui']);after['options']=after['options'][1:];after['grid']={'complete':True,'cells':[{'id':'cards','row':0,'column':0}]};after['focused_id']='cards'
            actual={'schema':'veda.menu-result.v1','action_id':'gold-action','resources':dict(source['resources'],gold=127),
                'inventory':'unchanged','facts':'unchanged','result':{'kind':'menu','ui':after},'observed_result':'Gold is now 127; gold row gone.'}
            result_image=make_capture(root,now+timedelta(seconds=2),color=(70,80,90),index='b')
            output=root/'result.json'
            write_menu_result(actual,session=session,action_id='gold-action',capture=result_image,
                reviewer='Fixture',evidence_note='Actual synthetic result.',reviewed=True,
                control_profile=CONTROL_PROFILE,output=output,now=now+timedelta(minutes=20))
            result=json.loads(output.read_text());self.assertEqual('verify',result['operation']);self.assertEqual({},result['telemetry'])

    def test_visible_skip_hint_survives_focus(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);now=datetime.now(timezone.utc)-timedelta(seconds=6)
            source=snapshot(True)
            source['ui']['options'][1]['activate_hint']={'button':'square','hint_text':'Square Skip'}
            choose={'option_id':'skip','reason':'Skip once.','decision_key':decision_key(source)}
            planned=plan_loot(source,choose)
            image=make_capture(root,now)
            request=root/'action.json'
            write_menu_request(planned['draft'],capture=image,reviewer='Fixture',evidence_note='Synthetic card offers.',
                reviewed=True,control_profile=CONTROL_PROFILE,output=request,now=now+timedelta(seconds=1))
            packet=json.loads(request.read_text())
            proposal=plan_choice_step(packet['observation'],packet['choice'],now=now+timedelta(seconds=1),action_id='focus-skip')
            self.assertEqual(['down'],proposal['command']['buttons'])
            session=root/'state.json';session.write_text(json.dumps({'schema':'veda.reviewed-play.v1','run_id':'run',
                'pending':{'status':'attempted','action_id':'focus-skip','attempted_at':(now+timedelta(seconds=1)).isoformat(),
                    'request':packet,'proposal':proposal,'decision_id':'decision'}}))
            actual={'schema':'veda.menu-result.v1','action_id':'focus-skip','resources':'unchanged',
                'inventory':'unchanged','facts':'unchanged','result':{'kind':'focus','focused_id':'skip'},
                'observed_result':'Skip focused; Square hint and all other facts unchanged.'}
            after_image=make_capture(root,now+timedelta(seconds=2),color=(40,80,90),index='b')
            output=root/'result.json'
            write_menu_result(actual,session=session,action_id='focus-skip',capture=after_image,
                reviewer='Fixture',evidence_note='Actual synthetic focus and hint.',reviewed=True,
                control_profile=CONTROL_PROFILE,output=output,now=now+timedelta(minutes=20))
            after=json.loads(output.read_text())['after']['observation']
            self.assertEqual('square',after['ui']['options'][1]['activate']['button'])
            self.assertEqual(after['frame']['frame_id'],after['ui']['options'][1]['activate']['evidence']['frame_id'])

    def test_card_reward_records_observed_inventory_delta_with_duplicates(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);now=datetime.now(timezone.utc)-timedelta(seconds=6)
            source=snapshot(True)
            source['ui'].update(phase='confirm', selected_ids=['strike'], pending_ids=['strike'],
                                confirm_hint={'button':'cross','hint_text':'Cross Confirm'})
            decision={'option_id':'strike','reason':'Synthetic explicit card choice.', 'decision_key':decision_key(source)}
            planned=plan_loot(source,decision)
            before_image=make_capture(root,now)
            request=root/'action.json'
            write_menu_request(planned['draft'],capture=before_image,reviewer='Fixture',evidence_note='Synthetic cards.',
                reviewed=True,control_profile=CONTROL_PROFILE,output=request,now=now+timedelta(seconds=1))
            packet=json.loads(request.read_text())
            proposal=plan_choice_step(packet['observation'],packet['choice'],now=now+timedelta(seconds=1),action_id='take-card')
            self.assertEqual('commit',proposal['step_kind'])
            session=root/'state.json';session.write_text(json.dumps({'schema':'veda.reviewed-play.v1','run_id':'run',
                'pending':{'status':'attempted','action_id':'take-card','attempted_at':(now+timedelta(seconds=1)).isoformat(),
                    'request':packet,'proposal':proposal,'decision_id':'decision'}}))
            actual_inventory=deepcopy(source['inventory']);actual_inventory['current']['card'].append('Strike')
            actual={'schema':'veda.menu-result.v1','action_id':'take-card','resources':'unchanged',
                'inventory':actual_inventory,'facts':'unchanged','result':{'kind':'menu','ui':snapshot()['ui']},
                'observed_result':'Returned to rewards; observed deck now contains two Strikes.'}
            image=make_capture(root,now+timedelta(seconds=2),color=(55,80,90),index='b')
            output=root/'result.json'
            write_menu_result(actual,session=session,action_id='take-card',capture=image,
                reviewer='Fixture',evidence_note='Actual synthetic inventory.',reviewed=True,
                control_profile=CONTROL_PROFILE,output=output,now=now+timedelta(minutes=20))
            events=json.loads(output.read_text())['telemetry']['inventory_events']
            self.assertEqual(1,len(events));self.assertEqual('acquired',events[0]['action'])
            self.assertEqual('Strike',events[0]['item']);self.assertEqual('card',events[0]['kind'])
