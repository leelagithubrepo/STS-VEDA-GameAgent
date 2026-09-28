import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from veda.request_envelope import load_request, MAX_BYTES

class EnvelopeTests(unittest.TestCase):
    def test_both_spellings_load_exact_same_verify_without_dispatch(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'result.json'
            packet={'operation':'verify','action_id':'already-sent','after':{'actual':True}}
            path.write_text(json.dumps(packet))
            for envelope in ({'request_file':str(path)}, {'operation':'request_file','path':str(path)}):
                self.assertEqual(packet,load_request(json.dumps(envelope)))
            self.assertEqual(packet,load_request(json.dumps(packet)))

    def test_conflicting_nested_duplicate_and_oversize_requests_are_rejected(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'request.json'
            for packet in ({'request_file':str(path)}, {'operation':'request_file','path':str(path)}, []):
                path.write_text(json.dumps(packet))
                with self.assertRaises(ValueError):load_request(json.dumps({'request_file':str(path)}))
            for raw in ('{"operation":"stop","operation":"send"}', '{"request_file":"x","operation":"execute"}',
                        '{"operation":"request_file","path":"x","buttons":["cross"]}', ' '* (MAX_BYTES+1)):
                with self.assertRaises(ValueError):load_request(raw)

    def test_actual_cli_accepts_alias_and_canonical_envelopes(self):
        import subprocess, sys
        from veda.telemetry_database import TelemetryDatabase
        with TemporaryDirectory() as directory:
            root=Path(directory);database=TelemetryDatabase(root/'fixture.sqlite3')
            run=database.start_or_resume_run(ascension=0)
            packet=root/'summary.json';packet.write_text('{"operation":"summary"}')
            inputs='\n'.join(json.dumps(v) for v in (
                {'operation':'request_file','path':str(packet)}, {'request_file':str(packet)}, {'operation':'stop'}))+'\n'
            result=subprocess.run([sys.executable,'scripts/veda_reviewed_play.py',str(root/'session'),
                '--run-id',run,'--database',str(root/'fixture.sqlite3'),'--mode','shadow'],
                input=inputs,text=True,capture_output=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stderr)
            output=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual('stopped',output[-1]['status'])
            for reply in output[1:-1]:
                self.assertNotIn('error',reply);self.assertIsNone(reply['pending'])
