"""Narrow metadata repair on temporary SQLite/session/capture fixtures only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_play_context_repair import main
from tests.test_menu_requests import make_capture
from veda import play_context_repair as repair
from veda.play_context import read_play_context
from veda.play_requests import reviewed_capture_source
from veda.play_telemetry import outcome_request_digest
from veda.telemetry_database import TelemetryDatabase


class ContextRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = TelemetryDatabase(self.root / 'memory.sqlite3')
        self.run = self.db.start_or_resume_run(ascension=2)
        self.floor = self.db.record_floor(run_id=self.run, act=1, floor=0, node_type='event',
            outcome='completed', starting_state={'event_id': 'neow', 'floor': 0})
        self.combat = self.db.start_combat(run_id=self.run, floor_id=self.floor, encounter_name='Unknown',
            encounter_type='enemy', opening_state={'unknowns': ['Opening state not observed.']})
        self.turn = self.db.start_combat_turn(combat_id=self.combat, turn_number=1, phase='combat', opening_state={})
        self.ids = dict(run_id=self.run, floor_id=self.floor, combat_id=self.combat, turn_id=self.turn)
        self.advisory = self.db.record_decision(**self.ids, phase='combat', state={'floor': 0},
            options=[{'kind': 'card', 'card_id': 'c1'}], recommendation={'steps': [{'kind': 'card', 'card_id': 'c1'}]},
            reasoning='Synthetic advice only', prediction={})
        self.session = self.root / 'state.json'
        self.session.write_text(json.dumps({'schema': 'veda.reviewed-play.v1', 'run_id': self.run,
            'pending': None, 'completed': 10}))
        (self.root / 'session.lock').touch()
        self.now = datetime.now(timezone.utc) + timedelta(seconds=3)
        self.capture = make_capture(self.root, self.now-timedelta(milliseconds=200), index='a')
        self.backup = self.root / 'before.sqlite3'
        self.value = {'schema': repair.SCHEMA, 'expected': self.ids,
            'supersede_advisory_decision_id': self.advisory, 'repair_neow_floor': True, 'neow_exit_event_id': None,
            'observed': {'screen': 'combat', 'act': 1, 'floor': 1, 'ascension': 2, 'character': 'Ironclad',
                         'hp': 80, 'max_hp': 80, 'energy': 2, 'block': 0, 'gold': 99,
                         'unknowns': ['Enemy identities and opening hand are unconfirmed.']},
            'reasoning': 'Correct synthetic metadata only; physical gameplay execution is not asserted.'}

    def rows(self, table):
        with self.db._connection() as con:
            return [dict(row) for row in con.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]

    def apply(self, value=None, **kwargs):
        params = dict(database=self.db.path, session=self.session, backup=self.backup, capture=self.capture,
            reviewer='Fixture', evidence_note='Synthetic exact reviewed combat HUD.', reviewed=True, now=self.now)
        params.update(kwargs)
        return repair.apply_context_repair(value or self.value, **params)

    def test_read_only_preflight_needs_no_capture_and_changes_nothing(self):
        before = self.rows('floors'), self.rows('decisions'), self.rows('evidence_events'), self.session.read_bytes()
        result = repair.validate_context_repair(self.value, database=self.db.path, session=self.session)
        self.assertTrue(result['draft_valid']); self.assertTrue(result['validation_only'])
        self.assertFalse(self.backup.exists())
        self.assertEqual(before, (self.rows('floors'), self.rows('decisions'), self.rows('evidence_events'), self.session.read_bytes()))

    def test_atomic_repair_preserves_original_facts_evidence_and_backup(self):
        old_floor = self.rows('floors')[0]; old_combat = self.rows('combats')[0]
        events = self.rows('evidence_events'); turns = self.rows('combat_turns'); session = self.session.read_bytes()
        result = self.apply(); new = result['next_context']
        self.assertFalse(result['runtime_authorized']); self.assertFalse(result['controller_input_sent'])
        self.assertNotEqual(self.floor, new['floor_id'])
        self.assertEqual(old_floor, self.rows('floors')[0]); self.assertEqual(turns, self.rows('combat_turns'))
        self.assertEqual(dict(old_combat, floor_id=new['floor_id']), self.rows('combats')[0])
        self.assertEqual(events, self.rows('evidence_events')[:-1]); self.assertEqual(session, self.session.read_bytes())
        floor = self.rows('floors')[1]
        self.assertEqual('{}', floor['starting_state_json']); self.assertEqual('{}', floor['ending_state_json'])
        self.assertFalse(json.loads(floor['summary_json'])['opening_state_known'])
        decision = self.rows('decisions')[0]
        self.assertEqual('skipped', decision['status'])
        self.assertEqual('unknown', json.loads(decision['actual_outcome_json'])['physical_action_execution'])
        audit = json.loads(self.rows('evidence_events')[-1]['payload_json'])
        self.assertEqual(self.ids, audit['old_context']); self.assertEqual(new, audit['new_context'])
        self.assertEqual('unknown', audit['physical_action_execution'])
        self.assertEqual(hashlib.sha256(self.backup.read_bytes()).hexdigest(), result['backup']['sha256'])
        with sqlite3.connect(self.backup) as backup:
            self.assertEqual(1, backup.execute('SELECT count(*) FROM floors').fetchone()[0])
            self.assertEqual('recommended', backup.execute('SELECT status FROM decisions').fetchone()[0])
        with self.assertRaisesRegex(ValueError, 'floor is no longer latest'):
            self.apply(backup=self.root / 'second.sqlite3')

    def test_advisory_only_can_be_retired_after_context_is_already_correct(self):
        with self.db._connection() as con:
            con.execute('UPDATE floors SET floor=1,node_type=\'enemy\' WHERE id=?', (self.floor,))
        value = deepcopy(self.value); value['repair_neow_floor'] = False
        result = self.apply(value)
        self.assertEqual(self.ids, result['next_context']); self.assertEqual(1, len(self.rows('floors')))

    def test_floor_only_does_not_require_an_advisory(self):
        self.db.resolve_decision(decision_id=self.advisory, chosen_action={}, actual_outcome={}, status='skipped')
        value = deepcopy(self.value); value['supersede_advisory_decision_id'] = None
        self.assertEqual('corrected', self.apply(value)['status'])

    def test_adapter_provenance_even_nested_never_treated_as_advisory(self):
        for change in ({'source': 'play_telemetry:reviewer'}, {'metadata': {'request': {}}}):
            with self.subTest(change=change):
                with self.db._connection() as con:
                    row = con.execute('SELECT e.* FROM evidence_events e JOIN decisions d ON d.event_id=e.id WHERE d.id=?', (self.advisory,)).fetchone()
                    old = dict(row)
                    if 'source' in change:
                        con.execute('UPDATE evidence_events SET source=? WHERE id=?', (change['source'], row['id']))
                    else:
                        payload = json.loads(row['payload_json']); payload['evidence'] = change['metadata']
                        con.execute('UPDATE evidence_events SET payload_json=? WHERE id=?', (json.dumps(payload), row['id']))
                with self.assertRaisesRegex(ValueError, 'advisory-only provenance'):
                    self.apply()
                self.assertFalse(self.backup.exists()); self.assertEqual(1, len(self.rows('floors')))
                with self.db._connection() as con:
                    con.execute('UPDATE evidence_events SET source=?,payload_json=? WHERE id=?', (old['source'], old['payload_json'], old['id']))

    def test_other_recommended_record_or_session_pending_blocks_all_writes(self):
        state = json.loads(self.session.read_text()); state['pending'] = {'status': 'attempted', 'action_id': 'unknown'}
        self.session.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'idle session'):
            self.apply()
        state['pending'] = None; self.session.write_text(json.dumps(state))
        self.db.record_decision(**self.ids, phase='combat', state={}, options=[], recommendation={}, reasoning='Other', prediction={})
        with self.assertRaisesRegex(ValueError, 'unexpected unresolved'):
            self.apply()
        self.assertFalse(self.backup.exists()); self.assertEqual(1, len(self.rows('floors')))

    def test_live_owner_and_stale_context_cas_block_before_backup(self):
        with (self.root / 'session.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'has an owner'):
                self.apply()
        value = deepcopy(self.value); value['expected']['turn_id'] = 'old-turn'
        with self.assertRaisesRegex(ValueError, 'latest sole open turn'):
            self.apply(value)
        self.assertFalse(self.backup.exists())

    def test_duplicate_floor_or_wrong_neow_or_unknown_exit_are_not_repaired(self):
        with self.db._connection() as con:
            con.execute('UPDATE floors SET outcome=NULL WHERE id=?', (self.floor,))
        with self.assertRaisesRegex(ValueError, 'verified Neow Leave'):
            self.apply()
        self.db.record_floor(run_id=self.run, act=1, floor=1, node_type='enemy', outcome=None)
        with self.assertRaisesRegex(ValueError, 'floor is no longer latest'):
            self.apply()
        self.assertFalse(self.backup.exists())

    def neow_exit(self):
        captured = self.now-timedelta(seconds=60)
        image = make_capture(self.root, captured, index='b')
        checked = reviewed_capture_source(capture=image, reviewer='Fixture', evidence_note='Synthetic Leave to map.',
            reviewed=True, now=captured+timedelta(seconds=1))
        ids = dict(self.ids, combat_id=None, turn_id=None)
        action = {'kind': 'choice', 'step_kind': 'commit', 'choice': {'choice_id': 'neow-leave',
                  'option_ids': ['leave'], 'review': {'frame_id': 'before-frame'}}}
        decision = self.db.record_decision(**ids, phase='event', state={}, options=[], recommendation=action,
            reasoning='Leave', prediction={}, source='play_telemetry:reviewer')
        state = {'facts': {'act': 1, 'floor': 0, 'event_id': 'neow'}, 'ui': {'screen': 'map'}}
        request = {'status': 'verified', 'context': ids, 'state': state, 'source': checked['source'],
                   'operation_id': 'result-operation', 'decision_id': decision}
        digest = outcome_request_digest(request)
        ident = self.db.record_event(**ids, kind='play_outcome', phase='event', state=state,
            source='play_telemetry:reviewer')
        payload = {'request': request, 'request_sha256': digest, 'play_operation_id': 'result-operation',
            'receipt': {'status': 'verified', 'unresolved': False, 'event_id': ident, 'decision_id': decision,
                        'source': checked['source'], 'context': ids, 'next_context': ids, 'kind': 'outcome',
                        'operation_id': 'result-operation', 'schema': 'veda.play-telemetry-receipt.v1'},
            'durable_verified_evidence': {'source': checked['source'], 'action_id': 'leave', 'outcome_sha256': digest,
                'basis': 'fresh_verified_result', 'review': dict(checked['review'], outcome={'action_id': 'leave',
                    'choice_id': 'neow-leave', 'option_ids': ['leave'], 'before_frame_id': 'before-frame', 'before_sha256': 'b'*64})}}
        dr = {'operation_id': 'leave', 'context': ids, 'action': action, 'source': {'sha256': 'b'*64}}
        dp = {'request': dr, 'request_sha256': outcome_request_digest(dr), 'play_operation_id': 'leave',
              'receipt': {'operation_id': 'leave', 'context': ids, 'kind': 'decision', 'decision_id': decision}}
        self.db.resolve_decision(decision_id=decision, chosen_action=action,
            actual_outcome={'status': 'verified', 'event_id': ident, 'source': checked['source'], 'state': state})
        with self.db._connection() as con:
            con.execute('UPDATE evidence_events SET payload_json=?,observed_at=? WHERE id=?',
                (json.dumps(payload), captured.isoformat(), ident))
            con.execute('UPDATE floors SET outcome=NULL WHERE id=?', (self.floor,))
            con.execute('UPDATE evidence_events SET payload_json=? WHERE id=(SELECT event_id FROM decisions WHERE id=?)',
                (json.dumps(dp), decision))
        self.value['neow_exit_event_id'] = ident
        return image, ident

    def test_retained_verified_exit_supports_null_legacy_floor_without_rewriting_it(self):
        _, ident = self.neow_exit()
        before = self.rows('floors')[0]
        result = self.apply()
        self.assertEqual(before, self.rows('floors')[0])
        audit = json.loads(self.rows('evidence_events')[-1]['payload_json'])
        self.assertEqual(ident, audit['neow_exit_evidence']['event_id'])
        self.assertNotEqual(self.floor, result['next_context']['floor_id'])

    def test_tampered_retained_exit_is_not_completion_evidence(self):
        image, _ = self.neow_exit(); image.write_bytes(image.read_bytes()+b'x')
        with self.assertRaises((ValueError, OSError)):
            self.apply()
        self.assertFalse(self.backup.exists())

    def test_different_resolved_decision_or_receipt_identity_cannot_prove_neow_exit(self):
        _, ident = self.neow_exit()
        with self.db._connection() as con:
            original = json.loads(con.execute('SELECT payload_json FROM evidence_events WHERE id=?', (ident,)).fetchone()[0])
        other = self.db.record_decision(**self.ids, phase='combat', state={}, options=[], recommendation={}, reasoning='Other', prediction={})
        self.db.resolve_decision(decision_id=other, chosen_action={}, actual_outcome={})
        for change in ({'decision_id': other}, {'context': dict(self.ids, run_id='different-run')}, {'kind': 'decision'}):
            value = deepcopy(original); value['receipt'].update(change)
            with self.db._connection() as con:
                con.execute('UPDATE evidence_events SET payload_json=? WHERE id=?', (json.dumps(value), ident))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.apply()
        self.assertFalse(self.backup.exists())

    def test_rehashed_proof_cannot_link_to_a_resolved_decision_from_another_run(self):
        _, ident = self.neow_exit()
        run = self.db.start_or_resume_run(ascension=3)
        floor = self.db.record_floor(run_id=run, act=1, floor=0, node_type='event', outcome=None)
        other = self.db.record_decision(run_id=run, floor_id=floor, phase='event', state={}, options=[],
            recommendation={}, reasoning='Unrelated run', prediction={}, source='play_telemetry:reviewer')
        self.db.resolve_decision(decision_id=other, chosen_action={}, actual_outcome={})
        with self.db._connection() as con:
            value = json.loads(con.execute('SELECT payload_json FROM evidence_events WHERE id=?', (ident,)).fetchone()[0])
            value['receipt']['decision_id'] = value['request']['decision_id'] = other
            value['request_sha256'] = value['durable_verified_evidence']['outcome_sha256'] = outcome_request_digest(value['request'])
            con.execute('UPDATE evidence_events SET payload_json=? WHERE id=?', (json.dumps(value), ident))
        with self.assertRaisesRegex(ValueError, 'exact run and floor'):
            self.apply()
        self.assertFalse(self.backup.exists())

    def test_exact_review_freshness_and_backup_are_required(self):
        for change in ({'reviewed': False}, {'now': self.now+timedelta(seconds=31)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.apply(**change)
        self.backup.write_text('already exists')
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual('already exists', self.backup.read_text())
        self.assertEqual(1, len(self.rows('floors')))
        self.assertEqual('recommended', self.rows('decisions')[0]['status'])

    def test_transaction_rolls_back_both_corrections_if_final_source_changes(self):
        original = repair.reviewed_capture_source; calls = 0
        def source(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ValueError('source changed')
            return original(**kwargs)
        with patch.object(repair, 'reviewed_capture_source', side_effect=source):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                self.apply()
        self.assertTrue(self.backup.is_file())
        self.assertEqual(1, len(self.rows('floors')))
        self.assertEqual('recommended', self.rows('decisions')[0]['status'])
        self.assertEqual(self.floor, self.rows('combats')[0]['floor_id'])
        self.assertFalse(any(r['kind'] == 'play_context_correction' for r in self.rows('evidence_events')))

    def test_session_changed_during_backup_cannot_commit(self):
        original = repair._backup
        def backup(*args):
            result = original(*args)
            value = json.loads(self.session.read_text()); value['completed'] += 1
            self.session.write_text(json.dumps(value))
            return result
        with patch.object(repair, '_backup', side_effect=backup):
            with self.assertRaisesRegex(ValueError, 'session changed'):
                self.apply()
        self.assertEqual(1, len(self.rows('floors')))
        self.assertEqual('recommended', self.rows('decisions')[0]['status'])

    def test_mismatched_observed_identity_and_invented_opening_fields_rejected(self):
        for field, bad in [('floor', 2), ('act', 2), ('ascension', 3), ('character', 'Silent'), ('hp', 0)]:
            value = deepcopy(self.value); value['observed'][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.apply(value)
        value = deepcopy(self.value); value['observed']['enemy'] = 'Slime'
        with self.assertRaisesRegex(ValueError, 'combat HUD'):
            self.apply(value)
        self.assertFalse(self.backup.exists())

    def test_cli_defaults_to_read_only_and_requires_complete_apply_arguments(self):
        draft = self.root / 'draft.json'; draft.write_text(json.dumps(self.value))
        args = ['--draft', str(draft), '--database', str(self.db.path), '--session', str(self.session)]
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(0, main(args)); self.assertTrue(json.loads(output.getvalue())['validation_only'])
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(2, main(args+['--apply']))
        self.assertFalse(self.backup.exists())

    def closed_first_floor(self):
        self.db.resolve_decision(decision_id=self.advisory, chosen_action={}, actual_outcome={})
        closing_capture = make_capture(self.root, self.now-timedelta(seconds=2), index='c')
        closing = {'screen': 'map', 'hp': 79, 'max_hp': 80, 'gold': 113, 'screenshot': str(closing_capture)}
        self.db.complete_combat(combat_id=self.combat, outcome='victory', closing_state=closing,
            summary={'note': 'Synthetic reward collected; no map path chosen.'})
        stamp = (self.now-timedelta(seconds=1)).isoformat()
        with self.db._connection() as con:
            con.execute('UPDATE combats SET closed_at=? WHERE id=?', (stamp, self.combat))
        value = deepcopy(self.value)
        value['expected'] = dict(self.ids, combat_id=None, turn_id=None)
        value['closed_first_floor'] = {'combat_id': self.combat, 'orphaned_turn_id': self.turn, 'expected_closed_at': stamp}
        value['supersede_advisory_decision_id'] = None
        value['observed'] = {'screen': 'map', 'act': 1, 'floor': 1, 'ascension': 2, 'character': 'Ironclad',
            'hp': 79, 'max_hp': 80, 'gold': 113, 'deck_size': 11, 'route_graph_complete': False,
            'route_choice_committed': False, 'unknowns': ['Full route and boss are not visible.',
            'The actual number of combat turns and final turn state are not established.']}
        return value, closing_capture

    def test_closed_victory_repair_preserves_history_closes_only_orphan_and_keeps_route_uncommitted(self):
        self.neow_exit()
        value, _ = self.closed_first_floor()
        floor = self.rows('floors')[0]; combat = self.rows('combats')[0]; turn = self.rows('combat_turns')[0]
        events = self.rows('evidence_events'); decisions = self.rows('decisions')
        self.assertTrue(repair.validate_context_repair(value, database=self.db.path, session=self.session)['draft_valid'])
        result = self.apply(value); context = result['next_context']
        self.assertIsNone(context['combat_id']); self.assertIsNone(context['turn_id'])
        self.assertEqual(floor, self.rows('floors')[0])
        self.assertEqual(events, self.rows('evidence_events')[:-1]); self.assertEqual(decisions, self.rows('decisions'))
        self.assertEqual(dict(combat, floor_id=context['floor_id']), self.rows('combats')[0])
        finished = self.rows('combat_turns')[0]
        self.assertEqual(combat['closed_at'], finished['closed_at'])
        self.assertEqual(turn['opening_state_json'], finished['opening_state_json'])
        self.assertEqual(turn['closing_state_json'], finished['closing_state_json'])
        self.assertEqual(turn['turn_number'], finished['turn_number'])
        closure = json.loads(finished['summary_json'])['administrative_closure']
        self.assertEqual('unknown', closure['actual_number_of_turns'])
        self.assertEqual('unknown', closure['actual_final_turn_state'])
        fresh_floor = self.rows('floors')[1]
        self.assertEqual('victory', fresh_floor['outcome']); self.assertEqual('{}', fresh_floor['starting_state_json'])
        self.assertFalse(json.loads(fresh_floor['ending_state_json'])['observed_map_after_victory']['route_choice_committed'])
        audit = self.rows('evidence_events')[-1]
        self.assertEqual('map', audit['phase']); self.assertIsNone(audit['combat_id']); self.assertIsNone(audit['turn_id'])
        payload = json.loads(audit['payload_json']); self.assertEqual(turn, payload['previous_orphaned_turn'])
        recorded = read_play_context(self.db.path, run_id=self.run)
        self.assertEqual(context, recorded['context']['binding'])
        self.assertEqual(1, recorded['context']['floor']['floor'])
        self.assertIsNone(recorded['context']['combat']); self.assertIsNone(recorded['context']['turn'])
        self.assertEqual([], recorded['context']['reasons'])
        with sqlite3.connect(self.backup) as backup:
            self.assertIsNone(backup.execute('SELECT closed_at FROM combat_turns').fetchone()[0])

    def test_closed_variant_cannot_fix_open_or_different_outcome_or_closure(self):
        value, _ = self.closed_first_floor()
        for updates in ({'outcome': 'defeat'}, {'closed_at': None}, {'closed_at': self.now.isoformat()}):
            old = self.rows('combats')[0]
            with self.db._connection() as con:
                for key, entry in updates.items():
                    con.execute('UPDATE combats SET ' + key + '=? WHERE id=?', (entry, self.combat))
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, 'sole completed first victory'):
                self.apply(value)
            with self.db._connection() as con:
                con.execute('UPDATE combats SET outcome=?,closed_at=? WHERE id=?', (old['outcome'], old['closed_at'], self.combat))
        self.assertFalse(self.backup.exists())

    def test_closed_variant_requires_exact_orphan_no_next_node_and_no_invented_energy(self):
        value, _ = self.closed_first_floor()
        for update in ({'route_choice_committed': True}, {'route_graph_complete': True}, {'energy': 1}):
            changed = deepcopy(value); changed['observed'].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.apply(changed)
        changed = deepcopy(value); changed['closed_first_floor']['orphaned_turn_id'] = 'another-turn'
        with self.assertRaisesRegex(ValueError, 'sole open turn'):
            self.apply(changed)
        with self.db._connection() as con:
            con.execute('UPDATE combat_turns SET closing_state_json=? WHERE id=?', ('{"energy":0}', self.turn))
        with self.assertRaisesRegex(ValueError, 'unclassified ending evidence'):
            self.apply(value)
        self.assertFalse(self.backup.exists())

    def test_closed_map_proof_needs_intact_receipt_and_matching_hud(self):
        value, image = self.closed_first_floor()
        changed = deepcopy(value); changed['observed']['gold'] += 1
        with self.assertRaisesRegex(ValueError, 'matching observed resources'):
            self.apply(changed)
        receipt_path = image.with_suffix('.capture.json')
        receipt = json.loads(receipt_path.read_text()); receipt['image_sha256'] = 'a'*64
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'capture provenance'):
            self.apply(value)
        self.assertFalse(self.backup.exists())

    def test_closed_repair_rolls_back_floor_combat_and_orphan_when_commit_review_fails(self):
        value, _ = self.closed_first_floor()
        before = self.rows('floors'), self.rows('combats'), self.rows('combat_turns'), self.rows('evidence_events')
        original = repair.reviewed_capture_source; calls = 0
        def source(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ValueError('changed final capture')
            return original(**kwargs)
        with patch.object(repair, 'reviewed_capture_source', side_effect=source):
            with self.assertRaisesRegex(ValueError, 'changed final capture'):
                self.apply(value)
        self.assertEqual(before, (self.rows('floors'), self.rows('combats'), self.rows('combat_turns'), self.rows('evidence_events')))
        self.assertTrue(self.backup.is_file())


if __name__ == '__main__':
    unittest.main()
