"""Synthetic result packing and real CLI; no live capture or game state."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts import veda_menu
from tests import test_menu_controls as menus
from tests.test_menu_requests import draft, make_capture
from veda.choice_execution import plan_choice_step
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_requests import write_menu_request
from veda.menu_results import validate_menu_result, write_menu_result


class MenuResultTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.now = datetime.now(timezone.utc)
        before = make_capture(self.root, self.now - timedelta(seconds=3))
        self.image = make_capture(self.root, self.now - timedelta(seconds=1), color=(51,41,91), index='b')
        path = self.root/'before.json'
        write_menu_request(draft(), capture=before, reviewer='Fixture', evidence_note='Synthetic fixture.',
            reviewed=True, control_profile=CONTROL_PROFILE, output=path, now=self.now)
        request = json.loads(path.read_text())
        self.action = str(uuid4())
        self.state = {'schema':'veda.reviewed-play.v1', 'run_id': request['context']['run_id'],
            'pending': {'status':'attempted', 'action_id':self.action, 'request':request,
                'decision_id':str(uuid4()), 'attempted_at':(self.now-timedelta(seconds=2)).isoformat(),
                'proposal':plan_choice_step(request['observation'], request['choice'], now=self.now, action_id=self.action)}}
        self.session = self.root/'state.json'; self.save_state()
        ui = menus.grid()['ui']
        self.result = {'schema':'veda.menu-result.v1', 'action_id':self.action,
            'resources':'unchanged', 'inventory':'unchanged', 'facts':'unchanged',
            'observed_result':'Synthetic reviewer sees the upgrade grid opened.',
            'result': {'kind':'menu', 'ui': {k:v for k,v in ui.items() if k != 'navigation'}}}
        self.output = self.root/'after.json'

    def save_state(self):
        self.session.write_text(json.dumps(self.state))

    def write(self, **kw):
        args = dict(session=self.session, action_id=self.action, capture=self.image, reviewer='Fixture reviewer',
            evidence_note='Inspected this exact synthetic after image.', reviewed=True,
            control_profile=CONTROL_PROFILE, output=self.output, now=self.now)
        args.update(kw)
        return write_menu_result(self.result, **args)

    def test_validation_has_no_source_or_request_and_does_not_modify_pending(self):
        before = self.session.read_bytes()
        with patch('veda.menu_results.reviewed_capture_source', side_effect=AssertionError('source call')):
            result=validate_menu_result(self.result,session=self.session,action_id=self.action,control_profile=CONTROL_PROFILE)
        self.assertTrue(result['result_valid']); self.assertFalse(result['source_bound'])
        self.assertFalse(result['dispatchable']); self.assertNotIn('request_file',result)
        self.assertEqual(before,self.session.read_bytes()); self.assertFalse(self.output.exists())

    def test_outcome_correlation_is_derived_from_pending_never_filename(self):
        self.write(); packet=json.loads(self.output.read_text())
        review=packet['after']['review']
        self.assertEqual(review,packet['after']['observation']['review'])
        self.assertEqual(review['outcome']['before_frame_id'], self.state['pending']['request']['observation']['frame']['frame_id'])
        self.assertNotEqual(review['outcome']['before_frame_id'],Path(self.state['pending']['request']['source']['path']).stem)
        self.assertEqual(review['outcome']['action_id'],self.action)
        self.assertEqual(packet['after']['source']['sha256'],review['image_sha256'])
        self.assertEqual(packet['telemetry'],{})

    def test_requires_exact_attempted_action_and_explicit_image_inspection(self):
        for kwargs in ({'action_id':str(uuid4())},{'reviewed':False},{'capture':''}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.write(**kwargs)
            self.assertFalse(self.output.exists())
        self.state['pending']['status']='verified_pending_log'; self.save_state()
        with self.assertRaisesRegex(ValueError,'finalize or metadata repair'):self.write()

    def test_does_not_allow_manually_supplied_source_or_previous_frame(self):
        for field in ('source','review','frame','before_frame_id'):
            with self.subTest(field=field):
                value=deepcopy(self.result); value[field]={}
                with self.assertRaises(ValueError): validate_menu_result(value,session=self.session,action_id=self.action,control_profile=CONTROL_PROFILE)

    def test_old_after_image_cannot_be_bound_to_a_later_dispatch(self):
        self.state['pending']['attempted_at']=self.now.isoformat();self.save_state()
        with self.assertRaisesRegex(ValueError,'follow the actual dispatch'): self.write()
        self.assertFalse(self.output.exists())

    def test_bad_result_is_rejected_before_output_and_preserves_original_files(self):
        original=self.session.read_bytes()
        self.result['resources']={'hp':1}
        with self.assertRaises(ValueError): self.write()
        self.assertEqual(original,self.session.read_bytes()); self.assertFalse(self.output.exists())

    def test_telemetry_cannot_override_outcome_identity_or_status(self):
        for key,value in [('status','verified'),('context',self.state['pending']['request']['context']),
                          ('operation_id',str(uuid4())),('source',{})]:
            self.result['telemetry']={key:value}
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'unknown telemetry override'):self.write()
            self.assertFalse(self.output.exists())

    def test_cli_result_validate_and_bind_use_short_file_pointer(self):
        path=self.root/'result-draft.json';path.write_text(json.dumps(self.result))
        base=['--result',str(path),'--session',str(self.session),'--control-profile',CONTROL_PROFILE]
        for more in (['--validate'],['--capture',str(self.image),'--reviewer','Fixture',
                '--evidence-note','Explicit synthetic review.','--reviewed','--output',str(self.output)]):
            out=io.StringIO()
            with redirect_stdout(out):status=veda_menu.main(base+more)
            self.assertEqual(0,status,out.getvalue())
            result=json.loads(out.getvalue())
            self.assertNotIn('after',result)
        self.assertEqual(str(self.output),result['request_file'])
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            veda_menu.main(['--result',str(path),'--validate','--control-profile',CONTROL_PROFILE])

    def test_source_session_changes_and_disk_failures_do_not_publish_partial_result(self):
        from veda import menu_results
        original=menu_results.read_pending
        calls=[]
        def altered(*args):
            pending,digest=original(*args);calls.append(None)
            return pending,digest if len(calls)==1 else 'changed'
        with patch.object(menu_results,'read_pending',side_effect=altered),self.assertRaisesRegex(ValueError,'pending action changed'):
            self.write()
        self.assertFalse(self.output.exists())
        with patch.object(menu_results.os,'fsync',side_effect=OSError('fixture disk failure')),self.assertRaises(OSError): self.write()
        self.assertFalse(self.output.exists())
        self.output.write_text('preserve')
        with self.assertRaises(FileExistsError):self.write()
        self.assertEqual('preserve',self.output.read_text())
