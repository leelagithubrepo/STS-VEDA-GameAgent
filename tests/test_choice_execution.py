"""Synthetic UI transitions; these fixtures do not validate console controls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from veda.choice_execution import (SCHEMA, ChoiceError, plan_choice_step,
                                   validate_choice_proposal, verify_choice_step)

NOW = datetime(2026, 9, 27, 6, tzinfo=timezone.utc)


def binding(button, meaning):
    return {'button': button, 'evidence': {'kind': 'reviewed_transition',
        'reviewer': 'synthetic fixture only', 'reference_id': 'synthetic-not-hardware',
        'layout_id': 'synthetic-layout', 'meaning': meaning,
        'before_sha256': 'a' * 64, 'after_sha256': 'b' * 64}}


def observation(screen='selection', mode='immediate', count=1):
    options = [{'id': str(i), 'label': name, 'enabled': True, 'costs': {},
                'activate': binding('cross', f'activate:{i}')}
               for i, name in enumerate(('first', 'second', 'third'))]
    edges = []
    for i in range(2):
        edges += [dict(binding('right', f'focus:{i}->{i+1}'), **{'from': str(i), 'to': str(i+1)}),
                  dict(binding('left', f'focus:{i+1}->{i}'), **{'from': str(i+1), 'to': str(i)})]
    frame = {'frame_id': 'frame-1', 'image_sha256': '1' * 64, 'observed_at': NOW.isoformat()}
    return {'schema': SCHEMA, 'frame': frame,
        'review': {'kind': 'reviewed_choice_ui', 'reviewer': 'synthetic fixture only', 'complete': True,
                   'frame_id': frame['frame_id'], 'image_sha256': frame['image_sha256']},
        'context': {'run_id': 'run', 'floor_id': 'floor-32', 'combat_id': 'combat', 'turn_id': 'turn-1'},
        'inventory_digest': 'c' * 64, 'resources': {'gold': 100, 'hp': 35}, 'facts': {'stage': 'choosing'},
        'ui': {'screen': screen, 'phase': 'choose', 'choice_id': 'choice-1', 'layout_id': 'synthetic-layout',
            'options': options, 'order': ['0', '1', '2'], 'focused_id': '0',
            'selection_mode': mode, 'required_count': count, 'selected_ids': [], 'pending_ids': [],
            'navigation': edges}}


def choice(obs, ids=None, kind='selection'):
    return {'kind': kind, 'choice_id': obs['ui']['choice_id'], 'option_ids': ids or ['0'],
        'review': dict(obs['review'], kind='reviewed_choice'),
        'postconditions': {'screen': 'combat', 'phase': 'result', 'context': deepcopy(obs['context']),
            'resources': deepcopy(obs['resources']), 'inventory_digest': 'unchanged',
            'facts': {'stage': 'resolved'}, 'allow_changed_facts': []}}


def later(obs, number=2):
    obs = deepcopy(obs)
    obs['frame'] = {'frame_id': f'frame-{number}', 'image_sha256': f'{number:064x}',
                    'observed_at': (NOW + timedelta(seconds=number - 1)).isoformat()}
    obs['review'] = dict(obs['review'], frame_id=obs['frame']['frame_id'],
                         image_sha256=obs['frame']['image_sha256'])
    obs['review'].pop('outcome', None)
    return obs


def outcome(obs, step, number=2):
    after = later(obs, number)
    post = step['choice']['postconditions']
    after['context'] = deepcopy(post['context'])
    after['resources'] = deepcopy(post['resources'])
    after['inventory_digest'] = obs['inventory_digest'] if post['inventory_digest'] == 'unchanged' else post['inventory_digest']
    after['facts'].update(post['facts'])
    after['ui'].update(screen=post['screen'], phase=post['phase'], choice_id='result-1',
        options=[], order=[], focused_id=None, selected_ids=[], pending_ids=[], required_count=0, navigation=[])
    after['review']['outcome'] = {'action_id': step['action_id'], 'before_frame_id': obs['frame']['frame_id'],
        'before_sha256': obs['frame']['image_sha256'], 'choice_id': step['choice']['choice_id'],
        'option_ids': step['choice']['option_ids'], 'observed_result': 'Synthetic explicit observed outcome.'}
    return after


def plan(obs, goal=None, **kwargs):
    return plan_choice_step(obs, goal or choice(obs), now=NOW, **kwargs)


def alternative_choice(obs, kind='event'):
    goal = choice(obs, kind=kind)
    combat = deepcopy(goal['postconditions'])
    combat['context'].update(combat_id='revealed-combat', turn_id='revealed-turn')
    combat['resources']['hp'] = 30
    combat['facts'] = {'stage': 'combat-revealed'}
    reward = deepcopy(goal['postconditions'])
    reward.update(screen='reward', phase='choose', inventory_digest='d' * 64)
    reward['resources']['gold'] = 125
    reward['facts'] = {'stage': 'reward-revealed'}
    goal['postconditions'] = {'alternatives': [
        {'id': 'combat', 'postconditions': combat},
        {'id': 'reward', 'postconditions': reward},
    ]}
    return goal


def alternative_outcome(obs, step, branch=0):
    synthetic_step = deepcopy(step)
    synthetic_step['choice']['postconditions'] = step['choice']['postconditions']['alternatives'][branch]['postconditions']
    after = outcome(obs, synthetic_step)
    if after['ui']['phase'] != 'result':
        after['ui'].update(options=[{'id': 'observed-next', 'label': 'Observed next option',
            'enabled': True, 'costs': {}}], order=['observed-next'], focused_id='observed-next', required_count=1)
    return after


class ChoiceExecutionTests(unittest.TestCase):
    def verify(self, step, before, after):
        return verify_choice_step(step, before, after, now=NOW + timedelta(seconds=3))

    def test_graph_navigation_emits_only_first_edge_and_requires_fresh_replan(self):
        before = observation()
        goal = choice(before, ['2'])
        step = plan(before, goal)
        self.assertEqual(step['command']['buttons'], ['right'])
        self.assertEqual(step['expectation'], {'focused_id': '1'})
        after = later(before); after['ui']['focused_id'] = '1'
        result = self.verify(step, before, after)
        self.assertTrue(result['step_verified'])
        self.assertFalse(result['choice_complete'])
        with self.assertRaisesRegex(ChoiceError, 'review references different source'):
            plan_choice_step(after, goal, now=NOW + timedelta(seconds=1))
        goal['review'] = dict(after['review'], kind='reviewed_choice')
        next_step = plan_choice_step(after, goal, now=NOW + timedelta(seconds=1))
        self.assertEqual(next_step['expectation'], {'focused_id': '2'})

    def test_no_navigation_wrap_is_invented(self):
        before = observation(); before['ui']['navigation'] = []
        with self.assertRaisesRegex(ChoiceError, 'no reviewed path'):
            plan(before, choice(before, ['2']))

    def test_navigation_rejects_wrong_focus_or_any_gameplay_change(self):
        before = observation(); step = plan(before, choice(before, ['2']))
        changes = [lambda a: a['ui'].update(focused_id='2'),
                   lambda a: a['resources'].update(hp=34),
                   lambda a: a.update(inventory_digest='d'*64),
                   lambda a: a['context'].update(turn_id='turn-2'),
                   lambda a: a['facts'].update(hidden_change=True),
                   lambda a: a['ui']['options'][0].update(label='different card')]
        for change in changes:
            after = later(before); after['ui']['focused_id'] = '1'; change(after)
            with self.subTest(change=change), self.assertRaises(ChoiceError):
                self.verify(step, before, after)

    def test_exact_two_card_selection_then_pending_confirmation(self):
        before = observation(mode='toggle', count=2)
        goal = choice(before, ['0', '1'])
        step = plan(before, goal)
        self.assertEqual(step['step_kind'], 'select')
        after = later(before); after['ui']['selected_ids'] = ['0']
        self.assertFalse(self.verify(step, before, after)['choice_complete'])
        # A fresh source after separately verified navigation focuses card 1.
        next_before = later(before); next_before['ui'].update(selected_ids=['0'], focused_id='1')
        goal['review'] = dict(next_before['review'], kind='reviewed_choice')
        step = plan_choice_step(next_before, goal, now=NOW + timedelta(seconds=1))
        selected = later(next_before, 3)
        selected['ui'].update(selected_ids=['0','1'], pending_ids=['0','1'], phase='confirm',
                              confirm=binding('triangle', 'confirm:choice-1'))
        self.assertFalse(self.verify(step, next_before, selected)['choice_complete'])
        goal['review'] = dict(selected['review'], kind='reviewed_choice')
        commit = plan_choice_step(selected, goal, now=NOW + timedelta(seconds=2))
        self.assertEqual(commit['command']['buttons'], ['triangle'])
        final = outcome(selected, commit, 4)
        self.assertTrue(self.verify(commit, selected, final)['choice_complete'])

    def test_confirmation_wrong_membership_order_count_or_pending_is_blocked(self):
        before = observation(mode='toggle', count=2)
        goal = choice(before, ['0','1'])
        before['ui'].update(phase='confirm', selected_ids=['0','1'], pending_ids=['0','1'],
                            confirm=binding('triangle', 'confirm:choice-1'))
        for key, value in [('pending_ids',['0','2']),('selected_ids',['1','0']),
                           ('selected_ids',['0']),('required_count',1)]:
            changed = deepcopy(before); changed['ui'][key] = value
            with self.subTest(key=key,value=value), self.assertRaises(ChoiceError):
                plan(changed, goal)

    def test_selection_over_quota_and_unknown_selection_never_toggled_away(self):
        before = observation(mode='toggle', count=1)
        for selected in (['2'], ['0','1']):
            before['ui']['selected_ids'] = selected
            with self.assertRaises(ChoiceError):
                plan(before)

    def test_headbutt_and_warcry_return_selection_verify_exact_zone_facts(self):
        for origin in ('discard', 'hand'):
            before = observation(); before['facts'] = {'origin': origin, 'return_top': None, 'other_cards': ['other']}
            goal = choice(before); goal['postconditions']['facts'] = {'return_top': 'selected-card'}
            step = plan(before, goal); after = outcome(before, step)
            self.assertTrue(self.verify(step, before, after)['choice_complete'])
            after['facts']['return_top'] = 'wrong-card'
            with self.assertRaisesRegex(ChoiceError, 'outcome fact differs'):
                self.verify(step, before, after)

    def test_potion_slot_then_drink_are_separate_verified_inputs(self):
        before = observation(screen='potion_slots')
        before['ui']['options'][0]['label'] = 'Explosive Potion'
        goal = choice(before, kind='potion')
        goal['postconditions'].update(screen='potion_menu', phase='choose', facts={'stage':'drink-menu','slot_id':'slot-1'})
        step = plan(before, goal); menu = outcome(before, step)
        menu['ui'].update(options=[{'id':'drink','label':'Drink','enabled':True,'costs':{},
            'activate':binding('cross','activate:drink')}], order=['drink'], required_count=1, focused_id='drink')
        self.assertTrue(self.verify(step, before, menu)['choice_complete'])
        drink = choice(menu, ['drink'], kind='potion')
        drink['postconditions'].update(inventory_digest='d'*64,
            facts={'stage':'resolved','slot_id':None,'enemy_hp':[20,30]})
        second = plan_choice_step(menu, drink, now=NOW+timedelta(seconds=1))
        after = outcome(menu, second, 3)
        self.assertTrue(self.verify(second, menu, after)['choice_complete'])
        after['inventory_digest'] = before['inventory_digest']
        with self.assertRaisesRegex(ChoiceError, 'inventory'):
            self.verify(second, menu, after)

    def test_shop_price_resource_and_outcome_are_checked_before_and_after(self):
        before = observation(screen='shop'); before['ui']['options'][0]['costs'] = {'gold':75}
        goal = choice(before, kind='shop'); goal['postconditions']['resources']['gold'] = 25
        goal['postconditions']['inventory_digest'] = 'd'*64
        step = plan(before, goal); self.assertTrue(self.verify(step, before, outcome(before, step))['choice_complete'])
        before['resources']['gold'] = 50
        with self.assertRaisesRegex(ChoiceError, 'exceeds observed'):
            plan(before, goal)
        before['resources']['gold'] = 100; goal['postconditions']['resources']['gold'] = {'min':0,'max':100}
        with self.assertRaisesRegex(ChoiceError, 'exact reviewed debit'):
            plan(before, goal)

    def test_disabled_and_unknown_cost_options_are_blocked(self):
        for mutation in (lambda o: o.update(enabled=False), lambda o: o.update(costs=None),
                         lambda o: o.update(costs={'gold':None}), lambda o: o.update(costs={'gold':True})):
            before = observation(); mutation(before['ui']['options'][0])
            with self.assertRaises(ChoiceError): plan(before)

    def test_map_unknown_rolls_require_explicit_allowed_facts_and_named_review(self):
        before = observation(screen='map'); before['context'].update(combat_id=None, turn_id=None)
        goal = choice(before, kind='map'); post=goal['postconditions']
        post['context'].update(floor_id='floor-33', combat_id='new-combat', turn_id='turn-1')
        post['facts'] = {'stage':'entered-node-33'}
        post['allow_changed_facts'] = ['hand','enemies']
        step = plan(before, goal); after = outcome(before, step)
        after['facts'].update(hand={'unknown':True}, enemies=['observed Collector','observed Torch Head'])
        self.assertTrue(self.verify(step, before, after)['choice_complete'])
        after['facts']['unreviewed_status'] = 'change'
        with self.assertRaisesRegex(ChoiceError, 'undeclared fact change'):
            self.verify(step, before, after)

    def test_rest_event_reward_semantic_outcomes_do_not_imply_automatic_mapping(self):
        for kind in ('rest','event','reward'):
            before = observation(screen=kind)
            goal = choice(before, kind=kind)
            if kind=='rest': goal['postconditions']['resources']['hp']=59
            step = plan(before, goal)
            self.assertTrue(self.verify(step, before, outcome(before, step))['choice_complete'])
            before['ui']['options'][0].pop('activate')
            with self.assertRaisesRegex(ChoiceError, 'binding required'): plan(before, goal)

    def test_visible_skip_shortcut_is_bound_to_current_frame_without_focus_guess(self):
        before = observation(screen='card_reward')
        skip = before['ui']['options'][2]; skip['label']='Skip'
        skip['shortcut']={'button':'circle','evidence':{'kind':'visible_hint','reviewer':'synthetic',
            'meaning':'activate:2','layout_id':'synthetic-layout','hint_text':'Circle Skip',
            'frame_id':before['frame']['frame_id'],'image_sha256':before['frame']['image_sha256']}}
        step = plan(before, choice(before,['2'],kind='reward'))
        self.assertEqual(step['command']['buttons'],['circle'])
        self.assertEqual(step['step_kind'],'commit')
        skip['shortcut']['evidence']['frame_id']='other'
        with self.assertRaises(ChoiceError): plan(before, choice(before,['2'],kind='reward'))

    def test_title_only_exact_continue_and_same_run_are_supported(self):
        before = observation(screen='title'); option=before['ui']['options'][0]
        option.update(label='Continue',role='continue_run')
        goal = choice(before,kind='continue_run')
        self.assertEqual(plan(before,goal)['step_kind'],'commit')
        for change in ({'label':'New Run'},{'role':'abandon_run'}):
            bad=deepcopy(before);bad['ui']['options'][0].update(change)
            with self.assertRaisesRegex(ChoiceError,'Continue'):plan(bad,goal)
        goal['postconditions']['context']['run_id']='new-run'
        with self.assertRaisesRegex(ChoiceError,'switch runs'):plan(before,goal)

    def test_before_send_rejects_stale_mutated_or_rebound_proposal(self):
        before=observation();step=plan(before)
        with self.assertRaisesRegex(ChoiceError,'stale'):
            validate_choice_proposal(step,before,now=NOW+timedelta(seconds=6))
        modified=deepcopy(step);modified['command']['buttons']=['triangle']
        with self.assertRaisesRegex(ChoiceError,'modified'):
            validate_choice_proposal(modified,before,now=NOW)
        before['resources']['hp']=34
        with self.assertRaises(ChoiceError):validate_choice_proposal(step,before,now=NOW)

    def test_public_freshness_limit_cannot_disable_age_checks(self):
        before=observation()
        for limit in (None,False,float('nan'),float('inf'),0,-1,31):
            with self.subTest(limit=limit),self.assertRaisesRegex(ChoiceError,'freshness limit'):
                plan(before,max_age_seconds=limit)
        with self.assertRaisesRegex(ChoiceError,'stale'):
            plan_choice_step(before,choice(before),now=NOW+timedelta(days=1))
        before['context']['combat_id']=None
        with self.assertRaisesRegex(ChoiceError,'turn identity requires'):
            plan(before)

    def test_after_review_must_correlate_choice_input_and_both_sources(self):
        before=observation();step=plan(before)
        for field,value in [('action_id','other'),('before_frame_id','other'),('before_sha256','f'*64),
                            ('choice_id','other'),('option_ids',['1']),('observed_result','')]:
            after=outcome(before,step);after['review']['outcome'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ChoiceError,'outcome review'):
                self.verify(step,before,after)
        after=outcome(before,step);after['review'].pop('outcome')
        with self.assertRaises(ChoiceError):self.verify(step,before,after)

    def test_unchanged_frame_and_unchanged_semantics_cannot_verify_commit(self):
        before=observation();goal=choice(before)
        goal['postconditions'].update(screen='selection',phase='choose',facts={'stage':'choosing'})
        step=plan(before,goal);after=outcome(before,step)
        after['ui']=deepcopy(before['ui'])
        with self.assertRaisesRegex(ChoiceError,'no observed semantic'):
            self.verify(step,before,after)
        after=outcome(before,step);after['ui']=deepcopy(before['ui'])
        after['frame']=deepcopy(before['frame']);after['review'].update(frame_id=before['frame']['frame_id'],image_sha256=before['frame']['image_sha256'])
        with self.assertRaisesRegex(ChoiceError,'later distinct'):
            self.verify(step,before,after)

    def test_changed_review_metadata_choice_id_or_highlight_is_not_a_commit_result(self):
        before=observation();goal=choice(before)
        goal['postconditions'].update(screen='selection',phase='choose',facts={'stage':'choosing'})
        step=plan(before,goal)
        for change in (lambda u:u.update(choice_id='renamed-only'),
                       lambda u:u.update(selected_ids=['0']),
                       lambda u:u['options'][0]['activate']['evidence'].update(reference_id='new-review-only')):
            after=outcome(before,step);after['ui']=deepcopy(before['ui']);change(after['ui'])
            with self.subTest(change=change),self.assertRaisesRegex(ChoiceError,'no observed semantic'):
                self.verify(step,before,after)

    def test_malformed_incomplete_future_and_ambiguous_evidence_is_rejected(self):
        mutations=[lambda b:b['review'].update(complete=False),lambda b:b['review'].update(image_sha256='f'*64),
            lambda b:b['ui'].update(order=['1','0','2']),lambda b:b['ui'].update(focused_id='missing'),
            lambda b:b['ui'].update(required_count=True),lambda b:b['ui'].update(selected_ids=[{}]),
            lambda b:b['ui']['navigation'].append(deepcopy(b['ui']['navigation'][0])),
            lambda b:b['ui']['options'][0]['activate'].update(button='options'),
            lambda b:b['frame'].update(observed_at=(NOW+timedelta(seconds=1)).isoformat()),
            lambda b:b['context'].update(run_id=''),lambda b:b['resources'].update(hp=float('nan'))]
        for mutate in mutations:
            before=observation();mutate(before)
            with self.subTest(mutate=mutate),self.assertRaises(ChoiceError):plan(before)

    def test_no_authority_or_input_is_granted_and_inputs_remain_unchanged(self):
        before=observation();goal=choice(before);original=deepcopy((before,goal))
        before['controller_authorized']=True;goal['runtime_authorized']=True
        step=plan(before,goal)
        self.assertFalse(step['controller_authorized']);self.assertFalse(step['runtime_authorized'])
        self.assertEqual(step['command']['action'],'tap');self.assertEqual(len(step['command']['buttons']),1)
        before.pop('controller_authorized');goal.pop('runtime_authorized')
        self.assertEqual((before,goal),original)

    def test_named_default_profile_is_not_a_general_button_mapping_escape(self):
        before = observation(screen='event')
        before['ui']['options'][0]['label'] = '[Talk]'
        before['ui']['options'][0]['activate']['evidence'] = {
            'kind': 'documented_control_profile', 'reviewer': 'synthetic',
            'meaning': 'activate:0', 'layout_id': 'synthetic-layout',
            'control_profile': 'ps5-default-cross-confirm-v1',
            'rule_id': 'neow-opening-talk-confirm-v1',
            'frame_id': before['frame']['frame_id'], 'image_sha256': before['frame']['image_sha256']}
        with self.assertRaisesRegex(ChoiceError, 'Neow opening'):
            plan(before, choice(before, kind='event'))

    def test_event_outcome_branches_match_complete_combat_or_reward_result(self):
        before = observation(screen='event'); before['context'].update(combat_id=None, turn_id=None)
        goal = alternative_choice(before); original = deepcopy((before, goal))
        step = plan(before, goal)
        for index, expected in enumerate(('combat', 'reward')):
            after = alternative_outcome(before, step, index)
            # An externally supplied name is not ground truth for matching.
            after['review']['outcome']['matched_outcome_id'] = 'wrong-claim'
            result = self.verify(step, before, after)
            self.assertTrue(result['choice_complete'])
            self.assertEqual(result['matched_outcome_id'], expected)
            self.assertFalse(result['controller_authorized'])
        self.assertEqual((before, goal), original)

    def test_map_and_reward_choices_can_also_declare_bounded_outcomes(self):
        for kind in ('map', 'reward'):
            before = observation(screen=kind); before['context'].update(combat_id=None, turn_id=None)
            step = plan(before, alternative_choice(before, kind))
            result = self.verify(step, before, alternative_outcome(before, step, 1))
            self.assertEqual(result['matched_outcome_id'], 'reward')

    def test_finite_fact_values_are_separate_exact_branches(self):
        before = observation(screen='event')
        goal = choice(before, kind='event'); first = deepcopy(goal['postconditions'])
        first.update(screen='event', facts={'stage': 'first-result'})
        second = deepcopy(first); second['facts']['stage'] = 'second-result'
        goal['postconditions'] = {'alternatives': [
            {'id': 'first', 'postconditions': first}, {'id': 'second', 'postconditions': second}]}
        step = plan(before, goal)
        for index, expected in enumerate(('first', 'second')):
            after = alternative_outcome(before, step, index)
            self.assertEqual(self.verify(step, before, after)['matched_outcome_id'], expected)
        after['facts']['stage'] = 'unlisted-result'
        with self.assertRaisesRegex(ChoiceError, 'no declared outcome'):
            self.verify(step, before, after)

    def test_outcome_fields_from_different_branches_cannot_be_mixed(self):
        before = observation(screen='event'); before['context'].update(combat_id=None, turn_id=None)
        step = plan(before, alternative_choice(before))
        combat, reward = (alternative_outcome(before, step, i) for i in (0, 1))
        for key in ('context', 'resources', 'inventory_digest', 'facts', 'ui'):
            mixed = deepcopy(combat); mixed[key] = deepcopy(reward[key])
            with self.subTest(key=key), self.assertRaisesRegex(ChoiceError, 'no declared outcome'):
                self.verify(step, before, mixed)
        for mutate in (
            lambda a: a['ui'].update(screen='shop'),
            lambda a: a['context'].update(run_id='different-run'),
            lambda a: a['facts'].update(unlisted_change=True),
            lambda a: a['resources'].update(hp=29),
            lambda a: a.update(inventory_digest='f' * 64),
        ):
            after = deepcopy(combat); mutate(after)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ChoiceError, 'no declared outcome'):
                self.verify(step, before, after)

    def test_every_alternative_must_preserve_run_and_exact_paid_costs(self):
        before = observation(screen='event')
        before['ui']['options'][0]['costs'] = {'gold': 10}
        goal = alternative_choice(before)
        for branch in goal['postconditions']['alternatives']:
            branch['postconditions']['resources']['gold'] = 90
        step = plan(before, goal)
        self.assertEqual(self.verify(step, before, alternative_outcome(before, step, 0))['matched_outcome_id'], 'combat')
        for mutate in (
            lambda p: p['resources'].update(gold=91),
            lambda p: p['resources'].update(gold={'min': 80, 'max': 90}),
            lambda p: p['context'].update(run_id='other-run'),
            lambda p: p.update(inventory_digest='unknown'),
            lambda p: p['resources'].pop('hp'),
            lambda p: p.update(screen='any'),
        ):
            bad = deepcopy(goal); mutate(bad['postconditions']['alternatives'][1]['postconditions'])
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                plan(before, bad)

    def test_multiple_matching_alternatives_cannot_be_claimed_as_success(self):
        before = observation(screen='event'); goal = choice(before, kind='event')
        first = deepcopy(goal['postconditions']); first['resources']['hp'] = {'min': 20, 'max': 35}
        second = deepcopy(first); second['resources']['hp'] = {'min': 30, 'max': 40}
        goal['postconditions'] = {'alternatives': [
            {'id': 'lower-range', 'postconditions': first}, {'id': 'upper-range', 'postconditions': second}]}
        step = plan(before, goal); after = alternative_outcome(before, step, 0)
        after['resources']['hp'] = 32
        with self.assertRaisesRegex(ChoiceError, 'overlapping outcome'):
            self.verify(step, before, after)
        after['resources']['hp'] = 25
        self.assertEqual(self.verify(step, before, after)['matched_outcome_id'], 'lower-range')
        after['resources']['hp'] = 38
        self.assertEqual(self.verify(step, before, after)['matched_outcome_id'], 'upper-range')

    def test_malformed_duplicate_nested_or_excessive_branches_are_rejected(self):
        before = observation(screen='event'); goal = alternative_choice(before)
        mutations = [
            lambda p: p.update(extra=True),
            lambda p: p.update(alternatives=[]),
            lambda p: p.update(alternatives=p['alternatives'][:1]),
            lambda p: p.update(alternatives='unknown'),
            lambda p: p.update(alternatives=[None, None]),
            lambda p: p['alternatives'][0].update(id=''),
            lambda p: p['alternatives'][1].update(id='combat'),
            lambda p: p['alternatives'][0].update(extra=True),
            lambda p: p['alternatives'][0].pop('postconditions'),
            lambda p: p['alternatives'][0].update(postconditions={'alternatives': []}),
            lambda p: p['alternatives'][1].update(postconditions=deepcopy(p['alternatives'][0]['postconditions'])),
            lambda p: p.update(alternatives=[{'id': str(i), 'postconditions': dict(deepcopy(p['alternatives'][0]['postconditions']),
                facts={'stage': str(i)})} for i in range(9)]),
        ]
        for mutate in mutations:
            bad = deepcopy(goal); mutate(bad['postconditions'])
            with self.subTest(mutate=mutate), self.assertRaises(ChoiceError):
                plan(before, bad)
        # Eight distinct complete alternatives are accepted at the bound.
        goal['postconditions']['alternatives'] = [
            {'id': str(i), 'postconditions': dict(deepcopy(goal['postconditions']['alternatives'][0]['postconditions']),
                facts={'stage': str(i)})} for i in range(8)]
        self.assertEqual(plan(before, goal)['step_kind'], 'commit')

    def test_alternatives_do_not_expand_other_choice_kinds_or_neow_rule(self):
        for kind, screen in (('selection', 'selection'), ('potion', 'potion_slots'), ('rest', 'rest'), ('shop', 'shop')):
            before = observation(screen=screen)
            with self.subTest(kind=kind), self.assertRaisesRegex(ChoiceError, 'only for map, event and reward'):
                plan(before, alternative_choice(before, kind))
        from tests.test_neow_start import review_input
        from veda.neow_start import build_neow_talk_from_review
        inputs = review_input(); packet = build_neow_talk_from_review(**inputs)
        goal = deepcopy(packet['choice']); exact = deepcopy(goal['postconditions'])
        other = deepcopy(exact); other['resources']['gold'] += 1
        goal['postconditions'] = {'alternatives': [
            {'id': 'normal-talk', 'postconditions': exact}, {'id': 'expanded-talk', 'postconditions': other}]}
        with self.assertRaisesRegex(ChoiceError, 'Neow Talk only advances'):
            plan_choice_step(packet['observation'], goal, now=inputs['now'])

    def test_alternatives_keep_source_integrity_outcome_review_and_progress_checks(self):
        before = observation(screen='event'); goal = alternative_choice(before); step = plan(before, goal)
        after = alternative_outcome(before, step)
        after['review']['outcome']['action_id'] = 'wrong-input'
        with self.assertRaisesRegex(ChoiceError, 'outcome review'):
            self.verify(step, before, after)
        after = alternative_outcome(before, step)
        after['frame']['observed_at'] = NOW.isoformat()
        with self.assertRaisesRegex(ChoiceError, 'later distinct'):
            self.verify(step, before, after)
        changed = deepcopy(step)
        changed['choice']['postconditions']['alternatives'][0]['postconditions']['resources']['hp'] = 99
        with self.assertRaisesRegex(ChoiceError, 'modified'):
            validate_choice_proposal(changed, before, now=NOW)
        # A permitted branch that merely repeats all before semantics is not a
        # successful commit, even when it is the unique matching branch.
        same = choice(before, kind='event')['postconditions']
        same.update(screen='event', phase='choose', facts=deepcopy(before['facts']))
        other = deepcopy(same); other['facts']['stage'] = 'different'
        goal['postconditions'] = {'alternatives': [
            {'id': 'unchanged', 'postconditions': same}, {'id': 'changed', 'postconditions': other}]}
        step = plan(before, goal); after = alternative_outcome(before, step)
        after['ui'] = deepcopy(before['ui'])
        with self.assertRaisesRegex(ChoiceError, 'no observed semantic'):
            self.verify(step, before, after)

    def test_exact_outcome_and_navigation_return_no_matched_branch(self):
        before = observation(); step = plan(before)
        self.assertIsNone(self.verify(step, before, outcome(before, step))['matched_outcome_id'])
        before = observation(screen='event'); goal = alternative_choice(before); goal['option_ids'] = ['2']
        step = plan(before, goal); after = later(before); after['ui']['focused_id'] = '1'
        result = self.verify(step, before, after)
        self.assertFalse(result['choice_complete'])
        self.assertIsNone(result['matched_outcome_id'])

    def test_exact_fact_values_do_not_coerce_booleans_or_nested_json_types(self):
        for expected, actual in ((1, True), (False, 0), (1, 1.0),
                                 ({'count': 1}, {'count': True}),
                                 ([{'count': 1}], [{'count': True}])):
            before = observation(); goal = choice(before)
            goal['postconditions']['facts'] = {'stage': 'resolved', 'typed_value': expected}
            step = plan(before, goal); after = outcome(before, step)
            after['facts']['typed_value'] = actual
            with self.subTest(expected=expected, actual=actual), self.assertRaisesRegex(ChoiceError, 'outcome fact differs'):
                self.verify(step, before, after)

    def test_unchanged_facts_and_navigation_also_preserve_exact_json_types(self):
        before = observation(); before['facts']['nested'] = {'counts': [1]}
        step = plan(before); after = outcome(before, step)
        after['facts']['nested']['counts'] = [True]
        with self.assertRaisesRegex(ChoiceError, 'undeclared fact change'):
            self.verify(step, before, after)
        step = plan(before, choice(before, ['2']))
        after = later(before); after['ui']['focused_id'] = '1'
        after['facts']['nested']['counts'] = [True]
        with self.assertRaisesRegex(ChoiceError, 'changed during choice navigation'):
            self.verify(step, before, after)

    def test_alternative_fact_matching_and_duplicate_checks_are_type_sensitive(self):
        before = observation(screen='event'); goal = choice(before, kind='event')
        first = deepcopy(goal['postconditions']); first['facts']['typed_value'] = {'values': [1]}
        second = deepcopy(first); second['facts']['typed_value'] = {'values': [True]}
        goal['postconditions'] = {'alternatives': [
            {'id': 'integer', 'postconditions': first}, {'id': 'boolean', 'postconditions': second}]}
        step = plan(before, goal)
        for index, expected in enumerate(('integer', 'boolean')):
            after = alternative_outcome(before, step, index)
            self.assertEqual(self.verify(step, before, after)['matched_outcome_id'], expected)
        goal['postconditions']['alternatives'][1]['postconditions']['facts']['typed_value'] = {'values': [2]}
        step = plan(before, goal); after = alternative_outcome(before, step)
        after['facts']['typed_value'] = {'values': [True]}
        with self.assertRaisesRegex(ChoiceError, 'no declared outcome'):
            self.verify(step, before, after)


if __name__=='__main__':unittest.main()
