"""Regression coverage for the observed September 24 deck and Slime fight."""
import copy
from datetime import datetime, timezone
import unittest

from veda.advisory import SPIKE_SLIME_SOURCE, check_plan, reviewed_card_type


def card(name, kind='Attack', cost=1, ident='c1', **extra):
    return dict(id=ident, name=name, type=kind, cost=cost,
                upgraded=name.endswith('+'),
                title_color='green' if name.endswith('+') else 'white',
                playable=True, **extra)


def context(hand=None):
    return {
        'fresh': True, 'unknowns': [], 'encounter_type': 'enemy',
        'inventory': {'coverage': {'relic': 'complete', 'potion': 'complete'},
                      'current': {'relic': [], 'potion': []}},
        'state': {
            'schema': 'spire.advisory.v1',
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'hp': 80, 'max_hp': 80, 'energy': 3, 'block': 10,
            'strength': 0, 'dexterity': 0, 'weak': 0, 'vulnerable': 0,
            'frail': 0, 'no_block': 0, 'powers': {}, 'powers_complete': True,
            'hand_complete': True, 'hand': hand or [], 'counters': {},
            'piles': {'draw': None, 'discard': [], 'exhaust': [], 'draw_order': None},
            'enemies': [{'id': 'enemy', 'name': 'Cultist', 'hp': 40, 'max_hp': 40,
                         'block': 0, 'vulnerable': 0, 'strength': 0, 'weak': 0,
                         'intent': 'attack 6', 'intent_hits': [6]}],
            'end_turn_damage': 0, 'unmodeled_effects': [],
        },
    }


def play(ident='c1', target='enemy'):
    return {'kind': 'card', 'card_id': ident, 'target': target}


def spike_context(hand=None):
    c = context(hand)
    c['state']['ascension'] = 2
    c['state']['enemies'] = [{
        'id': 'enemy', 'name': 'Spike Slime (L)', 'hp': 64, 'max_hp': 64,
        'block': 0, 'vulnerable': 0, 'strength': 0, 'weak': 0,
        'intent': 'Attack 18 and a negative effect', 'intent_hits': [18],
        'move': 'Flame Tackle',
        'split': {'threshold_percent': 50, 'smaller_slimes': 2, 'hp': 'current HP'},
        'intent_effects': [{'kind': 'generate_status', 'card': 'Slimed',
                            'count': 2, 'to_zone': 'discard'}],
        'evidence': {'source': SPIKE_SLIME_SOURCE, 'kind': 'reviewed_reference',
                     'observed_intent': True},
    }]
    return c


class CurrentDeckTests(unittest.TestCase):
    def test_anger_at_zero_energy_requires_generation_observation(self):
        c = context([card('Anger', cost=0)])
        c['state']['energy'] = 0
        before = copy.deepcopy(c)
        result = check_plan(c, {'steps': [play()]})
        self.assertTrue(result['allowed'], result['reasons'])
        self.assertEqual(result['steps'][0]['energy_after'], 0)
        self.assertTrue(result['steps'][0]['observe_after'])
        self.assertEqual(result['steps'][0]['reviewed_effect']['base_damage'], 6)
        self.assertEqual(result['steps'][0]['reviewed_effect']['generate'],
                         {'card': 'Anger', 'count': 1, 'zone': 'discard'})
        self.assertIsNone(result['forecast'])
        self.assertEqual(c, before, 'checking advice cannot fabricate a generated card')
        self.assertFalse(check_plan(c, {'steps': [play(), {'kind': 'end_turn'}]})['allowed'])
        self.assertFalse(check_plan(c, {'steps': [play()], 'claims_lethal': True})['allowed'])

    def test_anger_keeps_zero_cost_review_target_and_type_checks(self):
        c = context([card('Anger', cost=0)])
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])
        self.assertTrue(check_plan(c, {'steps': [{'kind': 'end_turn'}],
            'zero_cost_review': {'c1': 'Preserve the confirmed card-count limit'}})['allowed'])
        self.assertFalse(check_plan(c, {'steps': [play(target='absent')]})['allowed'])
        c['state']['hand'][0]['type'] = 'Skill'
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])

    def test_hemokinesis_hp_loss_precedes_any_kill_and_ignores_block(self):
        c = context([card('Hemokinesis+')])
        c['state']['block'] = 999
        c['state']['enemies'][0]['hp'] = 1
        for hp in (1, 2):
            with self.subTest(hp=hp):
                c['state']['hp'] = hp
                result = check_plan(c, {'steps': [play()]})
                self.assertFalse(result['allowed'])
                self.assertTrue(any('HP cost is not survivable' in r for r in result['reasons']))
        c['state']['hp'] = 3
        result = check_plan(c, {'steps': [play()]})
        self.assertTrue(result['allowed'], result['reasons'])
        self.assertEqual(result['steps'][0]['energy_after'], 2)
        self.assertTrue(result['steps'][0]['observe_after'])
        self.assertEqual(result['steps'][0]['reviewed_effect']['hp_loss'], 2)
        self.assertEqual(result['steps'][0]['reviewed_effect']['base_damage'], 20)
        self.assertIsNone(result['forecast'])
        c['state']['energy'] = 0
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])

    def test_whole_confirmed_deck_is_covered_without_unread_variants(self):
        deck = ['Strike'] * 5 + ['Defend'] * 4 + [
            'Bash+', 'Shrug It Off', 'Hemokinesis+', 'Headbutt', 'Anger']
        self.assertEqual(len(deck), 14)
        self.assertFalse([name for name in deck if reviewed_card_type(name) is None])
        self.assertEqual(reviewed_card_type('Hemokinesis+'), 'Attack')
        self.assertEqual(reviewed_card_type('Anger'), 'Attack')
        self.assertIsNone(reviewed_card_type('Anger+'))
        self.assertEqual(reviewed_card_type('Hemokinesis'), 'Attack')

    def test_observed_base_hemokinesis_is_checked_before_any_damage(self):
        c = context([card('Hemokinesis')])
        c['state']['hp'] = 2
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])
        c['state']['hp'] = 3
        result = check_plan(c, {'steps': [play()]})
        self.assertTrue(result['allowed'])
        self.assertEqual(result['steps'][0]['reviewed_effect']['base_damage'], 15)
        self.assertEqual(result['steps'][0]['reviewed_effect']['hp_loss'], 2)
        self.assertIsNone(result['forecast'])

    def test_shared_current_deck_metadata_keeps_draw_and_debuff_boundaries(self):
        for name, kind, expected in [('Bash+', 'Attack', {'base_damage': 10, 'vulnerable': 3}),
                                      ('Shrug It Off', 'Skill', {'base_block': 8, 'draw': 1}),
                                      ('Shrug It Off+', 'Skill', {'base_block': 11, 'draw': 1})]:
            with self.subTest(card=name):
                c = context([card(name, kind)])
                result = check_plan(c, {'steps': [play()]})
                self.assertTrue(result['allowed'])
                for key, value in expected.items():
                    self.assertEqual(result['steps'][0]['reviewed_effect'][key], value)
                self.assertTrue(result['steps'][0]['observe_after'])
                self.assertIsNone(result['forecast'])
                self.assertFalse(check_plan(c, {'steps': [play(), {'kind': 'end_turn'}]})['allowed'])

    def test_unknown_enemy_effect_still_blocks_end_turn(self):
        c = context()
        c['state']['unmodeled_effects'] = ['Enemy negative effect is unread']
        result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['forecast'])

    def test_slimed_is_a_paid_status_exhaust_without_draw(self):
        c = context([card('Slimed', 'Status')])
        c['state']['powers']['Corruption'] = True
        before = copy.deepcopy(c)
        result = check_plan(c, {'steps': [play()]})
        self.assertTrue(result['allowed'], result['reasons'])
        self.assertEqual(result['steps'][0]['energy_after'], 2)
        self.assertEqual(result['steps'][0]['reviewed_effect'], {'type': 'Status', 'exhaust': True})
        self.assertTrue(result['steps'][0]['observe_after'])
        self.assertIsNone(result['forecast'])
        self.assertEqual(c, before)
        self.assertFalse(check_plan(c, {'steps': [play(), {'kind': 'end_turn'}]})['allowed'])
        c['state']['energy'] = 0
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])
        self.assertTrue(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])
        c['state']['hand'][0]['type'] = 'Skill'
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])


class SpikeSlimeTests(unittest.TestCase):
    def test_split_threshold_33_vs_32_and_31_requires_new_intent(self):
        for remaining in (33, 32, 31):
            with self.subTest(remaining=remaining):
                c = spike_context([card('Strike')])
                c['state']['enemies'][0]['hp'] = remaining + 6
                before = copy.deepcopy(c)
                result = check_plan(c, {'steps': [play()]})
                self.assertTrue(result['allowed'], result['reasons'])
                self.assertEqual(c, before)
                if remaining == 33:
                    self.assertFalse(result['steps'][0]['observe_after'])
                    self.assertEqual(result['forecast']['enemies'][0]['hp'], 33)
                else:
                    self.assertTrue(result['steps'][0]['observe_after'])
                    self.assertIn('Split', result['steps'][0]['observation_reason'])
                    self.assertIsNone(result['forecast'], 'old attack intent cannot survive the threshold')
                    self.assertFalse(check_plan(c, {'steps': [play(), {'kind': 'end_turn'}]})['allowed'])

    def test_fresh_split_intent_bounds_survival_without_inventing_children(self):
        c = spike_context()
        enemy = c['state']['enemies'][0]
        enemy.update(hp=31, move='Split', intent='Split', intent_hits=[], intent_effects=[])
        before = copy.deepcopy(c)
        result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertTrue(result['allowed'], result['reasons'])
        self.assertEqual(result['forecast']['incoming_displayed'], 0)
        self.assertEqual(result['forecast']['player_hp'], 80)
        self.assertEqual(len(result['forecast']['enemies']), 1)
        self.assertEqual(result['forecast']['enemies'][0]['id'], 'enemy')
        self.assertTrue(result['steps'][0]['observe_after'])
        self.assertTrue(any('Split children' in note for note in result['notes']))
        self.assertEqual(c, before)

    def test_split_below_threshold_with_stale_attack_is_not_certified(self):
        c = spike_context()
        c['state']['enemies'][0]['hp'] = 31
        result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['forecast'])

    def test_split_descriptor_needs_known_maximum_and_exact_reviewed_values(self):
        changes = [
            ('max_hp', None), ('max_hp', 0),
            ('split', {'threshold_percent': 49, 'smaller_slimes': 2, 'hp': 'current HP'}),
            ('split', {'threshold_percent': 50, 'smaller_slimes': 3, 'hp': 'current HP'}),
            ('split', {'threshold_percent': 50, 'smaller_slimes': 2, 'hp': 'half maximum HP'}),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                c = spike_context([card('Strike')])
                c['state']['enemies'][0][field] = value
                self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])

    def test_reviewed_flame_tackle_preserves_displayed_damage_and_pending_status(self):
        c = spike_context()
        before = copy.deepcopy(c)
        result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertTrue(result['allowed'], result['reasons'])
        self.assertEqual(result['forecast']['incoming_displayed'], 18)
        self.assertEqual(result['forecast']['player_hp'], 72)
        self.assertEqual(c, before, 'no Slimed cards may be logged before the enemy acts')
        c['state']['enemies'][0]['intent_hits'] = [21]
        result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertEqual(result['forecast']['incoming_displayed'], 21,
                         'the rule cannot overwrite observed modified damage')

    def test_reference_move_effect_and_ascension_must_match(self):
        corruptions = [
            lambda c: c['state'].update(ascension=3),
            lambda c: c['state']['enemies'][0].pop('evidence'),
            lambda c: c['state']['enemies'][0]['evidence'].update(source='unreviewed'),
            lambda c: c['state']['enemies'][0]['evidence'].update(observed_intent=False),
            lambda c: c['state']['enemies'][0].update(move='Lick'),
            lambda c: c['state']['enemies'][0].update(intent_effects=None),
            lambda c: c['state']['enemies'][0]['intent_effects'][0].update(count=1),
            lambda c: c['state']['enemies'][0]['intent_effects'][0].update(to_zone='draw'),
            lambda c: c['state']['enemies'][0]['intent_effects'][0].update(card='Burn'),
            lambda c: c['state'].update(unmodeled_effects=['Another unknown negative effect']),
        ]
        for index, corrupt in enumerate(corruptions):
            with self.subTest(index=index):
                c = spike_context()
                corrupt(c)
                result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
                self.assertFalse(result['allowed'])
                self.assertIsNone(result['forecast'])

    def test_two_medium_slimes_use_verified_effects_and_current_block(self):
        c = spike_context()
        attacking = c['state']['enemies'][0]
        attacking.update(name='Spike Slime (M)', hp=31, max_hp=31, intent_hits=[10])
        attacking.pop('split')
        attacking['intent_effects'][0]['count'] = 1
        licking = copy.deepcopy(attacking)
        licking.update(id='second', move='Lick', intent='Frail', intent_hits=[],
                       intent_effects=[{'kind': 'apply_debuff', 'debuff': 'frail', 'amount': 1}])
        c['state']['enemies'].append(licking)
        result = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertTrue(result['allowed'], result['reasons'])
        self.assertEqual(result['forecast']['incoming_displayed'], 10)
        self.assertEqual(result['forecast']['player_hp'], 80, 'future Frail does not remove existing Block')
        licking['intent_effects'][0]['amount'] = 2
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])


if __name__ == '__main__':
    unittest.main()
