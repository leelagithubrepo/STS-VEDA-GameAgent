"""Missing metadata recovery in temporary SQLite; synthetic sources/fake inputs."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests import test_reviewed_play as fixtures
from veda.play_telemetry import validate_outcome_request, outcome_request_digest
from veda.reviewed_play import RuntimeStop, inventory_digest


class OutcomeMetadataRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ReviewedPlayTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        f.db.complete_combat_turn(turn_id=f.context_ids['turn_id'],closing_state={})
        f.db.complete_combat(combat_id=f.context_ids['combat_id'],outcome='victory',closing_state={})
        f.context_ids.update(combat_id=None,turn_id=None)
        baseline=f.db.record_inventory_baseline(run_id=f.context_ids['run_id'],floor_id=f.context_ids['floor_id'],
            items=[{'kind':'card','item':'Bash'}],coverage={'card':'complete','relic':'complete','potion':'complete'},source='Synthetic reviewed original Bash.')
        with f.db._connection() as db:
            db.execute('UPDATE inventory_baselines SET observed_at=? WHERE id=?',((f.base_time-timedelta(seconds=1)).isoformat(),baseline))
        f.before=f.choice_request(0)
        f.before['inventory']=f.db.inventory_ledger(run_id=f.context_ids['run_id'])
        f.before['observation']['inventory_digest']=inventory_digest(f.before['inventory'])
        f.before['observation']['ui']['options'][0]['label']='Bash'
        self.expected=deepcopy(f.before['inventory']);self.expected['current']['card']=['Bash+']
        f.before['choice']['postconditions']['inventory_digest']=inventory_digest(self.expected)
        f.create();f.session.handle(dict(f.arm_request(),screen='selection'))
        self.prepared=f.prepare();f.send(self.prepared)

    def verification(self, number=1, missing=True):
        f=self.f;f.now=f.base_time+timedelta(seconds=number)
        after=f.choice_after(self.prepared,number)
        after['inventory']=deepcopy(self.expected);after['observation']['inventory_digest']=inventory_digest(self.expected)
        event={'kind':'card','action':'replaced','item':'Bash','related_item':'Bash+'}
        if not missing:event['evidence_note']='Synthetic inspected Bash+ preview and confirmed transition; not card pixels in result.'
        changes={'inventory_events':[event]}
        after['mutation_review']={**after['review'],'changes':changes}
        return {'operation':'verify','operation_id':str(uuid4()),'action_id':self.prepared['action_id'],
            'after':after,'telemetry':changes}

    def legacy_pending(self):
        request=self.verification()
        # Simulate the exact pre-fix lifecycle: validation was deferred until
        # after the immutable verified_pending_log journal was already saved.
        with patch('veda.reviewed_play.validate_outcome_request',side_effect=lambda value:deepcopy(value)):
            with self.assertRaisesRegex(ValueError,'inventory event evidence'):
                self.f.session.handle(request)
        self.f.session.state['pending'].pop('outcome_verification',None)
        self.f.session._save()
        self.assertEqual('verified_pending_log',self.f.session.state['pending']['status'])
        return deepcopy(self.f.session.state['pending']['outcome_request'])

    def repair(self):
        pending=self.f.session.state['pending'];outcome=pending['outcome_request']
        return {'operation':'repair_outcome_metadata','repair_id':str(uuid4()),'action_id':pending['action_id'],
            'outcome_operation_id':outcome['operation_id'],'expected_outcome_sha256':outcome_request_digest(outcome),
            'inventory_event_notes':[{'index':0,'evidence_note':'Synthetic retained Bash+ preview and confirmed menu transition support this replacement.'}],
            'review':{'kind':'reviewed_retained_outcome_metadata','complete':True,'reviewer':'Synthetic recovery reviewer',
                'source':deepcopy(outcome['source']),'evidence_note':'Reviewed retained evidence and the already verified transition; repairing the missing note only.'}}

    def counts(self):
        with self.f.db._connection() as db:
            return (db.execute('SELECT COUNT(*) FROM inventory_events').fetchone()[0],
                db.execute("SELECT COUNT(*) FROM evidence_events WHERE kind='play_outcome'").fetchone()[0])

    def test_missing_note_fails_before_pending_log_and_corrected_fresh_verification_succeeds(self):
        f=self.f
        with self.assertRaisesRegex(ValueError,'inventory event evidence'):f.session.handle(self.verification())
        self.assertEqual('attempted',f.session.state['pending']['status'])
        self.assertNotIn('outcome_request',f.session.state['pending']);self.assertEqual((0,0),self.counts())
        result=f.session.handle(self.verification(2,missing=False))
        self.assertEqual('verified',result['status']);self.assertIsNone(f.session.state['pending'])
        self.assertEqual((1,1),self.counts());self.assertEqual(1,len(f.controller.inputs))
        self.assertEqual(['Bash+'],f.db.inventory_ledger(run_id=f.context_ids['run_id'])['current']['card'])

    def test_pure_outcome_validation_checks_deep_metadata_without_file_or_database_access(self):
        original=self.legacy_pending();complete=deepcopy(original)
        complete['inventory_events'][0]['evidence_note']='Synthetic reviewed note.'
        with patch('pathlib.Path.open',side_effect=AssertionError('source read')),patch('sqlite3.connect',side_effect=AssertionError('DB read')):
            self.assertEqual(complete,validate_outcome_request(complete))
            with self.assertRaisesRegex(ValueError,'inventory event evidence'):validate_outcome_request(original)
            for field,value in [('zone_events',[{'kind':'unknown'}]),('transitions',[{'kind':'end_turn','closing_state':{}}])]:
                bad=deepcopy(complete);bad[field]=value
                with self.subTest(field=field),self.assertRaises(ValueError):validate_outcome_request(bad)

    def test_legacy_repair_after_restart_is_historical_logging_with_original_source_and_audit(self):
        f=self.f;original=self.legacy_pending();f.session.close();f.create();f.now+=timedelta(days=1)
        repair=self.repair();before_inputs=len(f.controller.inputs)
        before_calls, before_factories = len(f.controller.calls), f.factories
        result=f.session.handle(repair)
        self.assertEqual('outcome_metadata_repaired',result['status']);self.assertFalse(result['controller_input_sent'])
        self.assertEqual(original,f.session.state['pending']['metadata_repair']['original_outcome_request'])
        self.assertEqual(original['source'],f.session.state['pending']['outcome_request']['source'])
        self.assertEqual((0,0),self.counts())
        f.session.close();f.create()
        self.assertTrue(f.session.handle(repair)['idempotent_replay'])
        final=f.session.handle({'operation':'finalize'})
        self.assertEqual('verified',final['status']);self.assertEqual((1,1),self.counts())
        self.assertEqual(before_inputs,len(f.controller.inputs));self.assertFalse(f.session.armed)
        self.assertEqual(before_calls,len(f.controller.calls));self.assertEqual(before_factories,f.factories)
        with f.db._connection() as db:
            row=db.execute("SELECT payload_json,observed_at FROM evidence_events WHERE kind='play_outcome'").fetchone()
        payload=json.loads(row[0]);self.assertEqual(original['source']['captured_at'],row[1])
        self.assertEqual(f.now.isoformat(),payload['receipt']['recorded_at'])
        self.assertEqual(original,payload['durable_verified_evidence']['metadata_repair']['original_outcome_request'])
        self.assertEqual(repair,payload['durable_verified_evidence']['metadata_repair']['request'])

    def test_repair_commit_response_failure_restarts_and_finalizes_exactly_once(self):
        f=self.f;self.legacy_pending();f.now+=timedelta(days=1);repair=self.repair();f.session.handle(repair)
        original=f.telemetry.record_outcome
        def commit_then_fail(*args,**kwargs):
            original(*args,**kwargs);raise OSError('synthetic lost response')
        with patch.object(f.telemetry,'record_outcome',side_effect=commit_then_fail),self.assertRaisesRegex(OSError,'lost response'):
            f.session.handle({'operation':'finalize'})
        self.assertEqual((1,1),self.counts());f.session.close();f.create();f.now+=timedelta(days=1)
        self.assertEqual('verified',f.session.handle({'operation':'finalize'})['status'])
        self.assertEqual((1,1),self.counts());self.assertEqual(1,len(f.controller.inputs))

    def test_repair_rejects_mismatched_ids_digest_source_review_and_arbitrary_effect_fields(self):
        f=self.f;self.legacy_pending();original=deepcopy(f.session.state['pending']);request=self.repair()
        changes=[lambda r:r.update(action_id=str(uuid4())),lambda r:r.update(outcome_operation_id=str(uuid4())),
            lambda r:r.update(expected_outcome_sha256='0'*64),lambda r:r.update(state={'hp':99}),
            lambda r:r['review'].update(complete=False),lambda r:r['review']['source'].update(sha256='0'*64),
            lambda r:r['inventory_event_notes'][0].update(index=True),lambda r:r['inventory_event_notes'][0].update(index=1),
            lambda r:r['inventory_event_notes'][0].update(evidence_note=''),
            lambda r:r['inventory_event_notes'][0].update(related_item='Other')]
        for change in changes:
            bad=deepcopy(request);change(bad)
            with self.subTest(change=change),self.assertRaises((ValueError,RuntimeStop)):f.session.handle(bad)
            self.assertEqual(original,f.session.state['pending']);self.assertEqual((0,0),self.counts())
        self.assertEqual(1,len(f.controller.inputs))

    def test_repair_never_replays_input_or_repairs_an_attempted_unverified_action(self):
        f=self.f
        with self.assertRaises(RuntimeStop):f.session.handle({'operation':'send','action_id':self.prepared['action_id']})
        self.legacy_pending();request=self.repair()
        f.session.state['pending']['status']='attempted'
        with self.assertRaisesRegex(RuntimeStop,'no matching verified'):f.session.handle(request)
        self.assertEqual(1,len(f.controller.inputs));self.assertEqual((0,0),self.counts())

    def test_changed_ledger_or_source_prevents_repair_without_mutating_journal(self):
        f=self.f;self.legacy_pending();request=self.repair();before=deepcopy(f.session.state['pending'])
        path=f.session.state['pending']['outcome_request']['source']['path']
        from pathlib import Path
        saved=Path(path).read_bytes();Path(path).write_bytes(fixtures.png_bytes((44,22,11)))
        with self.assertRaisesRegex(ValueError,'source hash'):f.session.handle(request)
        self.assertEqual(before,f.session.state['pending']);Path(path).write_bytes(saved)
        f.db.record_inventory_event(run_id=f.context_ids['run_id'],floor_id=f.context_ids['floor_id'],
            item_kind='potion',action='acquired',item_name='Fire Potion',source='Synthetic conflicting writer')
        with self.assertRaisesRegex(ValueError,'ledger/context changed'):f.session.handle(request)
        self.assertEqual(before,f.session.state['pending']);self.assertEqual(1,len(f.controller.inputs))

    def test_already_committed_outcome_cannot_be_repaired_under_the_same_operation_id(self):
        f=self.f;original=self.legacy_pending();repair=self.repair()
        complete=deepcopy(original);complete['inventory_events'][0]['evidence_note']='Existing confirmed note.'
        f.telemetry.record_outcome(complete,now=f.now)
        before=deepcopy(f.session.state['pending'])
        with self.assertRaisesRegex(ValueError,'already committed'):f.session.handle(repair)
        self.assertEqual(before,f.session.state['pending']);self.assertEqual((1,1),self.counts())

    def test_normal_unjournaled_stale_outcome_still_fails_freshness(self):
        f=self.f;original=self.legacy_pending();complete=deepcopy(original)
        complete['inventory_events'][0]['evidence_note']='Synthetic late note.'
        with self.assertRaisesRegex(ValueError,'stale'):
            f.telemetry.record_outcome(complete,now=f.now+timedelta(days=1))
        self.assertEqual((0,0),self.counts())

    def test_repair_does_not_replace_existing_notes_or_repair_twice_differently(self):
        f=self.f;self.legacy_pending();request=self.repair();f.session.handle(request)
        conflicting=deepcopy(request);conflicting['inventory_event_notes'][0]['evidence_note']='Different claim.'
        with self.assertRaisesRegex(RuntimeStop,'different metadata repair'):f.session.handle(conflicting)
        self.assertEqual((0,0),self.counts())
        self.assertEqual(1,len(f.controller.inputs))
