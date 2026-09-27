"""Explicit synthetic reviews, temporary SQLite/PNGs and fake controller only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_reviewed_play import png_bytes, FakeController
from veda.execution import ARM_PHRASE
from veda.neow_registration import register_neow_attempt, write_neow_talk_request, write_neow_talk_result
from veda.play_context import read_play_context
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import ReviewedPlaySession
from veda.telemetry_database import TelemetryDatabase


class NeowRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'memory.sqlite3'
        self.db = TelemetryDatabase(self.path)
        self.old = self.db.start_or_resume_run(ascension=2)
        self.floor = self.db.record_floor(run_id=self.old, act=2, floor=33, node_type='boss', outcome=None)
        self.combat = self.db.start_combat(run_id=self.old, floor_id=self.floor, opening_state={},
                                         encounter_name='The Collector', encounter_type='boss')
        self.db.complete_combat(combat_id=self.combat, outcome='defeat', closing_state={'hp': 0})
        self.db.complete_floor(floor_id=self.floor, outcome='defeat', ending_state={'hp': 0})
        self.now = datetime.now(timezone.utc)
        self.review = self.make_review()

    def make_review(self, seconds=0):
        moment = self.now + timedelta(seconds=seconds)
        image = self.root / ('ps5_observation_' + moment.strftime('%Y%m%dT%H%M%S.%fZ') + '.png')
        image.write_bytes(png_bytes((seconds % 255, 20, 60)))
        image.with_suffix('.capture.json').write_text(json.dumps({
            'schema': 'veda.game-window-capture.v1', 'image_path': str(image.resolve()),
            'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'dimensions': [8, 8],
            'capture_requested_at': moment.isoformat(), 'capture_completed_at': moment.isoformat()}))
        return {'schema': 'veda.neow-review.v1', 'game': 'Slay the Spire',
            'capture': str(image), 'reviewer': 'Synthetic reviewer', 'review_complete': True,
            'evidence_note': 'Synthetic fixture; no game pixels recognized.',
            'facts': {'event_id': 'neow', 'event_phase': 'opening_dialogue', 'act': 1, 'floor': 0,
                      'character': 'Ironclad', 'ascension': 2, 'dialogue_text': 'Another try...?'},
            'resources': {'hp': 80, 'max_hp': 80, 'gold': 99, 'deck_size': 10},
            'inventory': {'items': [{'kind': 'relic', 'item': 'Burning Blood'}],
                          'coverage': {'card': 'unknown', 'relic': 'complete', 'potion': 'complete'}},
            'visible_options': [{'id': 'talk', 'label': '[Talk]', 'enabled': True, 'costs': {}}],
            'focused_id': 'talk', 'control_profile': 'ps5-default-cross-confirm-v1'}

    def register(self, **kwargs):
        return register_neow_attempt(self.path, self.review, **{
            'phrase': ARM_PHRASE, 'authorization_scope': 'current_visible_attempt',
            'previous_run_id': self.old, 'now': self.now, **kwargs})

    def count_runs(self):
        with self.db._connection() as con:
            return con.execute('SELECT COUNT(*) FROM runs').fetchone()[0]

    def journal(self, ident, pending=None):
        directory = self.root / 'reviewed-play' / ident
        directory.mkdir(parents=True)
        (directory / 'session.lock').touch()
        (directory / 'state.json').write_text(json.dumps({'schema': 'veda.reviewed-play.v1',
            'run_id': self.old if ident == 'CURRENT_RUN' else ident, 'pending': pending, 'completed': 0}))
        return directory

    def test_registration_preserves_defeat_and_starts_separate_partial_inventory(self):
        before = self.count_runs()
        result = self.register()
        self.assertEqual('registered_neow_attempt', result['status'])
        self.assertNotEqual(self.old, result['run_id'])
        self.assertEqual(before + 1, self.count_runs())
        with self.db._connection() as con:
            old = con.execute('SELECT status,metadata_json FROM runs WHERE id=?', (self.old,)).fetchone()
            self.assertEqual('completed', old['status'])
            self.assertEqual('defeat', json.loads(old['metadata_json'])['terminal_reconciliation']['outcome'])
            self.assertEqual('defeat', con.execute('SELECT outcome FROM combats WHERE id=?', (self.combat,)).fetchone()[0])
        inventory = self.db.inventory_ledger(run_id=result['run_id'])
        self.assertEqual([], inventory['current']['card'])
        self.assertEqual('unknown', inventory['coverage']['card'])
        self.assertEqual(['Burning Blood'], inventory['current']['relic'])
        self.assertFalse(result['armed']); self.assertFalse(result['controller_input_sent'])

    def test_retry_same_attempt_is_idempotent_even_after_new_capture(self):
        first = self.register()
        self.review = self.make_review(1)
        second = self.register(now=self.now + timedelta(seconds=1))
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual('existing_neow_attempt', second['status'])
        self.assertEqual(2, self.count_runs())

    def test_resume_only_or_missing_arming_cannot_rebind(self):
        for kwargs in ({'authorization_scope': 'resume_only'}, {'phrase': 'yes'}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'authorization'):
                self.register(**kwargs)
        self.assertEqual(1, self.count_runs())

    def test_hp_zero_or_room_victory_cannot_close_run(self):
        with self.db._connection() as con:
            con.execute("UPDATE floors SET outcome='victory'")
            con.execute("UPDATE combats SET outcome='victory'")
        with self.assertRaisesRegex(ValueError, 'corroborated terminal'):
            self.register()
        self.assertEqual(1, self.count_runs())

    def test_pending_journal_and_live_session_owner_block_registration(self):
        directory = self.journal(self.old, {'status': 'attempted'})
        with self.assertRaisesRegex(ValueError, 'pending or invalid'):
            self.register()
        self.assertEqual(1, self.count_runs())
        value = json.loads((directory / 'state.json').read_text()); value['pending'] = None
        (directory / 'state.json').write_text(json.dumps(value))
        with (directory / 'session.lock').open('r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'still owns'):
                self.register()
        self.assertEqual(1, self.count_runs())

    def test_unrelated_character_pending_cannot_be_hidden_by_new_run(self):
        other = self.db.start_or_resume_run(character_name='Silent', ascension=10)
        self.journal(other, {'status': 'attempted'})
        with self.assertRaisesRegex(ValueError, 'pending or invalid'):
            self.register()
        self.assertEqual(2, self.count_runs())

    def test_unrelated_character_database_pending_cannot_be_hidden(self):
        other = self.db.start_or_resume_run(character_name='Silent', ascension=10)
        event = self.db.record_event(run_id=other, kind='choice', phase='event', state={})
        with self.db._connection() as con:
            con.execute("INSERT INTO decisions (id,event_id,options_json,recommendation_json,reasoning,prediction_json,status) VALUES ('pending',?,'[]','{}','fixture','{}','recommended')", (event,))
        with self.assertRaisesRegex(ValueError, 'unresolved decisions'):
            self.register()
        self.assertEqual(2, self.count_runs())

    def test_generic_talk_reward_options_stale_or_unreviewed_source_rejected(self):
        cases = [('generic', lambda r: r['facts'].update(event_id='other')),
                 ('reward', lambda r: r['facts'].update(event_phase='reward_options')),
                 ('no review', lambda r: r.update(review_complete=False)),
                 ('cost', lambda r: r['visible_options'][0].update(costs={'hp': 1}))]
        original = deepcopy(self.review)
        for label, change in cases:
            self.review = deepcopy(original); change(self.review)
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.register()
        self.review = original
        with self.assertRaisesRegex(ValueError, 'capture_stale'):
            self.register(now=self.now + timedelta(seconds=31))
        self.assertEqual(1, self.count_runs())

    def test_source_tamper_rolls_back_old_run_update_and_new_run(self):
        def change_after_write(*args, **kwargs):
            # Patch acts on the helper's instance; preserve its shared transaction.
            value = original_method(*args, **kwargs)
            Path(self.review['capture']).write_bytes(png_bytes((222, 0, 0)))
            return value
        original_method = TelemetryDatabase.record_inventory_baseline
        with patch.object(TelemetryDatabase, 'record_inventory_baseline', change_after_write):
            with self.assertRaisesRegex(ValueError, 'capture_hash_mismatch'):
                self.register()
        self.assertEqual(1, self.count_runs())
        with self.db._connection() as con:
            self.assertEqual('active', con.execute('SELECT status FROM runs WHERE id=?', (self.old,)).fetchone()[0])

    def test_current_terminal_context_rejected_before_register_then_new_context_ready(self):
        self.assertEqual('needs_review', read_play_context(self.path, run_id=self.old)['selection_status'])
        result = self.register()
        self.assertEqual('selected', read_play_context(self.path, run_id=result['run_id'])['selection_status'])

    def test_talk_request_uses_new_run_and_plans_one_cross_through_real_adapter(self):
        result = self.register()
        self.review = self.make_review(2)
        self.now += timedelta(seconds=2)
        directory = Path(result['session_directory']); directory.mkdir(parents=True)
        output = directory / 'talk.json'
        write_neow_talk_request(self.path, self.review, run_id=result['run_id'], output=output, now=self.now)
        packet = json.loads(output.read_text())
        self.assertEqual(result['run_id'], packet['context']['run_id'])
        controller = FakeController()
        with ReviewedPlaySession(directory, run_id=result['run_id'], telemetry=PlayTelemetry(self.db),
                mode='codex', controller_factory=lambda: controller, clock=lambda: self.now) as session:
            self.assertEqual([], controller.inputs)
            session.arm({'phrase': ARM_PHRASE, 'run_id': result['run_id'], 'source': packet['source'],
                'review': packet['review'], 'frame_id': packet['observation']['frame']['frame_id'],
                'game': 'Slay the Spire', 'screen': 'event', 'exclusive_client_confirmed': True})
            prepared = session.prepare(packet)
            self.assertEqual(['cross'], prepared['command']['buttons'])
            session.send(prepared['action_id'])
            self.assertEqual(1, len(controller.inputs))
            with self.assertRaisesRegex(Exception, 'never repeat'):
                session.send(prepared['action_id'])
            after = self.make_review(3)
            self.now += timedelta(seconds=3)
            after['facts'].update(event_phase='reward_options', dialogue_text='Choose your gift.')
            after['visible_options'] = [{'id': 'gift', 'label': 'Synthetic gift', 'enabled': True, 'costs': {}}]
            after['focused_id'] = 'gift'
            verified_file = directory / 'verified.json'
            pointer = write_neow_talk_result(output, after, action_id=prepared['action_id'], observed_result='Synthetic options became visible.',
                                  output=verified_file, now=self.now)
            self.assertEqual({'request_file'}, set(pointer))
            verified = session.verify(json.loads(verified_file.read_text()))
            self.assertEqual('verified', verified['status'])
            self.assertIsNone(session.summary()['pending'])
            self.assertEqual(1, len(controller.inputs))

    def test_completed_legacy_pointer_is_preserved_without_blocking_new_binding(self):
        directory = self.journal('CURRENT_RUN')
        original = (directory / 'state.json').read_bytes()
        result = self.register()
        context = read_play_context(self.path, run_id=result['run_id'])
        self.assertEqual('selected', context['selection_status'])
        self.assertTrue(context['sessions']['locations'][0]['historical_closed_run'])
        self.assertEqual(original, (directory / 'state.json').read_bytes())

    def test_talk_rejects_wrong_game_and_unregistered_floor(self):
        result = self.register()
        for changes in ("game='Other game'", "metadata_json='{}'"):
            with self.db._connection() as con:
                con.execute('UPDATE runs SET ' + changes + ' WHERE id=?', (result['run_id'],))
            with self.assertRaises(ValueError):
                write_neow_talk_request(self.path, self.review, run_id=result['run_id'],
                    output=self.root / 'rejected.json', now=self.now)
            self.assertFalse((self.root / 'rejected.json').exists())
            with self.db._connection() as con:
                con.execute("UPDATE runs SET game='Slay the Spire' WHERE id=?", (result['run_id'],))

    def test_cli_pointer_goes_through_real_shadow_jsonl_transport(self):
        result = self.register()
        self.now = datetime.now(timezone.utc)
        self.review = self.make_review()
        review_path = self.root / 'review.json'
        review_path.write_text(json.dumps(self.review))
        packet_path = self.root / 'talk.json'
        helper = subprocess.run([sys.executable, 'scripts/veda_neow.py', '--database', str(self.path), 'talk',
            '--review', str(review_path), '--run-id', result['run_id'], '--output', str(packet_path)],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(0, helper.returncode, helper.stdout + helper.stderr)
        pointer = json.loads(helper.stdout)
        self.assertEqual({'request_file'}, set(pointer))
        messages = helper.stdout + json.dumps({'operation': 'cancel_prepared'}) + '\n' + json.dumps({'operation': 'stop'}) + '\n'
        adapter = subprocess.run([sys.executable, 'scripts/veda_reviewed_play.py', result['session_directory'],
            '--database', str(self.path), '--run-id', result['run_id'], '--mode', 'shadow'],
            input=messages, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, adapter.returncode, adapter.stderr)
        rows = [json.loads(line) for line in adapter.stdout.splitlines()]
        self.assertEqual(['ready_unarmed', 'prepared', 'cancelled_without_input', 'stopped'], [row['status'] for row in rows])
        self.assertFalse(rows[1]['controller_input_sent'])
        self.assertEqual(['cross'], rows[1]['command']['buttons'])
