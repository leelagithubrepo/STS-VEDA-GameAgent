"""Event continuity and delayed result recording using synthetic SQLite and PNGs."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from uuid import uuid4
from tests import test_reviewed_play as fixture
from tests.test_combat_requests import draft, result
from tests.test_menu_requests import make_capture
from veda.combat_requests import write_combat_request, write_combat_result
from veda.evidence_continuity import bind_session

class EventEvidenceTests(unittest.TestCase):
    setUp = fixture.ReviewedPlayTests.setUp
    close_session = fixture.ReviewedPlayTests.close_session
    factory = fixture.ReviewedPlayTests.factory
    create = fixture.ReviewedPlayTests.create
    source = fixture.ReviewedPlayTests.source
    combat_request = fixture.ReviewedPlayTests.combat_request
    arm_request = fixture.ReviewedPlayTests.arm_request
    arm = fixture.ReviewedPlayTests.arm

    def bound(self):
        request = deepcopy(self.before)
        request['evidence_binding'] = bind_session(self.session.path, request['context'], request['source'])
        return request

    def test_long_thinking_does_not_expire_action_and_epoch_changes_after_dispatch(self):
        self.create(); self.arm()
        request = self.bound()
        self.now += timedelta(minutes=20)
        prepared = self.session.handle(request)
        self.assertEqual('prepared', prepared['status'])
        sent = self.session.handle({'operation':'send','action_id':prepared['action_id']})
        self.assertEqual('awaiting_fresh_review', sent['status'])
        self.assertEqual(1, len(self.controller.inputs))
        self.assertNotEqual(request['evidence_binding'], self.session.state['evidence_continuity'])
        # Even after receipt time passes 180s, the stored observation time stays original.
        with self.db._connection() as con:
            payload=json.loads(con.execute("SELECT e.payload_json FROM evidence_events e JOIN decisions d ON d.event_id=e.id").fetchone()[0])
        self.assertEqual(self.before['source']['captured_at'], payload['receipt']['source']['captured_at'])

    def test_external_change_invalidates_prepared_input_without_sending(self):
        self.create(); self.arm(); prepared=self.session.handle(self.bound())
        self.session.handle({'operation':'invalidate_evidence','reason':'Player moved controller focus'})
        with self.assertRaisesRegex(ValueError, 'epoch changed'):
            self.session.handle({'operation':'send','action_id':prepared['action_id']})
        self.assertEqual([], self.controller.inputs)

    def test_pre_epoch_capture_and_context_change_are_rejected(self):
        self.create(); self.arm()
        old = self.source(-1)
        with self.assertRaisesRegex(ValueError,'precedes'):
            bind_session(self.session.path,self.context_ids,old)
        self.session.state['evidence_continuity']['context'] = self.context_ids
        self.session._save()
        changed = dict(self.context_ids, turn_id='other-turn')
        with self.assertRaisesRegex(ValueError,'another run, floor or turn'):
            bind_session(self.session.path,changed,self.before['source'])

    def test_old_session_binding_cannot_survive_restart(self):
        self.create(); self.arm(); request=self.bound(); self.close_session(); self.create(); self.arm()
        with self.assertRaisesRegex(ValueError,'epoch changed'):
            self.session.handle(request)
        self.assertEqual([], self.controller.inputs)

    def test_delayed_historical_result_finalizes_once_without_replay(self):
        self.create(); self.arm()
        prepared=self.session.handle(self.before)
        self.session.handle({'operation':'send','action_id':prepared['action_id']})
        after=self.combat_request(1,focus='s')
        original_time=after['source']['captured_at']
        self.now += timedelta(minutes=20)
        answer=self.session.handle({'operation':'verify','action_id':prepared['action_id'],
            'operation_id':str(uuid4()),'after':after,'telemetry':{'zone_coverage':'complete'}})
        self.assertEqual('verified',answer['status'])
        self.assertEqual(1,len(self.controller.inputs))
        self.assertEqual(original_time,self.session.state['last_verified']['source']['captured_at'])
        self.assertIsNone(self.session.state['pending'])
        with self.assertRaisesRegex(ValueError,'precedes'):
            bind_session(self.session.path,self.context_ids,self.before['source'])

    def test_result_cannot_use_pre_dispatch_frame_or_future_image(self):
        self.create(); self.arm(); prepared=self.session.handle(self.before)
        self.session.handle({'operation':'send','action_id':prepared['action_id']})
        for after in (self.before,self.combat_request(2,focus='s')):
            with self.assertRaises((ValueError, RuntimeError)):
                self.session.handle({'operation':'verify','action_id':prepared['action_id'],
                    'operation_id':str(uuid4()),'after':after,'telemetry':{}})
        self.assertEqual(1,len(self.controller.inputs)); self.assertIsNotNone(self.session.state['pending'])

    def test_compact_helper_binds_old_capture_and_preserves_hash_time(self):
        self.create();self.arm()
        captured=self.base_time + timedelta(seconds=1)
        image=make_capture(self.root,captured)
        self.now += timedelta(minutes=15)
        output=self.root/'action.json'
        write_combat_request(draft(self.context_ids), capture=image, reviewer='Fixture',
            evidence_note='Settled synthetic state.',reviewed=True,output=output,session=self.session.path,now=self.now)
        packet=json.loads(output.read_text())
        self.assertEqual(captured.isoformat(),packet['source']['captured_at'])
        self.assertEqual(self.session.state['evidence_continuity'],packet['evidence_binding'])
        self.assertEqual('prepared',self.session.handle(packet)['status'])
        self.assertEqual([],self.controller.inputs)

    def test_transport_failure_still_prevents_an_epoch_bound_input(self):
        self.create();self.arm();request=self.bound()
        prepared=self.session.handle(request)
        self.controller.ready=False
        with self.assertRaisesRegex(RuntimeError,'readiness lost'):
            self.session.handle({'operation':'send','action_id':prepared['action_id']})
        self.assertEqual([],self.controller.inputs)
        self.assertFalse(self.session.armed)

    def test_stop_closes_controller_even_if_continuity_save_fails(self):
        from unittest.mock import patch
        self.create();self.arm()
        with patch.object(self.session,'_save',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):self.session.close()
        self.assertTrue(self.controller.closed)
        self.assertTrue(any(c['action']=='close' for c in self.controller.calls))
        self.assertEqual([],self.controller.inputs)
