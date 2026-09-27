"""Historical coverage tests only; no game, recognition or authority evidence."""
import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from veda.advisory import DIRECT, REVIEWED_SPECIAL_CARDS
from veda.autonomy_readiness import MAX_CHECKPOINT_BYTES, assess_automation_readiness, load_checkpoint
from veda.execution import RUNTIME_FIELDS

NOW = datetime(2026, 9, 27, 5, tzinfo=timezone.utc)


def historical_checkpoint():
    return {
        'run_id': 'synthetic-historical-run',
        'current_state': {'screen': 'map', 'act': 2, 'floor': 32, 'ascension': 2,
            'hp': 35, 'max_hp': 80, 'deck_size': 20, 'next_boss': 'The Collector',
            'potion_slots': ['Explosive Potion', None, None, None, None],
            'observed_at': '2026-09-27T04:04:46.448761+00:00'},
        'cards': ['Strike+'] * 4 + ['Defend+'] * 4 + ['Bash+', 'Hemokinesis+',
            'Power Through+', 'Shrug It Off', 'Shrug It Off', 'Headbutt', 'Anger',
            'Metallicize', 'Uppercut', 'True Grit', 'Warcry', 'Spot Weakness'],
        'relics': ['Burning Blood', "Neow's Lament", 'Anchor', 'Centennial Puzzle',
            'Juzu Bracelet', 'Bronze Scales', 'Red Mask', 'Shuriken', 'Bag of Preparation', 'Potion Belt'],
        'evidence_manifest': '/nonexistent/not-opened/evidence.json',
    }


def assess(checkpoint):
    return assess_automation_readiness(checkpoint, now=NOW)


class ReadinessTests(unittest.TestCase):
    def test_counts_preserve_exact_variants_and_copies_without_executability(self):
        checkpoint = historical_checkpoint()
        original = copy.deepcopy(checkpoint)
        report = assess(checkpoint)
        rows = {r['name']: r for r in report['cards']}
        self.assertEqual(rows['Strike+']['copies'], 4)
        self.assertEqual(rows['Shrug It Off']['copies'], 2)
        expected = sum(n in DIRECT or n in REVIEWED_SPECIAL_CARDS for n in checkpoint['cards'])
        self.assertEqual(report['coverage_counts'], {
            'recorded_cards': 20, 'unique_card_variants': 13, 'exact_effect_copies': expected,
            'routine_candidate_copies': expected, 'selection_followup_copies': 2,
            'executable_cards_established': 0})
        self.assertTrue(report['inventory_declaration']['card_list_count_matches_recorded_deck_size'])
        self.assertFalse(report['inventory_declaration']['complete_current_inventory_verified'])
        self.assertTrue(all(r['executable_from_checkpoint'] is False for r in rows.values()))
        self.assertEqual(checkpoint, original)
        self.assertEqual(set(report['runtime_validation_fields']), RUNTIME_FIELDS)

    def test_unreviewed_variants_are_not_promoted_by_base_name(self):
        report = assess({'cards': ['Power Through', 'Metallicize+', 'Uppercut+',
                                  'Warcry+', 'Spot Weakness+', 'True Grit++', ' strike+']})
        self.assertTrue(all(not r['exact_effect_registered'] for r in report['cards']))
        self.assertTrue(all(not r['routine_candidate_registered'] for r in report['cards']))
        self.assertEqual(report['coverage_counts']['routine_candidate_copies'], 0)

    def test_true_grit_checked_random_boundary_is_not_selected_exhaust_support(self):
        report = assess({'cards': ['True Grit', 'True Grit+']})
        base, upgraded = report['cards']
        self.assertTrue(base['routine_candidate_registered'])
        self.assertEqual(base['checked_support'], 'exact_boundary_effect')
        self.assertTrue(any('random' in c for c in base['conditions']))
        self.assertEqual(upgraded['checked_support'], 'type_and_boundary_guard_only')
        self.assertFalse(upgraded['routine_candidate_registered'])
        self.assertTrue(upgraded['selection_execution_implemented'])
        self.assertEqual(upgraded['selection_execution_scope'], 'reviewed choice contract only')
        self.assertFalse(base['executable_from_checkpoint'])

    def test_selection_and_passive_relic_scope_do_not_claim_full_effects(self):
        report = assess({'cards': ['Headbutt', 'Warcry', 'Dual Wield', 'Armaments'],
                         'relics': ['Shuriken', 'Red Mask', 'Potion Belt', 'Anchor', 'Unknown Relic']})
        self.assertTrue(all(r['selection_execution_implemented'] is True for r in report['cards']))
        self.assertIn('confirmed empty discard', ' '.join(report['cards'][0]['conditions']))
        self.assertEqual([r['routine_scope'] for r in report['relics']],
                         ['conditional_observed_state'] * 3 + ['routine_allowlist_only', 'unsupported_interaction'])
        self.assertTrue(all(r['currently_verified'] is False for r in report['relics']))

    def test_caller_authority_and_recent_timestamp_cannot_arm_or_validate(self):
        checkpoint = historical_checkpoint()
        for key in ('runtime_authorized', 'controller_authorized', 'armed', 'fresh', 'bridge_ready'):
            checkpoint[key] = True
            checkpoint['current_state'][key] = True
        checkpoint['calibration'] = {'all_fields': 1.0, 'runtime_authorized': True, 'total_frames': 999}
        checkpoint['current_state']['observed_at'] = NOW.isoformat()
        report = assess(checkpoint)
        self.assertTrue(set(checkpoint).intersection(report['ignored_authority_declarations']))
        for key in ('autonomy_ready', 'runtime_authorized', 'controller_authorized', 'automatic_recognition_complete'):
            self.assertIs(report[key], False)
        self.assertEqual(report['checkpoint']['age_status'], 'recorded_recently')
        self.assertIs(report['checkpoint']['freshness_verified'], False)
        self.assertIs(report['checkpoint']['historical_only'], True)
        gates = {b['id'] for b in report['blockers']}
        self.assertTrue({'complete_runtime_reader', 'independent_runtime_validation', 'per_run_arming',
                         'bridge_preflight', 'fresh_game_evidence', 'reviewed_execution_validation',
                         'reviewed_contracts'} <= gates)
        self.assertTrue({'potion_execution', 'selection_execution', 'noncombat_execution',
                         'runtime_ledger_lifecycle'}.isdisjoint(gates))

    def test_historical_missing_and_future_times_never_become_current(self):
        checkpoint = historical_checkpoint()
        report = assess(checkpoint)
        self.assertEqual(report['checkpoint']['age_status'], 'historical')
        self.assertGreater(report['checkpoint']['age_seconds'], 3000)
        checkpoint['current_state']['observed_at'] = '2026-09-28T00:00:00+00:00'
        self.assertEqual(assess(checkpoint)['checkpoint']['age_status'], 'future_timestamp')
        checkpoint['current_state'].pop('observed_at')
        checkpoint['saved_at'] = NOW.isoformat()
        self.assertIsNone(assess(checkpoint)['checkpoint']['age_seconds'])

    def test_missing_inventory_is_unknown_and_empty_is_only_recorded_empty(self):
        report = assess({})
        self.assertEqual(report['inventory_declaration']['cards'], 'missing')
        self.assertIsNone(report['coverage_counts']['recorded_cards'])
        self.assertIsNone(report['coverage_counts']['routine_candidate_copies'])
        report = assess({'cards': [], 'relics': [], 'current_state': {'potion_slots': [], 'deck_size': 1}})
        self.assertEqual(report['coverage_counts']['recorded_cards'], 0)
        self.assertFalse(report['inventory_declaration']['card_list_count_matches_recorded_deck_size'])
        self.assertFalse(report['inventory_declaration']['complete_current_inventory_verified'])

    def test_collector_scope_does_not_hide_missing_ascension_manifest(self):
        for ascension in (None, 1, 2):
            report = assess({'current_state': {'next_boss': 'The Collector', 'ascension': ascension}})
            gates = {b['id'] for b in report['blockers']}
            self.assertIn('collector_current_move_evidence', gates)
            self.assertEqual(report['boss_reference']['manifest_available'], ascension == 2)
            self.assertEqual(report['boss_reference']['conservative_bound_registered'], ascension == 2)
            self.assertFalse(report['boss_reference']['current_turn_bound_established'])
            self.assertEqual('recorded_boss_manifest' in gates, ascension != 2)
        report = assess({'current_state': {'floor': 32, 'ascension': 2}})
        self.assertIsNone(report['boss_reference']['name'])

    def test_potions_keep_slot_counts_without_claiming_controller_effects(self):
        report = assess({'current_state': {'potion_slots': ['Explosive Potion', None,
            'Explosive Potion', 'Fairy in a Bottle', 'Energy Potion']}})
        self.assertEqual([p['copies_in_record'] for p in report['potions']], [2, 1, 1])
        self.assertIn('not manually usable', report['potions'][1]['checked_scope'])
        self.assertEqual([p['controller_execution_implemented'] for p in report['potions']], [True, False, True])
        self.assertTrue(all(p['hardware_execution_validated'] is False for p in report['potions']))

    def test_implemented_reviewed_paths_do_not_satisfy_automatic_reader_or_live_gates(self):
        checkpoint = historical_checkpoint()
        checkpoint['implementation_inventory'] = [{'id': 'complete_runtime_reader', 'status': 'implemented'}]
        checkpoint['execution_paths'] = {'codex_reviewed': {'controller_authorized': True}}
        report = assess(checkpoint)
        implementations = {r['id']: r for r in report['implementation_inventory']}
        self.assertEqual(set(implementations), {'reviewed_choices', 'play_telemetry',
                                             'codex_reviewed_session', 'collector_a2_bound'})
        self.assertTrue(all(r['status'] == 'implemented' for r in implementations.values()))
        paths = report['execution_paths']
        self.assertEqual(paths['standalone_automatic']['implementation_status'], 'incomplete')
        self.assertFalse(paths['standalone_automatic']['calibration_established'])
        for path in paths.values():
            self.assertFalse(path['runtime_authorized'])
            self.assertFalse(path['automatic_recognition_complete'])
        self.assertFalse(paths['codex_reviewed']['controller_authorized'])
        self.assertFalse(paths['codex_reviewed']['hardware_execution_validated'])
        gates = {r['id']: r for r in report['blockers']}
        self.assertEqual(gates['complete_runtime_reader']['applies_to'], ['standalone_automatic'])
        self.assertEqual(gates['reviewed_execution_validation']['applies_to'], ['codex_reviewed'])

    def test_reviewed_selection_availability_does_not_add_unregistered_mechanics(self):
        report = assess({'cards': ['Headbutt', 'Warcry+', 'True Grit+', 'Armaments']})
        rows = {row['name']: row for row in report['cards']}
        self.assertTrue(all(row['selection_execution_implemented'] for row in rows.values()))
        self.assertTrue(rows['Headbutt']['routine_candidate_registered'])
        self.assertIn('confirmed empty discard', ' '.join(rows['Headbutt']['conditions']))
        for name in ('Warcry+', 'True Grit+', 'Armaments'):
            self.assertFalse(rows[name]['routine_candidate_registered'])
            self.assertFalse(rows[name]['exact_effect_registered'])
        self.assertEqual(report['coverage_counts']['executable_cards_established'], 0)
        self.assertEqual(set(next(row for row in report['blockers']
                                  if row['id'] == 'recorded_card_planner_gaps')['cards']),
                         {'Warcry+', 'True Grit+', 'Armaments'})

    def test_collector_bound_scope_retains_nonexact_and_ascension_limits(self):
        report = assess(historical_checkpoint())
        boss = report['boss_reference']
        self.assertEqual(boss['forecast_kind'], 'conservative_survival_bound')
        self.assertFalse(boss['full_encounter_simulator'])
        self.assertFalse(boss['identity_currently_verified'])
        reason = next(row['reason'] for row in report['blockers']
                      if row['id'] == 'collector_current_move_evidence')
        for scope in ('Buff', 'Mega Debuff', 'Spawn/Revive', 'Other Ascensions', 'future rolled moves'):
            self.assertIn(scope, reason)

    def test_malformed_or_unbounded_checkpoint_is_rejected(self):
        for doc in ([], {'current_state': []}, {'cards': 'Strike'}, {'cards': ['']},
                    {'cards': ['Strike'] * 301}, {'relics': [None]}, {'run_id': {}},
                    {'current_state': {'hp': True}}, {'current_state': {'hp': 81, 'max_hp': 80}},
                    {'current_state': {'ascension': 21}}, {'current_state': {'energy': float('nan')}},
                    {'current_state': {'observed_at': '2026-09-27T00:00:00'}},
                    {'current_state': {'potion_slots': [False]}}, {'extra': 'x' * MAX_CHECKPOINT_BYTES}):
            with self.subTest(doc=str(doc)[:80]), self.assertRaises(ValueError):
                assess(doc)
        with self.assertRaises(ValueError):
            assess_automation_readiness({}, now=datetime(2026, 9, 27))

    def test_assessment_does_not_call_external_services_or_follow_evidence_paths(self):
        with patch('subprocess.Popen', side_effect=AssertionError('no process')), \
             patch('socket.socket', side_effect=AssertionError('no network')), \
             patch('sqlite3.connect', side_effect=AssertionError('no database')):
            report = assess(historical_checkpoint())
        self.assertFalse(report['runtime_authorized'])


class ReadinessFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'check_automation_readiness.py'
        spec = importlib.util.spec_from_file_location('readiness_cli_test', script)
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def call(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = self.cli.main([str(a) for a in args])
        return code, json.loads(output.getvalue())

    def test_report_hash_binds_same_checkpoint_bytes_and_exit_zero_is_not_readiness(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / 'checkpoint.json', Path(directory) / 'report.json'
            raw = json.dumps(historical_checkpoint()).encode()
            source.write_bytes(raw)
            code, report = self.call([source, '--output', output])
            self.assertEqual(code, 0)
            self.assertFalse(report['autonomy_ready'])
            self.assertEqual(report['checkpoint_source']['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(report, json.loads(output.read_text()))
            self.assertEqual(source.read_bytes(), raw)

    def test_existing_output_and_source_are_never_overwritten(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / 'checkpoint.json', Path(directory) / 'report.json'
            source.write_text('{}')
            output.write_text('preserve previous report')
            for path in (source, output):
                before = path.read_bytes()
                code, error = self.call([source, '--output', path])
                self.assertEqual(code, 2)
                self.assertFalse(error['controller_authorized'])
                self.assertEqual(path.read_bytes(), before)

    def test_duplicate_keys_nonfinite_and_oversize_bytes_are_rejected(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'checkpoint.json'
            for raw in (b'{"cards":[],"cards":["Strike"]}', b'{"x":NaN}',
                        b'x' * (MAX_CHECKPOINT_BYTES + 1), b'\xff'):
                source.write_bytes(raw)
                with self.subTest(raw=raw[:30]), self.assertRaises((ValueError, UnicodeError)):
                    load_checkpoint(source)
                code, report = self.call([source])
                self.assertEqual(code, 2)
                self.assertFalse(report['autonomy_ready'])


if __name__ == '__main__':
    unittest.main()
