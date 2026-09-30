"""Focused contract coverage for the reviewed Treasure/chest interaction."""
from copy import deepcopy
import unittest

from tests import test_choice_execution as fixtures
from veda.choice_execution import plan_choice_step
from veda.menu_controls import CONTROL_PROFILE, TREASURE_RULE, bind_reviewed_menu_controls


class TreasureOptionTests(unittest.TestCase):
    def treasure(self):
        obs = fixtures.observation('treasure')
        obs['context'].update(combat_id=None, turn_id=None)
        obs['facts'] = {'node_type': 'treasure', 'floor': 9, 'current_node_id': 'act1-floor9-center'}
        obs['ui'].update(menu_family='treasure_options', control_layout='ps5_default',
                          layout_id='ps5-treasure-options-reviewed-v1',
                          screen='treasure', order=['open-chest', 'skip-chest'], focused_id='open-chest',
                          selected_ids=[], pending_ids=[], navigation=[], grid=None,
                          options=[{'id': 'open-chest', 'label': 'Open Chest', 'role': 'open_chest',
                                    'enabled': True, 'costs': {}},
                                   {'id': 'skip-chest', 'label': 'Skip Chest', 'role': 'skip_chest',
                                    'enabled': True, 'costs': {},
                                    'activate': {'button': 'triangle', 'evidence': {
                                        'kind': 'visible_hint', 'reviewer': 'synthetic fixture only',
                                        'meaning': 'activate:skip-chest', 'layout_id': 'ps5-treasure-options-reviewed-v1',
                                        'frame_id': 'frame-1', 'image_sha256': '1' * 64,
                                        'hint_text': '△ Skip Chest'}}}])
        return obs

    def test_default_cross_opens_reviewed_chest(self):
        before = bind_reviewed_menu_controls(self.treasure(), control_profile=CONTROL_PROFILE,
                                             now=fixtures.NOW, max_age_seconds=30)
        choice = {'kind': 'treasure', 'choice_id': before['ui']['choice_id'],
                  'option_ids': ['open-chest'], 'review': dict(before['review'], kind='reviewed_choice'),
                  'postconditions': {'screen': 'reward', 'phase': 'result',
                                     'context': deepcopy(before['context']),
                                     'resources': deepcopy(before['resources']),
                                     'inventory_digest': 'unchanged', 'facts': {},
                                     'allow_changed_facts': []}}
        step = plan_choice_step(before, choice, now=fixtures.NOW)
        self.assertEqual(['cross'], step['command']['buttons'])
        self.assertEqual('commit', step['step_kind'])
        self.assertEqual(TREASURE_RULE, before['ui']['options'][0]['activate']['evidence']['rule_id'])

    def test_visible_triangle_skips_reviewed_chest(self):
        before = bind_reviewed_menu_controls(self.treasure(), control_profile=CONTROL_PROFILE,
                                             now=fixtures.NOW, max_age_seconds=30)
        self.assertEqual('triangle', before['ui']['options'][1]['activate']['button'])

    def test_treasure_reward_uses_reviewed_loot_controls(self):
        obs = fixtures.observation('reward')
        obs['context'].update(combat_id=None, turn_id=None)
        obs['facts'] = {'reward_source': 'treasure', 'potion_capacity': 3}
        obs['ui'].update(menu_family='loot_rewards', control_layout='ps5_default', layout_id='loot-reward-v1',
                         screen='reward', order=['gold', 'bronze-scales', 'skip-rewards'],
                         focused_id='gold', selected_ids=[], pending_ids=[], navigation=[],
                         grid={'complete': True, 'cells': [
                             {'id': 'gold', 'row': 0, 'column': 0},
                             {'id': 'bronze-scales', 'row': 1, 'column': 0},
                             {'id': 'skip-rewards', 'row': 2, 'column': 0}]},
                         options=[
                             {'id': 'gold', 'label': '25 Gold', 'enabled': True, 'costs': {}, 'role': 'gold'},
                             {'id': 'bronze-scales', 'label': 'Bronze Scales', 'enabled': True, 'costs': {}, 'role': 'relic'},
                             {'id': 'skip-rewards', 'label': 'Skip Rewards', 'enabled': True, 'costs': {}, 'role': 'skip',
                              'activate_hint': {'button': 'triangle', 'hint_text': '△ Skip Rewards'}}])
        bound = bind_reviewed_menu_controls(obs, control_profile=CONTROL_PROFILE,
                                            now=fixtures.NOW, max_age_seconds=30)
        self.assertEqual('cross', bound['ui']['options'][0]['activate']['button'])
        self.assertEqual('triangle', bound['ui']['options'][2]['activate']['button'])

    def test_treasure_reward_can_reuse_verified_treasure_node_fact(self):
        obs = fixtures.observation('reward')
        obs['context'].update(combat_id=None, turn_id=None)
        obs['facts'] = {'node_type': 'treasure'}
        obs['ui'].update(menu_family='loot_rewards', control_layout='ps5_default',
                         screen='reward', order=['gold'], focused_id='gold',
                         selected_ids=[], pending_ids=[], navigation=[],
                         grid={'complete': True, 'cells': [{'id': 'gold', 'row': 0, 'column': 0}]},
                         options=[{'id': 'gold', 'label': '25 Gold', 'enabled': True, 'costs': {}}])
        bound = bind_reviewed_menu_controls(obs, control_profile=CONTROL_PROFILE,
                                            now=fixtures.NOW, max_age_seconds=30)
        self.assertEqual('cross', bound['ui']['options'][0]['activate']['button'])


if __name__ == '__main__':
    unittest.main()
