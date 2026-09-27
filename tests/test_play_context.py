"""Temporary SQLite and saved JSON only. No controller, capture or real run writes."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from veda.play_context import MAX_JSON_BYTES, read_play_context
from veda.telemetry_database import TelemetryDatabase


class PlayContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'memory.sqlite3'
        self.db = TelemetryDatabase(self.path)
        self.run = self.db.start_or_resume_run(ascension=2)
        self.floor = self.db.record_floor(run_id=self.run, act=2, floor=33, node_type='boss', outcome=None)
        self.combat = self.db.start_combat(run_id=self.run, floor_id=self.floor, opening_state={},
                                         encounter_name='The Collector', encounter_type='boss')
        self.turn = self.db.start_combat_turn(combat_id=self.combat, turn_number=1, phase='player', opening_state={})
        self.now = datetime.now(timezone.utc)
        self.sessions = self.root / 'reviewed-play'

    def read(self, **kwargs):
        return read_play_context(self.path, now=self.now, **kwargs)

    def session(self, name, *, run=None, pending=None):
        directory = self.sessions / name; directory.mkdir(parents=True)
        value = {'schema': 'veda.reviewed-play.v1', 'run_id': run or self.run,
                 'pending': pending, 'completed': 3}
        (directory / 'state.json').write_text(json.dumps(value))
        return directory

    def pending(self, status='prepared', decision_id=None):
        return {'action_id': 'action-test', 'status': status, 'decision_id': decision_id,
                'request': {'kind': 'combat', 'context': {'run_id': self.run, 'floor_id': self.floor,
                    'combat_id': self.combat, 'turn_id': self.turn},
                    'source': {'path': '/saved/historical.png', 'sha256': 'a' * 64,
                               'captured_at': (self.now - timedelta(days=1)).isoformat()}}}

    def add_pending_decision(self, ident='decision-test'):
        event = self.db.record_event(run_id=self.run, floor_id=self.floor, combat_id=self.combat,
                    turn_id=self.turn, kind='play_attempt', phase='combat', state={}, payload={}, source='synthetic')
        with self.db._connection() as conn:
            conn.execute('INSERT INTO decisions (id,event_id,options_json,recommendation_json,reasoning,prediction_json,status) '
                         "VALUES (?,?,'[]','{}','fixture','{}','recommended')", (ident, event))
        return ident

    def test_unique_context_and_historical_authority_flags(self):
        result = self.read()
        self.assertEqual(result['selection_status'], 'selected')
        self.assertEqual(result['context']['binding'], {'run_id': self.run, 'floor_id': self.floor,
                                                       'combat_id': self.combat, 'turn_id': self.turn})
        self.assertTrue(result['historical_only'])
        for key in ('live', 'runtime_authorized', 'controller_authorized'):
            self.assertFalse(result[key])
        self.assertFalse(self.sessions.exists(), 'inspection must not create session directories')

    def test_missing_database_does_not_create_database_or_parent(self):
        path = self.root / 'missing' / 'memory.sqlite3'
        with self.assertRaisesRegex(ValueError, 'existing database'):
            read_play_context(path)
        self.assertFalse(path.parent.exists())

    def test_read_reuses_inventory_without_initialization_and_preserves_main_bytes(self):
        before = self.path.read_bytes()
        with patch.object(TelemetryDatabase, 'initialize', side_effect=AssertionError('must not initialize')):
            self.read()
        self.assertEqual(self.path.read_bytes(), before)

    def test_committed_wal_contents_are_read_without_business_writes(self):
        connection = sqlite3.connect(self.path); self.addCleanup(connection.close)
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('UPDATE runs SET character_name=? WHERE id=?', ('Silent', self.run))
        connection.commit()
        wal = Path(str(self.path) + '-wal')
        self.assertTrue(wal.is_file())
        before = (self.path.read_bytes(), wal.read_bytes())
        self.assertEqual(self.read()['run']['character_name'], 'Silent')
        self.assertEqual((self.path.read_bytes(), wal.read_bytes()), before)

    def test_future_inventory_reader_write_is_denied_by_connection(self):
        def attempted_write(reader, **kwargs):
            with reader._connection() as connection:
                connection.execute('DELETE FROM runs')
        before = self.path.read_bytes()
        with patch.object(TelemetryDatabase, 'inventory_ledger', attempted_write):
            with self.assertRaisesRegex(ValueError, 'read transaction unavailable'):
                self.read()
        self.assertEqual(self.path.read_bytes(), before)

    def test_old_open_combat_is_exposed_without_hiding_unique_current_floor_combat(self):
        floor = self.db.record_floor(run_id=self.run, act=2, floor=34, node_type='combat', outcome=None)
        combat = self.db.start_combat(run_id=self.run, floor_id=floor, opening_state={},
                                     encounter_name='Other', encounter_type='normal')
        result = self.read()
        self.assertEqual(result['context']['binding']['floor_id'], floor)
        self.assertEqual(result['context']['binding']['combat_id'], combat)
        self.assertEqual(result['context']['historical_open_combats'][0]['id'], self.combat)
        self.assertEqual(result['selection_status'], 'needs_review')

    def test_ambiguous_runs_need_explicit_selection_not_newest(self):
        other = self.db.start_or_resume_run(character_name='Silent', ascension=2)
        result = self.read()
        self.assertEqual(result['selection_status'], 'needs_review')
        self.assertIsNone(result['run'])
        self.assertEqual({r['id'] for r in result['run_candidates']}, {self.run, other})
        self.assertEqual(self.read(run_id=self.run)['run']['id'], self.run)

    def test_completed_run_is_not_selected_even_explicitly(self):
        with self.db._connection() as conn:
            conn.execute("UPDATE runs SET status='completed' WHERE id=?", (self.run,))
        self.assertIsNone(self.read(run_id=self.run)['run'])

    def test_multiple_open_combats_and_turns_are_not_silently_selected(self):
        other = self.db.start_combat(run_id=self.run, floor_id=self.floor, opening_state={},
                                    encounter_name='Other', encounter_type='boss')
        result = self.read()
        self.assertIsNone(result['context']['binding']['combat_id'])
        self.assertIn('multiple open combats', ' '.join(result['reasons']))
        with self.db._connection() as conn:
            conn.execute('UPDATE combats SET closed_at=? WHERE id=?', (self.now.isoformat(), other))
        self.db.start_combat_turn(combat_id=self.combat, turn_number=2, phase='player', opening_state={})
        result = self.read()
        self.assertIsNone(result['context']['binding']['turn_id'])
        self.assertIn('multiple open turns', ' '.join(result['reasons']))

    def test_floor_timestamp_tie_and_combat_floor_disagreement_are_visible(self):
        other = self.db.record_floor(run_id=self.run, act=2, floor=34, node_type='reward', outcome=None)
        result = self.read()
        self.assertEqual(result['context']['binding']['floor_id'], other)
        self.assertIsNone(result['context']['binding']['combat_id'])
        with self.db._connection() as conn:
            conn.execute('UPDATE floors SET recorded_at=? WHERE run_id=?', (self.now.isoformat(), self.run))
        result = self.read()
        self.assertIsNone(result['context']['binding']['floor_id'])
        self.assertEqual(len(result['context']['floor_candidates']), 2)

    def test_inventory_duplicate_counts_and_unknown_coverage_remain_truthful(self):
        self.db.record_inventory_baseline(run_id=self.run, floor_id=self.floor, source='synthetic',
            coverage={'card': 'partial', 'relic': 'unknown', 'potion': 'complete'},
            items=[{'kind': 'card', 'item': 'Strike'}, {'kind': 'card', 'item': 'Strike'},
                   {'kind': 'card', 'item': 'Defend+'}, {'kind': 'potion', 'item': 'Fire Potion'}])
        result = self.read()['inventory']['categories']
        self.assertEqual(result['card']['coverage'], 'partial')
        self.assertEqual(result['card']['recorded_count'], 3)
        self.assertIn({'name': 'Strike', 'count': 2}, result['card']['items'])
        self.assertEqual(result['relic']['coverage'], 'unknown')
        self.assertEqual(result['potion']['coverage'], 'complete')
        self.assertNotIn('baseline_history', json.dumps(result))

    def test_latest_evidence_and_checkpoint_summaries_omit_large_payload_and_mark_future(self):
        future = (self.now + timedelta(days=1)).isoformat()
        self.db.record_event(run_id=self.run, floor_id=self.floor, combat_id=self.combat, turn_id=self.turn,
            kind='advisory_snapshot', phase='combat', state={'hp': 44, 'hand': ['Strike'], 'private': 'x' * 100_000},
            payload={'long_history': ['ignored'] * 1000}, source='synthetic', screenshot_path='/saved/frame.png')
        self.db.record_session_checkpoint(run_id=self.run, floor_id=self.floor, combat_id=self.combat,
            kind='pause', boundary='combat', state={'hp': 44}, source='synthetic', observed_at=future)
        result = self.read()
        self.assertEqual(result['latest_advisory']['recorded_state'], {'hp': 44, 'recorded_hand_count': 1})
        self.assertEqual(result['latest_checkpoint']['timing_status'], 'future_record')
        self.assertFalse(result['latest_checkpoint']['live'])
        self.assertNotIn('"private"', json.dumps(result))
        self.assertLess(len(json.dumps(result)), 10_000)

    def test_both_matching_session_directories_are_reported_without_guessing(self):
        self.session('CURRENT_RUN', pending=self.pending())
        self.session(self.run)
        result = self.read()['sessions']
        self.assertEqual(len(result['locations']), 2)
        self.assertIsNone(result['selected_directory'])
        self.assertTrue(result['must_not_repeat'])
        self.assertIn('both session directories', ' '.join(result['reasons']))

    def test_foreign_current_run_is_visible_and_blocks_directory_selection(self):
        self.session('CURRENT_RUN', run='other-run')
        directory = self.session(self.run)
        result = self.read()['sessions']
        self.assertIsNone(result['selected_directory'])
        self.assertEqual(result['locations'][0]['run_id'], 'other-run')
        self.assertFalse(result['locations'][0]['matching_run'])

    def test_both_pristine_idle_directories_select_canonical_without_deleting_legacy(self):
        first = self.session('CURRENT_RUN'); second = self.session(self.run)
        for directory in (first, second):
            path = directory / 'state.json'; value = json.loads(path.read_text())
            value['completed'] = 0; path.write_text(json.dumps(value))
        before = (first / 'state.json').read_bytes()
        result = self.read()['sessions']
        self.assertEqual(result['selected_directory'], str(second.resolve()))
        self.assertTrue(result['legacy_idle_directory_exists'])
        self.assertEqual(result['reasons'], [])
        self.assertEqual((first / 'state.json').read_bytes(), before)

    def test_pending_attempt_and_database_decision_are_summarized_not_replayed(self):
        decision = self.add_pending_decision()
        directory = self.session(self.run, pending=self.pending('attempted', decision))
        before = (directory / 'state.json').read_bytes()
        result = self.read()
        self.assertEqual(result['unresolved_decisions']['count'], 1)
        self.assertTrue(result['sessions']['must_not_repeat'])
        self.assertEqual(result['sessions']['selected_directory'], str(directory.resolve()))
        self.assertEqual((directory / 'state.json').read_bytes(), before)
        self.assertEqual(result['sessions']['locations'][1]['pending']['source']['timing_status'], 'historical')

    def test_pending_database_and_session_mismatch_blocks_directory_choice(self):
        self.add_pending_decision()
        self.session(self.run, pending=self.pending('attempted', 'different-decision'))
        result = self.read()['sessions']
        self.assertIsNone(result['selected_directory'])
        self.assertTrue(result['must_not_repeat'])
        self.assertIn('differ', ' '.join(result['reasons']))

    def test_unreadable_or_oversized_session_is_not_treated_as_empty(self):
        directory = self.session('CURRENT_RUN')
        (directory / 'state.json').write_bytes(b'x' * (MAX_JSON_BYTES + 1))
        self.session(self.run)
        result = self.read()['sessions']
        self.assertIsNone(result['selected_directory'])
        self.assertTrue(result['must_not_repeat'])
        self.assertEqual(result['locations'][0]['status'], 'unreadable_or_invalid')

    def test_pathlike_run_id_rejected_without_inspection_outside_sessions(self):
        for value in ('../other', '/tmp/other', '..', True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(run_id=value)

    def test_unsupported_schema_is_never_migrated(self):
        with self.db._connection() as conn:
            conn.execute("UPDATE schema_metadata SET value='old' WHERE key='schema'")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'does not initialize or migrate'):
            self.read()
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_compact_output_and_error_exit(self):
        spec = importlib.util.spec_from_file_location('context_cli', Path(__file__).resolve().parents[1] / 'scripts/veda_play_context.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(['--database', str(self.path)])
        self.assertEqual(code, 0)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(stdout.getvalue())['run']['id'], self.run)
        stdout = io.StringIO()
        missing = self.root / 'missing.sqlite3'
        with redirect_stdout(stdout):
            code = cli.main(['--database', str(missing)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'error')
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
