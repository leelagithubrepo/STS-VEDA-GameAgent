"""Recovery packaging CLI against synthetic journal/images and temporary SQLite."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts import veda_recover_outcome as recovery
from scripts import veda_reviewed_play as adapter_cli
from tests import test_outcome_metadata_recovery as fixtures
from tests import test_reviewed_play as images
from veda.play_telemetry import outcome_request_digest
from veda.reviewed_play import MAX_BYTES


class OutcomeRecoveryCliTests(unittest.TestCase):
    def setUp(self):
        self.g=fixtures.OutcomeMetadataRecoveryTests();self.g.setUp();self.addCleanup(self.g.doCleanups)
        self.f=self.g.f;self.original=self.g.legacy_pending()
        self.session=self.f.session.path;self.initial=self.session.read_bytes()
        self.output=self.f.root/'recovery-request.json'
        self.note='Reviewed the retained Bash+ preview and verified confirmation transition; result frame alone does not display card pixels.'
        self.kwargs={'session':self.session,'action_id':self.f.session.state['pending']['action_id'],
            'reviewer':'Synthetic independent reviewer','evidence_note':'Reviewed retained before/result evidence; missing metadata only.',
            'notes':[{'index':0,'evidence_note':self.note}],'reviewed':True,'output':self.output}

    def package(self, **overrides):
        return recovery.package(**{**self.kwargs,**overrides})

    def argv(self, **overrides):
        args={**self.kwargs,**overrides}
        result=['--session',str(args['session']),'--action-id',args['action_id'],'--reviewer',args['reviewer'],
            '--evidence-note',args['evidence_note'],'--output',str(args['output'])]
        for note in args['notes']:result+=['--inventory-event-note',str(note['index']),note['evidence_note']]
        if args['reviewed']:result.append('--reviewed')
        return result

    def test_package_exact_ids_sources_notes_and_cas_without_journal_or_database_writes(self):
        with patch('sqlite3.connect',side_effect=AssertionError('helper touched DB')), \
             patch('veda.bridge_client.BridgeClient',side_effect=AssertionError('helper touched bridge')):
            result=self.package()
        self.assertEqual({'request_file':str(self.output.resolve())},result)
        value=json.loads(self.output.read_bytes())
        self.assertEqual('repair_outcome_metadata',value['operation'])
        self.assertEqual(self.kwargs['action_id'],value['action_id'])
        self.assertEqual(self.original['operation_id'],value['outcome_operation_id'])
        self.assertEqual(outcome_request_digest(self.original),value['expected_outcome_sha256'])
        self.assertEqual(self.kwargs['notes'],value['inventory_event_notes'])
        self.assertEqual(self.original['source'],value['review']['source'])
        self.assertEqual('reviewed_retained_outcome_metadata',value['review']['kind'])
        self.assertEqual(self.initial,self.session.read_bytes())
        self.assertEqual((0,0),self.g.counts())
        self.assertNotIn('source',value);self.assertNotIn('state',value)
        self.assertNotIn('evidence_note',self.original['inventory_events'][0])

    def test_cli_requires_explicit_retained_evidence_review(self):
        output=io.StringIO()
        with redirect_stdout(output):code=recovery.main(self.argv(reviewed=False))
        self.assertEqual(2,code);report=json.loads(output.getvalue())
        self.assertEqual('needs_review',report['status']);self.assertFalse(report['controller_input_sent'])
        self.assertFalse(self.output.exists());self.assertEqual(self.initial,self.session.read_bytes())

    def test_exact_session_action_status_and_context_are_required(self):
        state=json.loads(self.initial)
        changes=[lambda s:s.update(schema='wrong'),lambda s:s['pending'].update(status='attempted'),
            lambda s:s['pending'].update(action_id=str(uuid4())),lambda s:s.update(run_id='different-run'),
            lambda s:s['pending']['request']['context'].update(run_id='different-run'),
            lambda s:s['pending']['outcome_request']['context'].update(floor_id='different-floor')]
        for change in changes:
            modified=deepcopy(state);change(modified);data=json.dumps(modified).encode();self.session.write_bytes(data)
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError,KeyError,AttributeError)):
                self.package()
            self.assertEqual(data,self.session.read_bytes());self.assertFalse(self.output.exists())

    def test_bounded_duplicate_free_finite_journal_is_required(self):
        for raw in [b' '*(MAX_BYTES+1),b'{"schema":1,"schema":2}',b'{"schema":NaN}',b'[]']:
            self.session.write_bytes(raw)
            with self.subTest(raw=raw[:40]),self.assertRaises((ValueError,AttributeError)):
                self.package()
            self.assertFalse(self.output.exists());self.assertEqual(raw,self.session.read_bytes())

    def test_only_distinct_existing_missing_event_notes_are_packaged(self):
        for notes in [[],[{'index':True,'evidence_note':self.note}],[{'index':-1,'evidence_note':self.note}],
            [{'index':1,'evidence_note':self.note}],[{'index':0,'evidence_note':''}],
            [{'index':0,'evidence_note':self.note}]*2,
            [{'index':0,'evidence_note':self.note,'related_item':'Different card'}]]:
            with self.subTest(notes=notes),self.assertRaises((ValueError,TypeError,KeyError)):
                self.package(notes=notes)
            self.assertFalse(self.output.exists())
        state=json.loads(self.initial);state['pending']['outcome_request']['inventory_events'][0]['evidence_note']='Existing reviewed note.'
        self.session.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'absent'):self.package()
        self.assertFalse(self.output.exists())

    def test_both_retained_before_and_result_image_hashes_are_checked(self):
        state=json.loads(self.initial)
        paths=[Path(state['pending']['request']['source']['path']),Path(self.original['source']['path'])]
        for path in paths:
            original=path.read_bytes();path.write_bytes(images.png_bytes((7,8,9)))
            with self.subTest(path=path.name),self.assertRaisesRegex(ValueError,'source bytes changed'):self.package()
            path.write_bytes(original);self.assertFalse(self.output.exists())
        self.assertEqual(self.initial,self.session.read_bytes())

    def test_pending_journal_change_during_packaging_rejects_pointer(self):
        original_identity=recovery._identity;calls=[]
        changed=json.dumps({**json.loads(self.initial),'other_writer_marker':True}).encode()
        def changed_journal(path):
            result=original_identity(path);calls.append(path)
            if len(calls)==1:self.session.write_bytes(changed)
            return result
        with patch.object(recovery,'_identity',side_effect=changed_journal),self.assertRaisesRegex(ValueError,'journal changed'):
            self.package()
        self.assertFalse(self.output.exists());self.assertEqual(changed,self.session.read_bytes())

    def test_existing_outputs_session_sources_and_dangling_symlinks_are_never_overwritten(self):
        self.output.write_bytes(b'existing review')
        targets=[self.output,self.session,Path(self.original['source']['path'])]
        for target in targets:
            original=target.read_bytes()
            with self.subTest(target=target.name),self.assertRaises((FileExistsError,ValueError)):
                self.package(output=target)
            self.assertEqual(original,target.read_bytes())
        self.output.unlink();target=self.f.root/'not-created';self.output.symlink_to(target)
        with self.assertRaises((FileExistsError,ValueError)):self.package()
        self.assertTrue(self.output.is_symlink());self.assertFalse(target.exists())

    def test_partial_new_output_is_removed_after_write_failure(self):
        real_open=Path.open
        class FailedWrite:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):return self.stream.__exit__(*args)
            def __getattr__(self,name):return getattr(self.stream,name)
            def write(self,data):
                self.stream.write(data[:20]);raise OSError('synthetic incomplete write')
        def opened(path,*args,**kwargs):
            stream=real_open(path,*args,**kwargs)
            return FailedWrite(stream) if path==self.output else stream
        with patch.object(Path,'open',opened),self.assertRaises((OSError,ValueError)):self.package()
        self.assertFalse(self.output.exists());self.assertEqual(self.initial,self.session.read_bytes())

    def test_cli_pointer_is_accepted_by_shadow_recovery_and_finalizes_without_bridge(self):
        output=io.StringIO()
        with redirect_stdout(output):self.assertEqual(0,recovery.main(self.argv()))
        pointer=json.loads(output.getvalue());self.assertEqual({'request_file':str(self.output.resolve())},pointer)
        before_calls=len(self.f.controller.calls);self.f.session.close()
        payload=(json.dumps(pointer)+'\n'+json.dumps({'operation':'finalize'})+'\n').encode()
        stream=io.TextIOWrapper(io.BytesIO(payload),encoding='utf-8');response=io.StringIO()
        with patch.object(adapter_cli.sys,'stdin',stream),redirect_stdout(response), \
             patch.object(adapter_cli,'BridgeClient',side_effect=AssertionError('recovery opened bridge')):
            self.assertEqual(0,adapter_cli.main([str(self.f.session.directory),'--run-id',self.f.context_ids['run_id'],
                '--database',str(self.f.db.path),'--mode','shadow']))
        lines=[json.loads(line) for line in response.getvalue().splitlines()]
        self.assertEqual(['ready_unarmed','outcome_metadata_repaired','verified'],[line['status'] for line in lines])
        self.assertEqual((1,1),self.g.counts());self.assertEqual(before_calls,len(self.f.controller.calls))
        final=json.loads(self.session.read_bytes());self.assertIsNone(final['pending'])
        self.assertEqual(1,final['completed'])
