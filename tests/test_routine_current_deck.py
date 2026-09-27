"""Deterministic state replay tests; these do not establish live vision accuracy."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
import unittest

from veda.advisory import SCHEMA, SPIKE_SLIME_SOURCE
from veda.calibration import CalibrationReport
from veda.routine_combat import ROUTINE_CRITICAL_FIELDS, plan_routine_combat
from veda.vision import StructuredGameState, VisibleEnemy


def card(name='Strike', *, cost=1, kind='Attack', ident='c1', **extras):
    return dict(id=ident, name=name, cost=cost, type=kind,
                upgraded=name.endswith('+'), title_color='green' if name.endswith('+') else 'white',
                playable=True, **extras)


def context(hand=None):
    return {'fresh': True, 'unknowns': [], 'encounter_type': 'enemy',
            'inventory': {'coverage': {'relic': 'complete', 'potion': 'complete'},
                          'current': {'relic': ['Burning Blood', "Neow's Lament", 'Anchor'], 'potion': []}},
            'state': {'schema': SCHEMA, 'observed_at': datetime.now(timezone.utc).isoformat(),
                      'ascension': 2, 'hp': 50, 'max_hp': 80, 'energy': 3, 'block': 0,
                      'strength': 0, 'dexterity': 0, 'weak': 0, 'frail': 0, 'vulnerable': 0,
                      'no_block': 0, 'powers': {}, 'powers_complete': True,
                      'hand': hand if hand is not None else [card()], 'hand_complete': True,
                      'piles': {'draw': None, 'discard': [], 'exhaust': None},
                      'counters': {}, 'unmodeled_effects': [], 'end_turn_damage': 0,
                      'enemies': [{'id': 'enemy', 'name': 'Cultist', 'hp': 50, 'max_hp': 50,
                                   'block': 0, 'artifact': 0, 'vulnerable': 0, 'weak': 0,
                                   'strength': 0, 'intent': 'attack 6', 'intent_hits': [6]}]}}


def observation(c, *, act=1):
    s = c['state']
    return StructuredGameState('COMBAT', 1, act=act, ascension=s['ascension'], hp=s['hp'],
        max_hp=s['max_hp'], energy=s['energy'], block=s['block'], player_strength=s['strength'],
        player_weak=s['weak'], player_frail=s['frail'], player_vulnerable=s['vulnerable'],
        end_turn_damage=s['end_turn_damage'], end_turn_damage_confidence=1,
        hand=tuple(c['name'] for c in s['hand']), hand_complete=s['hand_complete'],
        hand_details=tuple({'name': c['name'], 'title_color': c['title_color'],
                            'upgraded': c['upgraded'], 'current_cost': c['cost']} for c in s['hand']),
        enemies=tuple(VisibleEnemy(e['name'], e['hp'], e['max_hp'], e['intent'],
                      block=e['block'], intent_hits=tuple(e['intent_hits']),
                      intent_total_damage=sum(e['intent_hits']), intent_damage_confidence=1)
                      for e in s['enemies']))


def report():
    return CalibrationReport({f: 1.0 for f in ROUTINE_CRITICAL_FIELDS}, 12)


def plan(c, **kwargs):
    return plan_routine_combat(observation(c), report(), encounter_name=c['state']['enemies'][0]['name'],
                               context=c, **kwargs)


class RoutineCurrentDeckTests(unittest.TestCase):
    def test_upgrade_and_observed_cost_are_used_without_mutating_context(self):
        c = context([card('Strike+', cost=0)])
        c['state'].update(energy=0, strength=2)
        c['state']['enemies'][0]['hp'] = 11
        before = copy.deepcopy(c)
        result = plan(c)
        self.assertTrue(result.ready, result.reasons)
        self.assertEqual(result.sequence[0].cost, 0)
        self.assertEqual(result.sequence[0].attack_damage, 11)
        self.assertEqual(result.next_action, {'kind': 'card', 'card_id': 'c1', 'target': 'enemy'})
        self.assertTrue(result.checked_result['forecast']['lethal_to_enemies'])
        self.assertEqual(c, before)

    def test_block_modifiers_and_body_slam_share_advisory_arithmetic(self):
        c = context([card('Defend+', kind='Skill'), card('Body Slam+', cost=0, ident='slam')])
        c['state'].update(dexterity=2, frail=1, strength=3, block=4)
        result = plan(c)
        self.assertTrue(result.ready, result.reasons)
        self.assertEqual(result.next_action['card_id'], 'c1')
        self.assertEqual(len(result.sequence), 1, 'lookahead cannot authorize multiple inputs')
        self.assertEqual(result.prediction.player_block, 11)
        self.assertEqual(result.checked_result['forecast']['enemies'][0]['hp'], 50)

    def test_missing_fields_disagreement_and_legacy_context_fail_closed(self):
        c = context()
        self.assertFalse(plan_routine_combat(observation(c), report(), encounter_name='Cultist').ready)
        changes = [lambda o: replace(o, hand_complete=False),
                   lambda o: replace(o, hand_details=()),
                   lambda o: replace(o, energy=0),
                   lambda o: replace(o, hp=1),
                   lambda o: replace(o, hand_details=({'name': 'Strike', 'upgraded': False,
                                                       'title_color': 'white', 'current_cost': None},))]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(plan_routine_combat(change(observation(c)), report(),
                                                    encounter_name='Cultist', context=c).ready)
        old = report()
        accuracy = dict(old.field_accuracy); accuracy.pop('hand_details')
        self.assertFalse(plan_routine_combat(observation(c), CalibrationReport(accuracy, 12),
                                            encounter_name='Cultist', context=c).ready)

    def test_unknown_unselected_card_fields_still_prevent_play(self):
        c = context([card(), card('Impervious', kind='Skill', ident='other')])
        c['state']['hand'][1]['cost'] = None
        self.assertFalse(plan(c).ready)
        c['state']['hand'][1]['cost'] = 2
        result = plan(c)
        self.assertTrue(result.ready, result.reasons)
        self.assertEqual(result.supported_card_count, 1)
        self.assertEqual(result.deferred_card_count, 1)
        self.assertIn('Impervious', ' '.join(result.support_notes))

    def test_anger_generated_card_and_hemokinesis_hp_are_boundaries(self):
        for name, damage in [('Anger', 6), ('Hemokinesis+', 20), ('Hemokinesis', 15)]:
            with self.subTest(card=name):
                c = context([card(name, cost=0 if name == 'Anger' else 1)])
                before = copy.deepcopy(c)
                result = plan(c)
                self.assertTrue(result.ready, result.reasons)
                self.assertEqual(result.sequence[0].attack_damage, damage)
                self.assertEqual(len(result.sequence), 1)
                self.assertTrue(result.checked_result['steps'][0]['observe_after'])
                self.assertIsNone(result.prediction)
                self.assertEqual(c, before)
        c = context([card('Hemokinesis+')]); c['state'].update(hp=2, block=999)
        c['state']['enemies'][0]['hp'] = 1
        self.assertFalse(plan(c).ready, 'HP cost happens before the possible final kill')
        c['state'].update(hp=3, block=0); c['state']['enemies'][0]['hp'] = 50
        self.assertFalse(plan(c).ready, 'surviving self-damage alone does not prove survival')

    def test_bash_damage_does_not_gain_its_own_vulnerable(self):
        c = context([card('Bash+', cost=2)])
        c['state'].update(hp=1, block=0)
        c['state']['enemies'][0]['hp'] = 11
        self.assertFalse(plan(c).ready, '10 damage cannot kill 11 HP before incoming 6')
        c['state']['enemies'][0]['hp'] = 10
        self.assertTrue(plan(c).ready)

    def test_draw_and_exhaust_have_no_speculative_followup(self):
        for name, kind in [('Shrug It Off', 'Skill'), ('Shrug It Off+', 'Skill'), ('Slimed', 'Status')]:
            with self.subTest(card=name):
                c = context([card(name, kind=kind)])
                result = plan(c)
                self.assertTrue(result.ready, result.reasons)
                self.assertTrue(result.checked_result['steps'][0]['observe_after'])
                self.assertIsNone(result.prediction)
        c = context([card('Headbutt')]); c['state']['piles']['discard'] = ['Strike']
        result = plan(c)
        self.assertFalse(result.ready)
        self.assertIn('discard-return choice', ' '.join(result.support_notes))
        c['state']['piles']['discard'] = []
        self.assertTrue(plan(c).ready)

    def test_status_and_relic_interactions_are_not_silently_ignored(self):
        mutations = [lambda c: c['state'].update(unmodeled_effects=['Thorns']),
                     lambda c: c['state']['powers'].update(Corruption=True),
                     lambda c: c['inventory']['current']['relic'].append('Kunai'),
                     lambda c: c['inventory']['current']['relic'].append('Unreviewed Relic'),
                     lambda c: c['state'].update(end_turn_damage=2),
                     lambda c: c.update(fresh=False)]
        for mutate in mutations:
            c = context(); mutate(c)
            self.assertFalse(plan(c).ready)
        c = context([card('Hemokinesis+')]); c['inventory']['current']['relic'].append('Centennial Puzzle')
        result = plan(c)
        self.assertTrue(result.ready, result.reasons)
        self.assertTrue(result.checked_result['steps'][0]['observe_after'])
        self.assertIsNone(result.prediction, 'Puzzle draw must be observed, not invented')

    def test_bounded_search_can_consider_more_than_three_cards_but_emits_one(self):
        c = context([card('Strike', cost=0, ident=f'c{i}') for i in range(5)])
        c['state']['enemies'][0]['hp'] = 30
        result = plan(c, max_candidates=512, max_depth=5)
        self.assertTrue(result.ready)
        self.assertGreater(result.evaluated_candidates, 3)
        self.assertEqual(len(result.sequence), 1)
        self.assertEqual(result.prediction.enemies[0].hp, 24, 'prediction describes only the next action')
        capped = plan(c, max_candidates=2, max_depth=10)
        self.assertTrue(capped.ready)
        self.assertEqual(capped.evaluated_candidates, 2)
        self.assertTrue(capped.search_truncated)

    def test_target_ids_and_reviewed_spike_evidence_can_bypass_act1_registry(self):
        c = context()
        enemy = c['state']['enemies'][0]
        enemy.update(name='Spike Slime (M)', hp=31, max_hp=31, move='Flame Tackle',
                     intent_effects=[{'kind': 'generate_status', 'card': 'Slimed', 'count': 1, 'to_zone': 'discard'}],
                     evidence={'source': SPIKE_SLIME_SOURCE, 'kind': 'reviewed_reference', 'observed_intent': True})
        other = copy.deepcopy(enemy); other['id'] = 'other'; c['state']['enemies'].append(other)
        result = plan_routine_combat(observation(c, act=2), report(), encounter_name='Spike Slime (M)', context=c)
        self.assertTrue(result.ready, result.reasons)
        self.assertIn(result.next_action['target'], ('enemy', 'other'))
        self.assertEqual(len(result.sequence), 1)
        self.assertIsNone(result.prediction)
        other['evidence']['observed_intent'] = False
        self.assertFalse(plan_routine_combat(observation(c, act=2), report(), encounter_name='Spike Slime (M)', context=c).ready)

    def test_unknown_and_reactive_encounters_escalate(self):
        for name in ('Unknown', 'Gremlin Nob', 'Lagavulin', 'Sentry'):
            c = context(); c['state']['enemies'][0]['name'] = name
            self.assertFalse(plan(c).ready)

    def test_end_turn_requires_all_same_guards_and_no_affordable_card(self):
        c = context(); c['state']['energy'] = 0
        result = plan(c)
        self.assertTrue(result.ready, result.reasons)
        self.assertEqual(result.next_action, {'kind': 'end_turn'})
        self.assertEqual(result.sequence, ())
        self.assertEqual(result.instructions, ('End Turn',))
        self.assertEqual(result.prediction.projected_player_hp, 44)
        c['state']['hp'] = 6
        self.assertFalse(plan(c).ready, 'no actions does not make a lethal End Turn safe')
        c['state']['hp'] = 50
        self.assertFalse(plan_routine_combat(replace(observation(c), energy=1), report(),
                                            encounter_name='Cultist', context=c).ready)
        c = context([card('Anger', cost=0)]); c['state']['energy'] = 0
        self.assertEqual(plan(c).next_action['kind'], 'card', 'zero-cost play cannot disappear in End Turn fallback')
        c = context([card('Impervious', kind='Skill', cost=2)])
        self.assertFalse(plan(c).ready, 'unsupported affordable card requires review')
        c['state']['energy'] = 0
        self.assertEqual(plan(c).next_action, {'kind': 'end_turn'})
        c['state']['hand'][0]['cost'] = None
        self.assertFalse(plan(c).ready, 'unread cost cannot authorize End Turn')

    def test_empty_hand_and_verified_dazed_can_end_safely(self):
        c = context([])
        self.assertEqual(plan(c).next_action, {'kind': 'end_turn'})
        c = context([card('Dazed', cost=None, kind='Status', unplayable=True, ethereal=True)])
        c['state']['hand'][0]['playable'] = False
        self.assertEqual(plan(c).next_action, {'kind': 'end_turn'})
        c['state']['hand'][0].pop('unplayable')
        self.assertFalse(plan(c).ready)


if __name__ == '__main__':
    unittest.main()
