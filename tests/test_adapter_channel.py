"""Private socket tests; no bridge, capture or controller."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import socket
import unittest
from veda.adapter_channel import RequestServer, submit, ReplyUnknown, socket_path


class AdapterChannelTests(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory(); self.addCleanup(t.cleanup); self.root = Path(t.name)

    def test_reply_returns_without_waiting_for_persistent_server_exit(self):
        with RequestServer(self.root) as server:
            def respond():
                submission = server.accept()
                self.assertEqual({'operation': 'summary'}, json.loads(submission.raw))
                submission.reply({'status': 'ready_unarmed'})
            thread = Thread(target=respond); thread.start()
            self.assertEqual({'status': 'ready_unarmed'}, submit(self.root/'state.json', {'operation': 'summary'}))
            thread.join(2); self.assertFalse(thread.is_alive())
            self.assertTrue(server.path.exists())
            self.assertEqual(0o600, server.path.stat().st_mode & 0o777)
        self.assertFalse(socket_path(self.root).exists())

    def test_lost_reply_is_unknown_and_never_resubmits(self):
        with RequestServer(self.root) as server:
            received = []
            def disconnect():
                submission = server.accept(); received.append(submission.raw)
                submission.connection.close()
            thread = Thread(target=disconnect); thread.start()
            with self.assertRaises(ReplyUnknown): submit(self.root, {'operation': 'execute'})
            thread.join(2); self.assertEqual(1, len(received))

    def test_connection_absent_is_not_sent_and_wrong_reply_is_unknown(self):
        with self.assertRaises(FileNotFoundError): submit(self.root, {'operation': 'summary'})
        with RequestServer(self.root) as server:
            def wrong():
                submission = server.accept()
                submission.connection.sendall(b'{"schema":"wrong"}\n'); submission.connection.close()
            thread = Thread(target=wrong); thread.start()
            with self.assertRaises(ReplyUnknown): submit(self.root, {'operation': 'summary'})
            thread.join(2)

    def test_active_endpoint_not_replaced_and_dead_owned_endpoint_recovers(self):
        with RequestServer(self.root) as server:
            with self.assertRaisesRegex(ValueError, 'active'):
                with RequestServer(self.root): pass
            self.assertIsNone(server.accept())
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(socket_path(self.root))); stale.close()
        with RequestServer(self.root) as server: self.assertTrue(server.path.exists())

    def test_client_capture_only_after_ack_and_never_after_unknown(self):
        from scripts.veda_submit import main
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        with patch('scripts.veda_submit.submit', return_value={'status':'verified'}), \
             patch('veda.game_capture.capture_game_window', side_effect=AssertionError('unexpected capture')), redirect_stdout(StringIO()):
            self.assertEqual(0, main(['--session',str(self.root),'--operation','summary','--capture-after']))
        with patch('scripts.veda_submit.submit', side_effect=ReplyUnknown('inspect pending')), redirect_stdout(StringIO()):
            self.assertEqual(2, main(['--session',str(self.root),'--operation','summary','--capture-after']))

    def test_persistent_serve_routes_file_pointer_and_stop_reply(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.veda_reviewed_play import serve
        class Session:
            closed=False
            decision_policy='learning'
            def summary(self): return {'armed':False}
            def handle(self,request):
                if request['operation']=='stop': self.closed=True
                return {'status':request['operation'],'controller_input_sent':False}
            def recoverable_error(self,error): return {'status':'recoverable_review','reason':str(error)}
            def timing_summary(self,**kwargs): return {}
        read_fd,write_fd=os.pipe()
        source=os.fdopen(read_fd); self.addCleanup(source.close); self.addCleanup(os.close,write_fd)
        packet=self.root/'packet.json'; packet.write_text('{"operation":"summary"}')
        with RequestServer(self.root) as server, patch('scripts.veda_reviewed_play.sys.stdin',source), redirect_stdout(StringIO()):
            thread=Thread(target=serve,args=(Session(),server,SimpleNamespace(mode='codex',verbose_timing=False)))
            thread.start()
            self.assertEqual('summary',submit(self.root,{'request_file':str(packet)})['status'])
            self.assertTrue(thread.is_alive())
            self.assertEqual('stop',submit(self.root,{'operation':'stop'})['status'])
            thread.join(2); self.assertFalse(thread.is_alive())

    def test_actual_adapter_execute_verify_via_socket_uses_one_fake_input(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.veda_reviewed_play import serve
        from tests import test_combat_requests as fixture
        from veda.combat_flow import observed_result
        f=fixture.CombatResultTests(methodName='runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        request=f.packet(f.value); f.fx.before=request
        arm=f.fx.arm_request(); arm['frame_id']=request['reading']['frame_id']; f.session.handle(arm)
        request['operation']='execute'
        read_fd,write_fd=os.pipe(); source=os.fdopen(read_fd)
        self.addCleanup(source.close); self.addCleanup(os.close,write_fd)
        with RequestServer(f.session.path) as server, patch('scripts.veda_reviewed_play.sys.stdin',source), redirect_stdout(StringIO()):
            thread=Thread(target=serve,args=(f.session,server,SimpleNamespace(mode='codex',verbose_timing=False)))
            thread.start()
            reply=submit(f.session.path,request)
            self.assertEqual('awaiting_fresh_review',reply['status'])
            review=observed_result(f.session.path,focus='d',unchanged=True,note='Synthetic actual focus inspected.')
            packet=f.packet(review,result_mode=True)
            reply=submit(f.session.path,packet)
            self.assertEqual('verified',reply['status']); self.assertEqual(1,len(f.fx.controller.inputs))
            self.assertIsNone(f.session.state['pending'])
            submit(f.session.path,{'operation':'stop'}); thread.join(2); self.assertFalse(thread.is_alive())
