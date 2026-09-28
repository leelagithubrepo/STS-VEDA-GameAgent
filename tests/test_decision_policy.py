"""One-action policy contracts; no game, controller or live database access."""
from copy import deepcopy
from dataclasses import replace
import unittest

from tests.test_execution import context, reading
from veda.advisory import check_plan
from veda.decision_policy import assess_combat, assess_observation, plan_shape_reasons
from veda.execution import Reading


class DecisionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.context = context()
        self.plan = {'steps': [{'kind': 'card', 'card_id': 'd'}]}

    def test_strict_retains_exact_existing_check(self):
        expected = check_plan(self.context, self.plan, survival_scope='action_prefix')
        actual = assess_combat(self.context, self.plan)
        self.assertEqual(expected, actual['checked'])
        for key in expected:
            self.assertEqual(expected[key], actual[key])
        self.assertEqual([], actual['warnings'])

    def test_unsupported_enemy_effect_and_missing_potion_review_are_advice(self):
        self.context['state']['unmodeled_effects'] = ['Unknown displayed debuff.']
        self.context['inventory']['current']['potion'] = ['Energy Potion']
        plan = {'steps': [{'kind': 'end_turn'}]}
        self.assertFalse(assess_combat(self.context, plan)['allowed'])
        prior = deepcopy(self.context)
        actual = assess_combat(self.context, plan, policy='learning')
        self.assertTrue(actual['allowed']); self.assertTrue(actual['decision_under_uncertainty'])
        self.assertIsNone(actual['forecast']); self.assertEqual('unknown', actual['forecast_status'])
        self.assertTrue(any('Energy Potion' in reason for reason in actual['warnings']))
        self.assertEqual(prior, self.context)
        self.assertFalse(actual['checked']['allowed'])

    def test_unknown_enemy_intent_and_stats_remain_null(self):
        state = self.context['state']
        state['enemies'][0].update(intent=None, intent_hits=None, intent_effects=None, hp=None, block=None)
        state.update(strength=None, block=None, powers_complete=False, hand_complete=False)
        self.context['unknowns'] = ['Intent and some effects are unread.']
        self.context['inventory']['coverage']['relic'] = 'partial'
        expected = deepcopy(self.context)
        actual = assess_combat(self.context, self.plan, policy='learning')
        self.assertTrue(actual['allowed']); self.assertIsNone(actual['forecast'])
        self.assertEqual(expected, self.context)
        self.assertFalse(assess_combat(self.context, self.plan)['allowed'])

    def test_unsupported_card_can_be_attempted_without_invented_checked_effect(self):
        self.context['state']['hand'][1]['name'] = 'Uncatalogued Skill'
        actual = assess_combat(self.context, self.plan, policy='learning')
        self.assertTrue(actual['allowed']); self.assertEqual([], actual['steps'])
        self.assertIsNone(actual['forecast']); self.assertEqual([], actual['checked']['steps'])
        self.assertTrue(any('no reviewed immediate-effect' in r for r in actual['warnings']))

    def test_forecast_death_is_a_warning_but_actual_death_is_not(self):
        self.context['state']['hp'] = 1
        plan = {'steps': [{'kind': 'end_turn'}]}
        assessment = assess_combat(self.context, plan, policy='learning')
        self.assertTrue(assessment['allowed'])
        self.assertEqual('known', assessment['forecast_status'])
        self.assertLess(assessment['forecast']['player_hp'], 1)
        self.assertFalse(assessment['forecast']['survival_established'])
        self.context['state']['hp'] = 0
        self.assertFalse(assess_combat(self.context, plan, policy='learning')['allowed'])

    def test_missing_zero_cost_review_warns_and_explicit_review_is_preserved(self):
        self.context['state']['hand'][0]['cost'] = 0
        plan = {'steps': [{'kind': 'end_turn'}]}
        actual = assess_combat(self.context, plan, policy='learning')
        self.assertTrue(actual['allowed'])
        self.assertTrue(any('zero-cost' in r for r in actual['warnings']))
        plan['zero_cost_review'] = {'s': 'Preserve this card for a later turn.'}
        self.assertTrue(assess_combat(self.context, plan)['allowed'])

    def test_known_illegal_or_malformed_actions_are_rejected_under_learning(self):
        edits = [lambda c, p:p['steps'][0].update(card_id='absent'),
                 lambda c, p:c['state']['hand'][1].update(playable=False),
                 lambda c, p:c['state'].update(energy=0),
                 lambda c, p:c.update(fresh=False),
                 lambda c, p:c['state']['hand'].append(deepcopy(c['state']['hand'][0])),
                 lambda c, p:c['state'].update(hp=-1),
                 lambda c, p:c['state']['hand'][1].update(cost='one'),
                 lambda c, p:c['state']['hand'][1].update(playable='yes'),
                 lambda c, p:p['steps'].append({'kind':'end_turn'}),
                 lambda c, p:p['steps'][0].update(buttons=['cross']),
                 lambda c, p:p.update(potion_review={'Energy Potion': 1})]
        for edit in edits:
            c = deepcopy(self.context); p = deepcopy(self.plan); edit(c, p)
            with self.subTest(edit=edit):
                self.assertFalse(assess_combat(c, p, policy='learning')['allowed'])

    def test_unknown_rule_does_not_hide_known_cost_or_unplayable(self):
        card = self.context['state']['hand'][1]
        card.update(name='Uncatalogued Skill', cost=2)
        actual = assess_combat(self.context, self.plan, policy='learning')
        self.assertFalse(actual['allowed']); self.assertTrue(any('observed energy' in r for r in actual['hard_reasons']))
        card.update(cost=1, playable=False)
        self.assertFalse(assess_combat(self.context, self.plan, policy='learning')['allowed'])

    def test_attack_needs_actual_target_and_known_choker_still_applies(self):
        plan = {'steps': [{'kind':'card', 'card_id':'s', 'target':'missing'}]}
        self.assertFalse(assess_combat(self.context, plan, policy='learning')['allowed'])
        plan['steps'][0]['target'] = 'enemy'
        self.context['state']['enemies'][0]['hp'] = 0
        self.assertFalse(assess_combat(self.context, plan, policy='learning')['allowed'])
        self.context['state']['enemies'][0]['hp'] = None
        self.assertTrue(assess_combat(self.context, plan, policy='learning')['allowed'])
        self.context['inventory']['current']['relic'] = ['Velvet Choker']
        self.context['state']['counters']['velvet_choker'] = 6
        self.assertFalse(assess_combat(self.context, plan, policy='learning')['allowed'])

    def test_observation_confidence_and_completeness_are_advice_not_conflicting_facts(self):
        visual = Reading.from_dict(dict(reading(self.context, {}), frame_id='test', image_sha256='0'*64)).state
        low = replace(visual, confidence=.2, hand_complete=False)
        self.assertTrue(assess_observation(low, self.context, 'learning')['allowed'])
        self.assertFalse(assess_observation(low, self.context)['allowed'])
        for changed in (replace(low, hp=1), replace(low, screen_type='MAP'),
                        replace(low, hand=('Unexpected card',)),
                        replace(low, enemies=(replace(low.enemies[0], intent_hits=(99,)),))):
            with self.subTest(changed=changed):
                self.assertFalse(assess_observation(changed, self.context, 'learning')['allowed'])

    def test_optional_advice_fields_are_bounded_and_never_controller_instructions(self):
        good = dict(self.plan, potion_review={'Energy Potion':'Save it.'}, zero_cost_review={'s':'No useful target.'},
                    setup_reason='Set up next turn.', claims_lethal=False)
        self.assertEqual([], plan_shape_reasons(good))
        for extra in ({'survival_scope':'anything'}, {'claims_lethal':1}, {'setup_reason':'x'*4097},
                      {'potion_review':{'Energy Potion':''}}, {'armed':True}):
            self.assertTrue(plan_shape_reasons(dict(self.plan, **extra)))


if __name__ == '__main__':
    unittest.main()
