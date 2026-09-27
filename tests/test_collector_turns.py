"""A2 source-reviewed one-turn bounds, not console order/recognition proof.

Effect constants: community Collector wiki and sts_lightspeed MonsterSpecific.
Final-floor arithmetic: sts_lightspeed Monster::calculateDamageToPlayer.
Exact sources and compatibility limits live in the versioned boss manifest.
"""
from copy import deepcopy
from fractions import Fraction
import unittest

from veda.advisory import (COLLECTOR_EFFECTS, _displayed_raw_upper, boss_manifest,
                           check_plan, collector_turn_bound)
from veda.routine_combat import _boundary_rank, plan_routine_combat
from tests.test_collector_current_run import collector
from tests.test_advisory_current_deck import card
from tests.test_routine_current_deck import observation, report


EFFECTS = {
    'Fireball': [],
    'Buff': [{'kind': 'gain_strength', 'target': 'all_enemies', 'amount': 3},
             {'kind': 'gain_block', 'target': 'self', 'amount': 15}],
    'Mega Debuff': [{'kind': 'apply_debuff', 'debuff': n, 'amount': 3}
                    for n in ('weak', 'vulnerable', 'frail')],
    'Spawn': [{'kind': 'summon_to_limit', 'enemy': 'Torch Head', 'amount': 2}],
    'Revive': [{'kind': 'summon_to_limit', 'enemy': 'Torch Head', 'amount': 2}],
}


def turn(move, torches=2, hand=None):
    c = collector(hand or [], move=move)
    c['state']['enemies'] = c['state']['enemies'][:1 + torches]
    c['state']['enemies'][0]['intent_effects'] = deepcopy(EFFECTS[move])
    c['state']['block'] = 0
    return c


def end(c):
    return check_plan(c, {'steps': [{'kind': 'end_turn'}]})


class CollectorTurnTests(unittest.TestCase):
    def test_buff_accounts_for_torches_after_strength_and_retains_displayed_sum(self):
        c = turn('Buff'); before = deepcopy(c)
        r = end(c)
        self.assertTrue(r['allowed'], r['reasons'])
        f = r['forecast']
        self.assertEqual(f['kind'], 'conservative_survival_bound')
        self.assertEqual(f['incoming_displayed'], 14)
        self.assertEqual(f['incoming_upper_bound'], 20)
        self.assertEqual(f['player_hp_lower_bound'], 60)
        self.assertEqual(f['player_hp'], 60)
        self.assertEqual(f['enemy_state_scope'], 'after_player_actions_before_enemy_turn')
        self.assertEqual(f['enemies'][0]['block'], 0, 'do not assert post-enemy state')
        self.assertEqual(c, before)

    def test_mega_debuff_uses_new_vulnerability_without_reducing_existing_block(self):
        c = turn('Mega Debuff'); c['state']['block'] = 13
        c['state']['powers'] = {'Metallicize': 3, 'Artifact': 2}
        r = end(c)
        self.assertTrue(r['allowed'], r['reasons'])
        self.assertEqual(r['forecast']['incoming_upper_bound'], 20)
        self.assertEqual(r['forecast']['survival_block'], 16)
        self.assertEqual(r['forecast']['player_hp_lower_bound'], 76)
        self.assertEqual(c['state']['vulnerable'], 0)
        self.assertEqual(c['state']['powers']['Artifact'], 2)

    def test_already_vulnerable_is_not_multiplied_twice(self):
        c = turn('Mega Debuff'); c['state']['vulnerable'] = 1
        for e in c['state']['enemies'][1:]: e['intent_hits'] = [10]
        self.assertEqual(end(c)['forecast']['incoming_upper_bound'], 20)

    def test_integer_inverse_matches_exhaustive_independent_rational_enumeration(self):
        for weak in (False, True):
            for vulnerable in (False, True):
                multiplier = (Fraction(3, 4) if weak else 1) * (Fraction(3, 2) if vulnerable else 1)
                for shown in range(51):
                    candidates = [raw for raw in range(101) if int(raw * multiplier) == shown]
                    self.assertEqual(_displayed_raw_upper(shown, weak, vulnerable),
                                     max(candidates) if candidates else None)

    def test_bound_dominates_all_compatible_raw_hits_and_both_effect_orders(self):
        for move in ('Buff', 'Mega Debuff'):
            for weak in (False, True):
                for vulnerable in (False, True):
                    factor = (Fraction(3, 4) if weak else 1) * (Fraction(3, 2) if vulnerable else 1)
                    for raw in range(41):
                        c = turn(move, torches=1); c['state']['vulnerable'] = int(vulnerable)
                        torch = c['state']['enemies'][1]
                        torch.update(weak=int(weak), intent_hits=[int(raw * factor)])
                        actual_after = int((raw + (3 if move == 'Buff' else 0))
                            * (Fraction(3, 4) if weak else 1)
                            * (Fraction(3, 2) if vulnerable or move == 'Mega Debuff' else 1))
                        bound = collector_turn_bound(c['state'], [])['incoming_upper_bound']
                        self.assertGreaterEqual(bound, max(torch['intent_hits'][0], actual_after))

    def test_rounded_weak_display_is_not_naively_incremented(self):
        c = turn('Buff', torches=1); c['state']['enemies'][1].update(weak=1, intent_hits=[5])
        # Raw 7 fits Weak display 5; +3 yields floor(10*.75)=7.
        self.assertEqual(end(c)['forecast']['incoming_upper_bound'], 7)
        c['state']['vulnerable'] = 1
        c['state']['enemies'][1]['intent_hits'] = [7]  # raw7 * .75 *1.5 ->7
        self.assertEqual(end(c)['forecast']['incoming_upper_bound'], 11)

    def test_inconsistent_fractional_modifier_display_is_not_forecast(self):
        c = turn('Buff', torches=1); c['state']['vulnerable'] = 1
        c['state']['enemies'][1]['intent_hits'] = [2]  # no integer raw *1.5 floors to2
        self.assertFalse(end(c)['allowed']); self.assertIsNone(end(c)['forecast'])

    def test_spawn_and_revive_bound_vacant_slots_without_inventing_roster(self):
        for move in ('Spawn', 'Revive'):
            for n in range(3):
                c = turn(move, torches=n); before = deepcopy(c)
                r = end(c)
                self.assertTrue(r['allowed'], r['reasons'])
                self.assertEqual(r['forecast']['incoming_upper_bound'], 14)
                self.assertEqual(len(r['forecast']['enemies']), n + 1)
                self.assertEqual(c, before)

    def test_spawn_includes_maximum_observed_strength_and_current_vulnerability(self):
        c = turn('Revive', torches=1); c['state']['vulnerable'] = 2
        c['state']['enemies'][0]['strength'] = 6
        c['state']['enemies'][1].update(strength=9, intent_hits=[24])
        self.assertEqual(end(c)['forecast']['incoming_upper_bound'], 48)

    def test_thorns_death_before_spawn_can_add_replacement_after_old_hit(self):
        c = turn('Spawn'); c['inventory']['current']['relic'] = ['Bronze Scales']
        c['state']['powers'] = {'Thorns': 3}
        for e in c['state']['enemies'][1:]: e['hp'] = 3
        r = end(c)
        self.assertTrue(r['allowed'], r['reasons'])
        self.assertEqual(r['forecast']['incoming_upper_bound'], 28)
        self.assertEqual(r['forecast']['terms'][-1]['possible_thorns_vacancies'], 2)
        c['state']['energy'] = 0
        routine = plan_routine_combat(observation(c), report(), encounter_name='The Collector', context=c)
        self.assertTrue(routine.ready, routine.reasons)
        c['state']['powers']['Thorns'] = 6
        self.assertFalse(end(c)['allowed'])
        c['state']['powers']['Thorns'] = None
        self.assertFalse(end(c)['allowed'])

    def test_unknowns_immunities_and_unmodeled_effects_remain_blockers(self):
        for key in ('weak', 'vulnerable', 'strength', 'dexterity'):
            c = turn('Buff'); c['state'][key] = None
            self.assertFalse(end(c)['allowed'], key)
        for key in ('weak', 'vulnerable', 'strength', 'artifact'):
            c = turn('Buff'); c['state']['enemies'][1][key] = None
            self.assertFalse(end(c)['allowed'], key)
        for relic in ('Paper Krane', 'Odd Mushroom', "Philosopher's Stone"):
            c = turn('Buff'); c['inventory']['current']['relic'] = [relic]
            self.assertFalse(end(c)['allowed'], relic)
        c = turn('Buff'); c['state']['unmodeled_effects'] = ['unknown immunity']
        self.assertFalse(end(c)['allowed'])
        c = turn('Buff'); c['state']['powers']['Intangible'] = 1
        self.assertFalse(end(c)['allowed'])

    def test_typed_effects_reference_roster_and_ascension_are_required(self):
        for change in ('missing_effects', 'wrong_amount', 'wrong_source', 'extra_enemy', 'incomplete', 'ascension', 'dead'):
            c = turn('Buff'); b = c['state']['enemies'][0]
            if change == 'missing_effects': b['intent_effects'] = []
            elif change == 'wrong_amount': b['intent_effects'][0]['amount'] = 4
            elif change == 'wrong_source': b['evidence']['source'] = 'unknown'
            elif change == 'extra_enemy': c['state']['enemies'].append(dict(b, id='other'))
            elif change == 'incomplete': c['state']['enemies_complete'] = False
            elif change == 'ascension': c['state']['ascension'] = 3
            else: c['state']['enemies'][1]['hp'] = 0
            self.assertFalse(end(c)['allowed'], change)

    def test_unsafe_end_turn_and_potion_review_use_upper_bound(self):
        c = turn('Mega Debuff'); c['state']['hp'] = 18
        self.assertFalse(end(c)['allowed'])  # old displayed14 would look survivable
        c['state']['hp'] = 30; c['inventory']['current']['potion'] = ['Explosive Potion']
        self.assertFalse(end(c)['allowed'])
        r = check_plan(c, {'steps': [{'kind':'end_turn'}],
            'potion_review': {'Explosive Potion': 'Inspected targets; retain potion for later.'}})
        self.assertTrue(r['allowed'], r['reasons'])
        self.assertEqual(r['forecast']['player_hp_lower_bound'], 10)

    def test_routine_uses_upper_bound_for_end_turn_and_special_card_rank(self):
        c = turn('Mega Debuff'); c['state']['energy'] = 0
        r = plan_routine_combat(observation(c), report(), encounter_name='The Collector', context=c)
        self.assertTrue(r.ready, r.reasons)
        self.assertEqual(r.prediction.incoming_damage, 20)
        c = turn('Mega Debuff', hand=[card('True Grit','Skill')]); c['state']['hp'] = 8
        r = plan_routine_combat(observation(c), report(), encounter_name='The Collector', context=c)
        self.assertFalse(r.ready)

    def test_ranking_torch_kill_before_revive_does_not_keep_weaker_old_hit(self):
        c = turn('Revive', torches=1, hand=[card('Strike+')])
        c['state']['enemies'][1].update(hp=5, weak=1, intent_hits=[5])
        rank = _boundary_rank(c['state'], c['state']['hand'][0],
                              {'target':'torch0'}, [])
        self.assertEqual(rank[1], -66)  # two possible fresh7 hits; not old5+7

    def test_manifest_has_same_exact_typed_effects_and_no_raw_fireball_substitution(self):
        m = boss_manifest('The Collector', 2)
        self.assertEqual(m['turn_effects'], EFFECTS)
        self.assertIsNone(m['raw_attack_damage'])
        self.assertEqual(m['forecast_kind'], 'conservative_survival_bound')
        self.assertEqual(COLLECTOR_EFFECTS, EFFECTS)


if __name__ == '__main__':
    unittest.main()
