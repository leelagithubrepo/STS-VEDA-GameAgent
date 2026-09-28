"""Synthetic menu reviews; default-profile rules are not hardware validation."""
from copy import deepcopy
from datetime import timedelta
import unittest

from tests import test_choice_execution as fixtures
from veda.choice_execution import ChoiceError, plan_choice_step, verify_choice_step
from veda.menu_controls import (CONTROL_PROFILE, EVENT_RULE, GRID_FOCUS_RULE,
                                bind_reviewed_menu_controls)
from veda.neow_start import uses_neow_control_rule

NOW = fixtures.NOW


def event():
    obs = fixtures.observation('event')
    obs['context'].update(combat_id=None, turn_id=None)
    obs['facts'] = {'event_id': 'neow', 'event_phase': 'reward_options'}
    obs['ui'].update(menu_family='event_options', control_layout='ps5_default', navigation=[])
    obs['ui']['options'] = [
        {'id': 'upgrade', 'label': 'Upgrade a Card', 'enabled': True, 'costs': {}},
        {'id': 'potions', 'label': 'Obtain 3 random Potions', 'enabled': True, 'costs': {}},
        {'id': 'colorless', 'label': 'Lose 8 Max HP: rare colorless card', 'enabled': True, 'costs': {'max_hp': 8}},
    ]
    obs['resources'].update(max_hp=80)
    obs['ui'].update(order=[o['id'] for o in obs['ui']['options']], focused_id='upgrade')
    return obs


def grid():
    obs = event()
    obs['ui'].update(screen='selection', menu_family='card_upgrade', selection_purpose='upgrade',
        selection_mode='toggle', options=[
            {'id': 'strike-1', 'label': 'Strike', 'card': {'name': 'Strike', 'upgrade_name': 'Strike+'}, 'enabled': True, 'costs': {}},
            {'id': 'strike-2', 'label': 'Strike', 'card': {'name': 'Strike', 'upgrade_name': 'Strike+'}, 'enabled': True, 'costs': {}},
            {'id': 'defend-1', 'label': 'Defend', 'card': {'name': 'Defend', 'upgrade_name': 'Defend+'}, 'enabled': True, 'costs': {}},
            {'id': 'bash-1', 'label': 'Bash', 'card': {'name': 'Bash', 'upgrade_name': 'Bash+'}, 'enabled': True, 'costs': {}},
        ], order=['strike-1', 'strike-2', 'defend-1', 'bash-1'], focused_id='strike-1',
        grid={'complete': True, 'cells': [
            {'id': 'strike-1', 'row': 0, 'column': 0}, {'id': 'strike-2', 'row': 0, 'column': 1},
            {'id': 'defend-1', 'row': 1, 'column': 0}, {'id': 'bash-1', 'row': 1, 'column': 1}]})
    return obs


def bound(obs, number=0):
    return bind_reviewed_menu_controls(obs, control_profile=CONTROL_PROFILE,
        now=NOW + timedelta(seconds=number), max_age_seconds=30)


def goal(obs, target='bash-1'):
    result = fixtures.choice(obs, [target], kind='selection')
    target_card = next(o for o in obs['ui']['options'] if o['id'] == target)
    result['postconditions'].update(screen='event', phase='result', inventory_digest='d'*64,
        facts={'upgraded_card_id': target, 'upgraded_card_name': target_card['card']['upgrade_name']})
    return result


def clear_controls(obs):
    for option in obs['ui']['options']:
        option.pop('activate', None)
    obs['ui']['navigation'] = []
    return obs


def preview(before, number=2, button='triangle', subset=True):
    after = clear_controls(fixtures.later(before, number))
    selected = before['ui']['focused_id']
    if subset:
        after['ui']['options'] = [o for o in after['ui']['options'] if o['id'] == selected]
        after['ui']['order'] = [selected]
        after['ui'].pop('grid')
    card = next(o['card'] for o in after['ui']['options'] if o['id'] == selected)
    after['ui'].update(phase='confirm', selected_ids=[selected], pending_ids=[selected],
        upgrade_preview={'option_id': selected, 'before_name': card['name'], 'after_name': card['upgrade_name'],
                         'observed_upgrade_text': 'Synthetic preview: upgraded effect is visible.'},
        confirm={'button': button, 'evidence': {'kind': 'visible_hint', 'reviewer': 'synthetic fixture only',
            'meaning': 'confirm:' + before['ui']['choice_id'], 'layout_id': before['ui']['layout_id'],
            'frame_id': after['frame']['frame_id'], 'image_sha256': after['frame']['image_sha256'],
            'hint_text': button.title() + ' Confirm'}})
    return after


class MenuControlTests(unittest.TestCase):
    def test_neow_reward_focused_cross_is_a_rule_not_fake_observation(self):
        raw = event(); original = deepcopy(raw); obs = bound(raw)
        command = plan_choice_step(obs, fixtures.choice(obs, ['upgrade'], kind='event'), now=NOW)
        self.assertEqual(['cross'], command['command']['buttons'])
        self.assertEqual('commit', command['step_kind'])
        self.assertEqual(raw, original)
        proof = obs['ui']['options'][0]['activate']['evidence']
        self.assertEqual(EVENT_RULE, proof['rule_id'])
        self.assertEqual('documented_control_profile', proof['kind'])
        self.assertFalse(set(proof) & {'hint_text', 'reference_id', 'before_sha256', 'after_sha256'})
        self.assertFalse(uses_neow_control_rule(obs))
        self.assertEqual(EVENT_RULE, obs['ui']['options'][1]['activate']['evidence']['rule_id'])

    def test_event_opening_verifies_unchanged_inventory_then_new_grid(self):
        before = bound(event())
        choice = fixtures.choice(before, ['upgrade'], kind='event')
        choice['postconditions'].update(screen='selection', phase='choose', facts={})
        step = plan_choice_step(before, choice, now=NOW)
        after = fixtures.later(grid())
        after['review']['outcome'] = {'action_id': step['action_id'],
            'before_frame_id': before['frame']['frame_id'], 'before_sha256': before['frame']['image_sha256'],
            'choice_id': choice['choice_id'], 'option_ids': ['upgrade'], 'observed_result': 'Upgrade grid opened.'}
        self.assertTrue(verify_choice_step(step, before, after, now=NOW+timedelta(seconds=1))['choice_complete'])
        after['inventory_digest'] = 'e'*64
        with self.assertRaisesRegex(ChoiceError, 'inventory'):
            verify_choice_step(step, before, after, now=NOW+timedelta(seconds=1))

    def test_exact_paid_event_debit_and_unlisted_reward_still_rejected(self):
        obs = event(); obs['ui']['focused_id'] = 'colorless'; obs = bound(obs)
        choice = fixtures.choice(obs, ['colorless'], kind='event')
        with self.assertRaisesRegex(ChoiceError, 'exact reviewed debit'):
            plan_choice_step(obs, choice, now=NOW)
        choice['postconditions']['resources']['max_hp'] = 72
        step = plan_choice_step(obs, choice, now=NOW)
        after = fixtures.outcome(obs, step); after['ui']['screen'] = 'shop'
        with self.assertRaisesRegex(ChoiceError, 'unexpected committed'):
            verify_choice_step(step, obs, after, now=NOW+timedelta(seconds=1))

    def test_ineligible_screens_layouts_profiles_and_neow_opening_rejected(self):
        for key, value in [('screen', 'title'), ('screen', 'potion_menu'), ('screen', 'reward'),
                           ('control_layout', 'custom'), ('menu_family', 'unknown')]:
            obs = event(); obs['ui'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ChoiceError): bound(obs)
        obs = event(); obs['facts']['event_phase'] = 'opening_dialogue'
        with self.assertRaisesRegex(ChoiceError, 'narrower'): bound(obs)
        for phase in (None, 'unknown', 'dialogue'):
            obs = event()
            if phase is None:
                obs['facts'].pop('event_phase')
            else:
                obs['facts']['event_phase'] = phase
            with self.subTest(phase=phase), self.assertRaisesRegex(ChoiceError, 'reward_options'):
                bound(obs)
        with self.assertRaisesRegex(ChoiceError, 'default PS5'):
            bind_reviewed_menu_controls(event(), control_profile='custom', now=NOW)

    def test_stale_source_or_forged_profile_provenance_is_not_accepted(self):
        with self.assertRaisesRegex(ChoiceError, 'stale'):
            bind_reviewed_menu_controls(event(), control_profile=CONTROL_PROFILE, now=NOW+timedelta(seconds=31))
        # A pure binding may use epoch validation supplied by the reviewed adapter.
        bind_reviewed_menu_controls(event(), control_profile=CONTROL_PROFILE, now=NOW, max_age_seconds=None)
        for update in [{'frame_id': 'wrong'}, {'rule_id': 'universal-cross'}, {'hint_text': 'invented glyph'},
                       {'reference_id': 'invented hardware'}, {'control_profile': 'unknown'}]:
            obs = bound(event()); obs['ui']['options'][0]['activate']['evidence'].update(update)
            with self.subTest(update=update), self.assertRaises(ChoiceError):
                plan_choice_step(obs, fixtures.choice(obs, ['upgrade'], kind='event'), now=NOW)

    def test_event_nonfocused_option_does_not_gain_unsupported_navigation(self):
        obs = bound(event())
        with self.assertRaisesRegex(ChoiceError, 'no reviewed path'):
            plan_choice_step(obs, fixtures.choice(obs, ['potions'], kind='event'), now=NOW)

    def test_event_explicit_geometry_allows_one_fresh_navigation_then_activation(self):
        raw = event()
        raw['ui']['grid'] = {'complete': True, 'cells': [
            {'id': o['id'], 'row': i, 'column': 0} for i, o in enumerate(raw['ui']['options'])]}
        before = bound(raw)
        target = fixtures.choice(before, ['potions'], kind='event')
        step = plan_choice_step(before, target, now=NOW)
        self.assertEqual(['down'], step['command']['buttons'])
        self.assertEqual('focus', step['step_kind'])
        after = clear_controls(fixtures.later(before)); after['ui']['focused_id'] = 'potions'
        after = bound(after, 1)
        self.assertFalse(verify_choice_step(step, before, after, now=NOW+timedelta(seconds=1))['choice_complete'])
        step = plan_choice_step(after, fixtures.choice(after, ['potions'], kind='event'), now=NOW+timedelta(seconds=1))
        self.assertEqual(['cross'], step['command']['buttons'])
        self.assertEqual('commit', step['step_kind'])

    def test_malformed_order_focus_fails_as_choice_error(self):
        obs = event(); obs['ui']['order'] = ['unknown']; obs['ui']['focused_id'] = 'unknown'
        with self.assertRaises(ChoiceError): bound(obs)

    def test_duplicate_names_use_unique_ids_and_adjacent_one_tap_navigation(self):
        original = grid(); obs = bound(original); before = deepcopy(obs)
        step = plan_choice_step(obs, goal(obs), now=NOW)
        self.assertEqual(['down'], step['command']['buttons'])
        self.assertEqual({'focused_id': 'defend-1'}, step['expectation'])
        self.assertEqual(before, obs)
        self.assertEqual([], original['ui']['navigation'])
        edges = obs['ui']['navigation']
        self.assertFalse(any(e['from']=='strike-2' and e['to']=='defend-1' for e in edges))
        self.assertTrue(all(e['evidence']['rule_id']==GRID_FOCUS_RULE for e in edges))
        after = fixtures.later(obs); after['ui']['focused_id'] = 'defend-1'
        after = bound(clear_controls(after), 1)
        self.assertFalse(verify_choice_step(step, obs, after, now=NOW+timedelta(seconds=1))['choice_complete'])
        next_step = plan_choice_step(after, goal(after), now=NOW+timedelta(seconds=1))
        self.assertEqual(['right'], next_step['command']['buttons'])
        self.assertEqual({'focused_id': 'bash-1'}, next_step['expectation'])

    def test_grid_rejects_clipping_overlap_wrong_purpose_and_boolean_coordinates(self):
        changes = [lambda o: o['ui']['grid'].update(complete=False),
            lambda o: o['ui']['grid']['cells'][1].update(column=0),
            lambda o: o['ui']['grid']['cells'][0].update(row=True),
            lambda o: o['ui'].update(selection_purpose='remove'),
            lambda o: o['ui']['grid']['cells'].pop(),
            lambda o: o['ui']['grid']['cells'][0].update(id='hidden')]
        for change in changes:
            obs = grid(); change(obs)
            with self.subTest(change=change), self.assertRaises(ChoiceError): bound(obs)

    def test_counterfeit_grid_wrap_named_rule_is_rejected(self):
        obs = bound(grid())
        edge = deepcopy(obs['ui']['navigation'][0]); edge.update(**{'from':'strike-2','to':'defend-1','button':'right'})
        edge['evidence']['meaning'] = 'focus:strike-2->defend-1'
        obs['ui']['navigation'].append(edge)
        with self.assertRaisesRegex(ChoiceError, 'no wrap'):
            plan_choice_step(obs, goal(obs), now=NOW)

    def test_select_preview_then_fresh_triangle_confirm_are_separate_actions(self):
        raw = grid(); raw['ui']['focused_id'] = 'bash-1'; obs = bound(raw)
        step = plan_choice_step(obs, goal(obs), now=NOW)
        self.assertEqual('select', step['step_kind']); self.assertEqual(['cross'], step['command']['buttons'])
        after = preview(obs)
        result = verify_choice_step(step, obs, after, now=NOW+timedelta(seconds=1))
        self.assertFalse(result['choice_complete'])
        after = bound(after, 1)
        commit = plan_choice_step(after, goal(after), now=NOW+timedelta(seconds=1))
        self.assertEqual('commit', commit['step_kind']); self.assertEqual(['triangle'], commit['command']['buttons'])
        final = fixtures.outcome(after, commit, 3)
        self.assertTrue(verify_choice_step(commit, after, final, now=NOW+timedelta(seconds=2))['choice_complete'])

    def test_preview_may_show_unchanged_full_grid_and_actual_cross_hint(self):
        obs = bound(grid()); after = preview(obs, button='cross', subset=False)
        step = plan_choice_step(obs, goal(obs, 'strike-1'), now=NOW)
        self.assertTrue(verify_choice_step(step, obs, after, now=NOW+timedelta(seconds=1))['step_verified'])
        commit = plan_choice_step(bound(after, 1), goal(after, 'strike-1'), now=NOW+timedelta(seconds=1))
        self.assertEqual(['cross'], commit['command']['buttons'])

    def test_preview_rejects_wrong_card_missing_or_invented_hint_and_early_inventory_change(self):
        obs = bound(grid()); step = plan_choice_step(obs, goal(obs, 'strike-1'), now=NOW)
        changes = [lambda a: a['ui']['upgrade_preview'].update(option_id='strike-2'),
            lambda a: a['ui']['upgrade_preview'].update(after_name='Bash+'),
            lambda a: a['ui']['upgrade_preview'].update(observed_upgrade_text=''),
            lambda a: a['ui'].pop('confirm'),
            lambda a: a['ui']['confirm']['evidence'].update(kind='documented_control_profile'),
            lambda a: a['ui']['confirm']['evidence'].update(frame_id='stale'),
            lambda a: a['resources'].update(hp=34),
            lambda a: a.update(inventory_digest='d'*64),
            lambda a: a['ui'].update(unrelated='invented')]
        for change in changes:
            after = preview(obs); change(after)
            with self.subTest(change=change), self.assertRaises(ChoiceError):
                verify_choice_step(step, obs, after, now=NOW+timedelta(seconds=1))

    def test_upgrade_contract_requires_exact_target_inventory_and_unchanged_resources(self):
        obs = bound(grid())
        for update in [{'inventory_digest':'unchanged'}, {'inventory_digest':obs['inventory_digest']},
                       {'resources':{'hp':34,'gold':100,'max_hp':80}}, {'facts':{}},
                       {'facts':{'upgraded_card_id':'strike-1','upgraded_card_name':'Strike+'}}]:
            choice = goal(obs); choice['postconditions'].update(update)
            with self.subTest(update=update), self.assertRaises(ChoiceError):
                plan_choice_step(obs, choice, now=NOW)

    def test_final_result_must_match_chosen_upgrade_not_another_card(self):
        obs = bound(grid()); after = preview(obs)
        commit = plan_choice_step(after, goal(after, 'strike-1'), now=NOW+timedelta(seconds=1))
        final = fixtures.outcome(after, commit, 3); final['facts']['upgraded_card_id'] = 'strike-2'
        with self.assertRaisesRegex(ChoiceError, 'outcome fact differs'):
            verify_choice_step(commit, after, final, now=NOW+timedelta(seconds=2))


class ReviewedMenuFlowTests(unittest.TestCase):
    """Real temporary SQLite, fake taps and synthetic reviewed PNGs only."""
    def test_open_inspect_navigate_preview_confirm_and_record_exact_upgrade(self):
        from tests import test_reviewed_play as runtime_fixture
        from veda.reviewed_play import inventory_digest, RuntimeStop
        from uuid import uuid4

        f = runtime_fixture.ReviewedPlayTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.db.complete_combat_turn(turn_id=f.context_ids['turn_id'], closing_state={})
        f.db.complete_combat(combat_id=f.context_ids['combat_id'], outcome='victory', closing_state={})
        f.context_ids.update(combat_id=None, turn_id=None)
        inventory = deepcopy(f.choice_request(0)['inventory'])

        def request(template, number, *, inventory_override=None, target=None):
            f.now = f.base_time + timedelta(seconds=number)
            req = f.choice_request(number)
            obs = deepcopy(template)
            obs.update(frame=deepcopy(req['observation']['frame']), context=deepcopy(f.context_ids),
                       review=deepcopy(req['review']))
            supplied_inventory = deepcopy(inventory if inventory_override is None else inventory_override)
            obs['inventory_digest'] = inventory_digest(supplied_inventory)
            # Rebind only declared profile controls to the actually reviewed new
            # source. The preview's visible hint is separately read in its frame.
            if obs['ui']['phase'] == 'choose':
                obs = clear_controls(obs)
            if obs['ui'].get('confirm'):
                obs['ui']['confirm']['evidence'].update(frame_id=obs['frame']['frame_id'],
                    image_sha256=obs['frame']['image_sha256'])
            if obs['ui']['phase'] in {'choose', 'confirm'}:
                obs = bind_reviewed_menu_controls(obs, control_profile=CONTROL_PROFILE, now=f.now)
            req.update(observation=obs, review=deepcopy(obs['review']), inventory=supplied_inventory)
            if target:
                req['choice'] = goal(obs, target)
                upgraded_inventory = deepcopy(supplied_inventory)
                upgraded_inventory['current']['card'].remove('Bash')
                upgraded_inventory['current']['card'].append('Bash+')
                req['choice']['postconditions']['inventory_digest'] = inventory_digest(upgraded_inventory)
            else:
                req.pop('choice', None)
            return req

        def prepare_send(req):
            prepared = f.session.handle(req)
            before_count = len(f.controller.inputs)
            f.session.handle({'operation':'send','action_id':prepared['action_id']})
            self.assertEqual(before_count+1, len(f.controller.inputs))
            self.assertEqual(1, len(f.controller.inputs[-1]['buttons']))
            # No second prepare or repeat input can bypass the durable pending.
            with self.assertRaises(RuntimeStop): f.session.handle(req)
            with self.assertRaises(RuntimeStop):
                f.session.handle({'operation':'send','action_id':prepared['action_id']})
            self.assertEqual(before_count+1, len(f.controller.inputs))
            return prepared

        def verify(prepared, after, changes=None):
            before = f.session.state['pending']['request']
            if f.session.state['pending']['proposal']['step_kind'] == 'commit':
                after['observation']['review']['outcome'] = {
                    'action_id': prepared['action_id'], 'before_frame_id': before['observation']['frame']['frame_id'],
                    'before_sha256': before['source']['sha256'], 'choice_id': before['choice']['choice_id'],
                    'option_ids': before['choice']['option_ids'], 'observed_result': 'Synthetic reviewed actual result.'}
                after['review'] = deepcopy(after['observation']['review'])
            if changes:
                after['mutation_review'] = {**after['review'], 'changes': changes}
            result = f.session.handle({'operation':'verify','operation_id':str(uuid4()),
                'action_id':prepared['action_id'],'after':after,'telemetry':changes or {}})
            self.assertEqual('verified', result['status'])
            self.assertIsNone(f.session.state['pending'])
            # Deliberate failed replay checks above disarm; freshly re-arm the
            # same run from the new saved frame before the next independent tap.
            return result

        def rearm(req):
            f.session.handle({'operation':'arm', 'phrase':runtime_fixture.ARM_PHRASE,
                'run_id':f.context_ids['run_id'], 'source':req['source'], 'review':req['review'],
                'frame_id':req['observation']['frame']['frame_id'], 'game':'Slay the Spire',
                'screen':req['observation']['ui']['screen'], 'exclusive_client_confirmed':True})

        opened = request(event(), 0)
        opened['choice'] = fixtures.choice(opened['observation'], ['upgrade'], kind='event')
        opened['choice']['postconditions'].update(screen='selection', phase='choose', facts={})
        f.before=opened; f.create(); rearm(opened)
        prepared=prepare_send(opened)
        grid_result=request(grid(),1)
        verify(prepared,grid_result)
        self.assertEqual(['cross'],f.controller.inputs[-1]['buttons'])
        self.assertEqual('unknown',f.db.inventory_ledger(run_id=f.context_ids['run_id'])['coverage']['card'])

        # Opening did not add cards. Only the separate complete grid inspection
        # establishes a source-backed baseline before choosing the upgrade.
        cards=['Strike','Strike','Defend','Bash']
        baseline=f.db.record_inventory_baseline(run_id=f.context_ids['run_id'], floor_id=f.context_ids['floor_id'],
            items=[{'kind':'card','item':c} for c in cards],
            coverage={'card':'complete','relic':'complete','potion':'complete'},
            source='Synthetic complete grid inspection after verified opening.',
            screenshot_path=grid_result['source']['path'])
        with f.db._connection() as db:
            db.execute('UPDATE inventory_baselines SET observed_at=? WHERE id=?',
                       (f.now.isoformat(),baseline))
        inventory=f.db.inventory_ledger(run_id=f.context_ids['run_id'])

        first=request(grid(),2,target='bash-1'); rearm(first)
        prepared=prepare_send(first)
        next_grid=grid(); next_grid['ui']['focused_id']='defend-1'
        focused=request(next_grid,3); verify(prepared,focused)
        second=request(next_grid,4,target='bash-1'); rearm(second)
        prepared=prepare_send(second)
        next_grid['ui']['focused_id']='bash-1'
        focused=request(next_grid,5); verify(prepared,focused)
        select=request(next_grid,6,target='bash-1'); rearm(select)
        prepared=prepare_send(select)
        selected=request(preview(next_grid),7)
        verify(prepared,selected)
        self.assertCountEqual(cards,f.db.inventory_ledger(run_id=f.context_ids['run_id'])['current']['card'])

        confirm=request(preview(next_grid),8,target='bash-1'); rearm(confirm)
        prepared=prepare_send(confirm)
        expected=deepcopy(inventory); expected['current']['card'].remove('Bash'); expected['current']['card'].append('Bash+')
        final_template=fixtures.outcome(confirm['observation'], f.session.state['pending']['proposal'])
        final=request(final_template,9,inventory_override=expected)
        changes={'inventory_events':[{'kind':'card','action':'replaced','item':'Bash','related_item':'Bash+',
            'evidence_note':'Selected Bash+ result and deck were independently reviewed in synthetic fixture.'}]}
        verify(prepared,final,changes)
        self.assertEqual([['cross'],['down'],['right'],['cross'],['triangle']],
                         [c['buttons'] for c in f.controller.inputs])
        self.assertCountEqual(['Strike','Strike','Defend','Bash+'],
                              f.db.inventory_ledger(run_id=f.context_ids['run_id'])['current']['card'])
        with f.db._connection() as db:
            rows=db.execute('SELECT item_name,related_item_name FROM inventory_events').fetchall()
            self.assertEqual([('Bash','Bash+')],[tuple(row) for row in rows])
