"""Source-reviewed September 27 mechanics, not vision or live-play validation."""
from copy import deepcopy
import unittest

from veda.advisory import COLLECTOR_SOURCE, boss_manifest, check_plan, reviewed_card_type
from veda.routine_combat import plan_routine_combat, _boundary_rank
from tests.test_advisory_current_deck import card, context, play
from tests.test_routine_current_deck import context as routine_context, observation, report, plan


def collector(hand=None, move='Fireball'):
    c = context(hand)
    c['encounter_type'] = 'boss'
    c['boss_manifest'] = boss_manifest('The Collector', 2)
    c['state'].update(ascension=2, enemies_complete=True)
    c['state']['enemies'] = [dict(id='boss', name='The Collector', hp=282, max_hp=282,
        block=0, vulnerable=0, weak=0, artifact=0, strength=0, move=move,
        intent='attack 18' if move == 'Fireball' else move,
        intent_hits=[18] if move == 'Fireball' else [], intent_effects=[],
        evidence={'source': COLLECTOR_SOURCE, 'kind': 'reviewed_reference', 'observed_intent': True})]
    for i in range(2):
        c['state']['enemies'].append(dict(id=f'torch{i}', name='Torch Head', hp=40, max_hp=40,
            block=0, vulnerable=0, weak=0, artifact=0, strength=0, move='Tackle',
            intent='attack 7', intent_hits=[7], intent_effects=[],
            evidence={'source': COLLECTOR_SOURCE, 'kind': 'reviewed_reference', 'observed_intent': True}))
    return c


class CollectorCurrentRunTests(unittest.TestCase):
    def test_true_grit_is_random_boundary_without_zone_or_target_prediction(self):
        c = context([card('True Grit', 'Skill'), card('Strike', ident='s')])
        before = deepcopy(c)
        r = check_plan(c, {'steps': [play()]})
        self.assertTrue(r['allowed'], r['reasons'])
        self.assertEqual(r['steps'][0]['reviewed_effect'],
                         {'type': 'Skill', 'base_block': 7, 'random_exhaust': 1})
        self.assertTrue(r['steps'][0]['observe_after'])
        self.assertIsNone(r['forecast'])
        self.assertEqual(c, before)
        chosen = dict(play(), exhaust_card_id='s')
        self.assertFalse(check_plan(c, {'steps': [chosen]})['allowed'])
        self.assertFalse(check_plan(c, {'steps': [play(), play('s')]})['allowed'])
        self.assertFalse(check_plan(c, {'steps': [play()], 'claims_lethal': True})['allowed'])

    def test_true_grit_routine_rank_is_block_only_and_upgrade_not_enabled(self):
        c = routine_context([card('True Grit', 'Skill')])
        r = plan(c)
        self.assertTrue(r.ready, r.reasons)
        self.assertEqual(r.next_action, {'kind': 'card', 'card_id': 'c1'})
        self.assertIsNone(r.prediction)
        state = c['state']; state.update(hp=30, block=0, dexterity=1, frail=1)
        state['enemies'][0]['intent_hits'] = [10]
        rank = _boundary_rank(state, state['hand'][0], {'kind': 'card', 'card_id': 'a'})
        self.assertEqual(rank[1], -26)  # floor((7 + 1) * .75) == 6; no exhaust benefit.
        state['no_block'] = True
        self.assertEqual(_boundary_rank(state, state['hand'][0], play())[1], -20)
        c['state']['hand'][0] = card('True Grit+', 'Skill')
        self.assertFalse(plan(c).ready)

    def test_five_exact_variants_are_boundaries_without_context_mutation(self):
        for name, kind, key, value in [
            ('Power Through+', 'Skill', 'base_block', 20),
            ('Metallicize', 'Power', 'end_turn_block', 3),
            ('Uppercut', 'Attack', 'base_damage', 13),
            ('Warcry', 'Skill', 'draw', 1),
            ('Spot Weakness', 'Skill', 'strength_if_target_attacks', 3),
        ]:
            with self.subTest(name=name):
                c = context([card(name, kind)])
                before = deepcopy(c)
                r = check_plan(c, {'steps': [play()]})
                self.assertTrue(r['allowed'], r['reasons'])
                self.assertEqual(r['steps'][0]['reviewed_effect'][key], value)
                self.assertTrue(r['steps'][0]['observe_after'])
                self.assertIsNone(r['forecast'])
                self.assertEqual(c, before)
                self.assertFalse(check_plan(c, {'steps': [play(), {'kind': 'end_turn'}]})['allowed'])
                self.assertFalse(check_plan(c, {'steps': [play()], 'claims_lethal': True})['allowed'])

    def test_unreviewed_variants_stay_unsupported(self):
        for name in ('Power Through', 'Metallicize+', 'Uppercut+', 'Warcry+', 'Spot Weakness+'):
            self.assertIsNone(reviewed_card_type(name))

    def test_power_through_requires_hand_room_and_wounds_remain_unplayable(self):
        c = context([card('Power Through+', 'Skill')] + [card('Strike', ident=f's{i}') for i in range(9)])
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])
        c['state']['hand'].pop()
        self.assertTrue(check_plan(c, {'steps': [play()]})['allowed'])
        c['state']['hand'] = [dict(card('Wound', 'Status', None), playable=False, unplayable=True)]
        self.assertTrue(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])
        del c['state']['hand'][0]['unplayable']
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])

    def test_spot_weakness_requires_selected_attack_even_when_zero_damage(self):
        c = context([card('Spot Weakness', 'Skill')])
        self.assertFalse(check_plan(c, {'steps': [play(target='absent')]})['allowed'])
        c['state']['enemies'][0]['intent_hits'] = []
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])
        c['state']['enemies'][0]['intent_hits'] = [0]
        self.assertTrue(check_plan(c, {'steps': [play()]})['allowed'])

    def test_warcry_cannot_choose_from_pre_draw_hand(self):
        c = context([card('Warcry', 'Skill', 0), card('Strike', ident='s')])
        p = play(); p['return_card_id'] = 's'
        self.assertFalse(check_plan(c, {'steps': [p]})['allowed'])
        self.assertTrue(check_plan(c, {'steps': [play()]})['allowed'])

    def test_shuriken_requires_nonboolean_current_counter(self):
        c = context([card('Strike')]); c['inventory']['current']['relic'] = ['Shuriken']
        for count in (None, True, False, -1, 3, '2'):
            c['state']['counters']['shuriken'] = count
            self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])
        c['state']['counters'] = None
        self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])

    def test_third_attack_stops_before_using_unobserved_strength(self):
        c = context([card('Strike', ident=f's{i}') for i in range(4)])
        c['inventory']['current']['relic'] = ['Shuriken']
        c['state']['counters']['shuriken'] = 0
        two = check_plan(c, {'steps': [play('s0'), play('s1')]})
        self.assertTrue(two['allowed']); self.assertIsNotNone(two['forecast'])
        self.assertEqual(two['steps'][-1]['shuriken_after'], 2)
        three = check_plan(c, {'steps': [play('s0'), play('s1'), play('s2')]})
        self.assertTrue(three['allowed']); self.assertIsNone(three['forecast'])
        self.assertEqual(three['steps'][-1]['shuriken_strength_gain'], 1)
        self.assertEqual(three['steps'][-1]['shuriken_after'], 0)
        self.assertFalse(check_plan(c, {'steps': [play(f's{i}') for i in range(4)]})['allowed'])
        self.assertEqual(c['state']['strength'], 0)
        self.assertEqual(c['state']['counters']['shuriken'], 0)

    def test_skill_does_not_count_as_shuriken_attack(self):
        c = context([card('Defend+', 'Skill')]); c['inventory']['current']['relic'] = ['Shuriken']
        c['state']['counters']['shuriken'] = 2
        r = check_plan(c, {'steps': [play()]})
        self.assertEqual(r['steps'][0]['shuriken_after'], 2)
        self.assertEqual(r['steps'][0]['shuriken_strength_gain'], 0)

    def test_metallicize_is_end_turn_only_and_not_card_block(self):
        c = context([card('Body Slam+', cost=0)])
        c['state'].update(block=6, dexterity=9, frail=1, no_block=1, powers={'Metallicize': 3})
        c['state']['enemies'][0]['intent_hits'] = [11]
        r = check_plan(c, {'steps': [play()]})
        self.assertEqual(r['forecast']['block'], 6)
        self.assertEqual(r['forecast']['enemies'][0]['hp'], 34, 'future Block cannot increase Body Slam now')
        self.assertEqual(r['forecast']['end_turn_block'], 3)
        self.assertEqual(r['forecast']['player_hp'], 78)
        for intensity in (True, None, '3', -1):
            c['state']['powers']['Metallicize'] = intensity
            self.assertFalse(check_plan(c, {'steps': [play()]})['allowed'])

    def test_unknown_other_effect_still_blocks_metallicize_survival(self):
        c = context([]); c['state']['powers'] = {'Metallicize': 3}
        c['state']['unmodeled_effects'] = ['Unknown damage before Metallicize']
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])
        c['state']['powers'] = None
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])

    def test_collector_manifest_is_a2_only_and_does_not_invent_raw_damage(self):
        self.assertIsNone(boss_manifest('The Collector', 1))
        self.assertIsNone(boss_manifest('The Collector', True))
        m = boss_manifest('The Collector', 2)
        self.assertIsNone(m['raw_attack_damage'])
        self.assertTrue(m['source_conflicts'])

    def test_collector_complete_attack_roster_uses_displayed_values(self):
        c = collector(); c['state']['enemies'][1]['intent_hits'] = [9]
        c['state'].update(vulnerable=2, block=10)
        r = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
        self.assertTrue(r['allowed'], r['reasons'])
        self.assertEqual(r['forecast']['incoming_displayed'], 34, 'displayed hits already include modifiers')
        self.assertEqual(r['forecast']['player_hp'], 56)
        c['state']['enemies_complete'] = False
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])

    def test_collector_buff_debuff_summon_or_wrong_reference_blocks_turn_forecast(self):
        for move in ('Buff', 'Mega Debuff', 'Spawn', 'Revive', 'Unknown'):
            c = collector(move=move)
            r = check_plan(c, {'steps': [{'kind': 'end_turn'}]})
            self.assertFalse(r['allowed'], move); self.assertIsNone(r['forecast'])
        c = collector(); c['state']['enemies'][1]['evidence']['observed_intent'] = False
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])
        c = collector(); c['boss_manifest'] = {'name': 'The Collector'}
        self.assertFalse(check_plan(c, {'steps': [{'kind': 'end_turn'}]})['allowed'])

    def test_explosive_immediate_damage_ignores_attack_modifiers_and_stops(self):
        c = context([]); c['inventory']['current']['potion'] = ['Explosive Potion']
        c['state'].update(enemies_complete=True, strength=20, weak=2)
        c['state']['enemies'][0].update(block=3, vulnerable=2, potion_damage_modifiers=[], damage_reactions=[])
        c['state']['enemies'].append(dict(c['state']['enemies'][0], id='other', hp=5, block=0))
        before = deepcopy(c)
        p = {'kind': 'potion', 'name': 'Explosive Potion', 'targets': ['enemy', 'other']}
        r = check_plan(c, {'steps': [p]})
        self.assertTrue(r['allowed'], r['reasons'])
        self.assertEqual(r['steps'][0]['reviewed_effect']['enemies'],
                         [{'id': 'enemy', 'hp': 33, 'block': 0}, {'id': 'other', 'hp': 0, 'block': 0}])
        self.assertIsNone(r['forecast'])
        self.assertEqual(c, before)
        self.assertFalse(check_plan(c, {'steps': [p, {'kind': 'end_turn'}]})['allowed'])
        self.assertFalse(check_plan(c, {'steps': [p], 'claims_lethal': True})['allowed'])
        for field in ('potion_damage_modifiers', 'damage_reactions'):
            c['state']['enemies'][0][field] = None
            self.assertFalse(check_plan(c, {'steps': [p]})['allowed'])
            c['state']['enemies'][0][field] = []
        for targets in ([], ['enemy'], ['enemy', 'enemy'], ['enemy', 'other', 'missing']):
            self.assertFalse(check_plan(c, {'steps': [dict(p, targets=targets)]})['allowed'])

    def test_current_relics_and_metallicize_routine_support_still_require_evidence(self):
        c = routine_context()
        c['inventory']['current']['relic'] += ['Red Mask', 'Potion Belt', 'Shuriken']
        c['state']['counters']['shuriken'] = 1
        c['state']['powers'] = {'Metallicize': 3}
        self.assertTrue(plan(c).ready)
        c['state']['enemies'][0]['artifact'] = None
        self.assertFalse(plan(c).ready)
        c['state']['enemies'][0]['artifact'] = 0
        c['state']['powers']['Rage'] = 3
        self.assertFalse(plan(c).ready)

    def test_collector_routine_does_not_bypass_reactive_turn(self):
        c = collector([card('Strike')])
        r = plan_routine_combat(observation(c, act=2), report(), encounter_name='The Collector', context=c)
        self.assertTrue(r.ready, r.reasons)
        c['state']['enemies'][0].update(move='Buff', intent='buff', intent_hits=[])
        self.assertFalse(plan_routine_combat(observation(c, act=2), report(),
                                           encounter_name='The Collector', context=c).ready)


if __name__ == '__main__':
    unittest.main()
