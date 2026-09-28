"""Compact policy forwarding and the retained floor-two failure shape, synthetic only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_combat_requests import draft, result
from tests import test_combat_requests as combat_fixtures
from tests.test_menu_requests import make_capture
from veda.combat_requests import validate_combat_draft, write_combat_request, read_pending, _result_draft
from veda.reviewed_play import SCHEMA as SESSION_SCHEMA, ReviewedPlaySession


def floor_two_draft():
    value = draft(); state = value['state']
    state.update(hp=79, energy=0, floor=2, gold=113, ascension=2,
        unmodeled_effects=['The exact status effects represented by the visible non-damage intent are not modeled.'])
    state['hand'] = [dict(id='defend-0', name='Defend', type='Skill', cost=1, upgraded=False,
                         title_color='white', playable=False),
                     dict(id='defend-1', name='Defend', type='Skill', cost=1, upgraded=False,
                         title_color='white', playable=False)]
    state['enemies'] = [dict(id='spike-medium', name='Spike Slime (M)', hp=26, max_hp=32, block=0,
        vulnerable=0, weak=0, strength=0, artifact=0, intent='Lick', intent_hits=[], intent_effects=[],
        intent_damage_confidence=1.0)]
    value['inventory']['current'].update(relic=['Burning Blood'], potion=['Energy Potion'])
    value['encounter'].update(name='Acid Slime (S) and Spike Slime (M)', confidence=.8)
    value['perception']['confidence'] = .85
    value['ui'].update(focused_card_id=None, hand_order=['defend-0','defend-1'], target_order=['spike-medium'])
    value['plan'] = {'steps':[{'kind':'end_turn'}]}
    return value


class CompactPolicyTests(unittest.TestCase):
    def test_floor_two_end_turn_repro_strict_blocks_learning_warns_without_forecast(self):
        value = floor_two_draft()
        with self.assertRaisesRegex(ValueError, 'survival forecast.*Energy Potion'):
            validate_combat_draft(value)
        value['decision_policy'] = 'learning'; original = deepcopy(value)
        with patch('socket.socket', side_effect=AssertionError('no input')):
            result = validate_combat_draft(value)
        self.assertEqual(original, value)
        self.assertEqual({'buttons':['triangle'], 'expected_kind':'end_turn'}, result['next_atomic_input_preview'])
        self.assertIsNone(result['assessment']['forecast'])
        self.assertEqual('unknown', result['assessment']['forecast_status'])
        self.assertTrue(any('Energy Potion' in x for x in result['assessment']['warnings']))
        self.assertFalse(result['dispatchable'])

    def test_optional_potion_review_is_accepted_but_does_not_fabricate_intent_support(self):
        value = floor_two_draft()
        value['plan']['potion_review'] = {'Energy Potion':'Save it; the two remaining Defends do not help the observed nonattack.'}
        with self.assertRaisesRegex(ValueError, 'survival forecast') as raised:
            validate_combat_draft(value)
        self.assertNotIn('review available Energy Potion', str(raised.exception))
        value['decision_policy'] = 'learning'
        result = validate_combat_draft(value)
        self.assertTrue(result['draft_valid']); self.assertIsNone(result['assessment']['forecast'])

    def test_incomplete_inventory_unknown_intent_and_low_confidence_are_retained(self):
        value = draft(); value['decision_policy'] = 'learning'
        value['state']['enemies'][0].update(intent=None, intent_hits=None, intent_effects=None)
        value['state'].update(hand_complete=False, powers_complete=False, block=None)
        value['inventory']['coverage'].update(relic='partial', potion='unknown')
        value['perception']['confidence'] = .1
        value['unknowns'] = ['Some hand and intent details are unread.']
        with TemporaryDirectory() as directory:
            root = Path(directory); now = datetime.now(timezone.utc)
            image = make_capture(root, now-timedelta(seconds=1)); output = root/'packet.json'
            write_combat_request(value, capture=image, reviewer='Fixture reviewer', evidence_note='Synthetic image reviewed.',
                                 reviewed=True, output=output, now=now)
            packet = json.loads(output.read_text())
        self.assertEqual('learning', packet['decision_policy'])
        self.assertEqual('learning', packet['reading']['context']['decision_policy'])
        self.assertEqual(value['inventory'], packet['reading']['context']['inventory'])
        self.assertIsNone(packet['reading']['state']['enemies'][0]['intent_total_damage'])
        self.assertIsNone(packet['reading']['context']['state']['enemies'][0]['intent_hits'])
        self.assertEqual(value['unknowns'], packet['reading']['context']['unknowns'])

    def test_learning_does_not_bypass_mapping_source_or_known_energy(self):
        value = draft(); value['decision_policy'] = 'learning'
        for edit in (lambda v:v['ui'].update(focused_card_id=None), lambda v:v['state'].update(energy=0),
                     lambda v:v.update(decision_policy='anything'), lambda v:v['plan']['steps'][0].update(kind='potion')):
            changed = deepcopy(value); edit(changed)
            with self.assertRaises((ValueError, RuntimeError)):
                validate_combat_draft(changed)
        with TemporaryDirectory() as directory:
            root = Path(directory); now = datetime.now(timezone.utc)
            image = make_capture(root, now-timedelta(seconds=31))
            with self.assertRaises(ValueError):
                write_combat_request(value, capture=image, reviewer='Fixture', evidence_note='Synthetic reviewed.',
                                     reviewed=True, output=root/'packet.json', now=now)
            self.assertFalse((root/'packet.json').exists())

    def test_session_directory_resolution_and_result_policy_pinning(self):
        pending = {'action_id':'attempted', 'status':'attempted', 'request': {'kind':'combat',
            'context': {'run_id':'fixture'}, 'decision_policy':'learning',
            'reading':{'context':{'decision_policy':'learning'}}}}
        session = {'schema':SESSION_SCHEMA,'run_id':'fixture','decision_policy':'learning','pending':pending}
        with TemporaryDirectory() as directory:
            path = Path(directory)/'state.json'; path.write_text(json.dumps(session))
            self.assertEqual(pending, read_pending(directory, 'attempted')[0])
            session['decision_policy'] = 'strict'; path.write_text(json.dumps(session))
            with self.assertRaisesRegex(ValueError, 'policy'):
                read_pending(directory, 'attempted')
        value = {'schema':'veda.combat-result.v1','action_id':'attempted','inventory':'unchanged',
                 'observed_result':'Synthetic boundary.', 'boundary':{}, 'decision_policy':'strict'}
        with self.assertRaisesRegex(ValueError, 'policy'):
            _result_draft(value, pending)

    def test_reasoning_diagnostic_is_specific(self):
        value = draft(); value['reasoning'] = 'x'*4097
        with self.assertRaisesRegex(ValueError, '4096 UTF-8 bytes'):
            validate_combat_draft(value)


class CompactLearningFlowTests(unittest.TestCase):
    def setUp(self):
        self.flow = combat_fixtures.CombatResultTests(methodName='runTest'); self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        fx = self.flow.fx; self.flow.session.close()
        self.flow.session = fx.session = ReviewedPlaySession(fx.root/'session', run_id=fx.context_ids['run_id'],
            telemetry=fx.telemetry, mode='codex', controller_factory=fx.factory, clock=lambda:fx.now,
            decision_policy='learning')
        self.flow.value['decision_policy'] = 'learning'

    def test_actual_card_effect_mismatch_reconciles_instead_of_replaying(self):
        flow = self.flow
        flow.fx.db.start_combat_zones(combat_id=flow.fx.context_ids['combat_id'], deck=['Strike','Defend'],
                                      hand=['Strike','Defend'], source='Synthetic policy opening')
        flow.value['ui'].update(phase='card_selected', focused_card_id='d', selected_card_id='d')
        prepared = flow.prepare_send(arm=True)
        state = deepcopy(flow.value['state']); state['hand'] = state['hand'][:1]
        state.update(energy=0, block=13, hp=39); state['piles']['discard'] = ['Defend']
        after = result(prepared['action_id'], focus='s', state=state)
        after['ui']['hand_order'] = ['s']; after['card_destination'] = 'discard'
        packet = flow.packet(after, result_mode=True)
        self.assertEqual('learning', packet['after']['decision_policy'])
        self.assertEqual('learning', packet['after']['reading']['context']['decision_policy'])
        response = flow.session.handle(packet)
        self.assertTrue(response['logical_action_complete'])
        self.assertIsNone(flow.session.state['pending'])
        self.assertEqual([['cross']], [c['buttons'] for c in flow.fx.controller.inputs])

    def test_unknown_end_turn_forecast_accepts_actual_next_turn(self):
        flow = self.flow
        flow.value['state']['unmodeled_effects'] = ['Uncatalogued enemy effect.']
        flow.value['ui']['focused_card_id'] = None
        flow.value['plan']['steps'] = [{'kind':'end_turn'}]
        prepared = flow.prepare_send(arm=True)
        self.assertIsNone(prepared['assessment']['forecast'])
        state = deepcopy(flow.value['state']); state.update(turn=2, energy=3, hp=37)
        after = result(prepared['action_id'], state=state, focus='s')
        after['next_turn'] = {'turn_number':2}
        packet = flow.packet(after, result_mode=True)
        response = flow.session.handle(packet)
        self.assertTrue(response['logical_action_complete']); self.assertIsNone(flow.session.state['pending'])
        self.assertEqual([['triangle']], [c['buttons'] for c in flow.fx.controller.inputs])


if __name__ == '__main__':
    unittest.main()
