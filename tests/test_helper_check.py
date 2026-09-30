from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class HelperCheckTests(unittest.TestCase):
    @staticmethod
    def module():
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'veda_helper_check.py'
        spec = importlib.util.spec_from_file_location('veda_helper_check_fixture', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def invoke(self, module, argv):
        output = io.StringIO()
        with patch('sys.argv', ['veda_helper_check.py', *argv]), redirect_stdout(output):
            status = module.main()
        return status, json.loads(output.getvalue())

    def test_session_receipt_turns_repeated_helper_validation_into_a_cache_hit(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / 'session'
            session.mkdir()
            first_status, first = self.invoke(module, ['veda_loot.py', '--session', str(session)])
            second_status, second = self.invoke(module, ['veda_loot.py', '--session', str(session)])
            self.assertEqual(0, first_status)
            self.assertFalse(first['cached'])
            self.assertEqual(0, second_status)
            self.assertTrue(second['cached'])
            receipt = json.loads((session / '.helper-registry.json').read_text())
            self.assertIn('veda_loot.py', receipt['helpers'])

    def test_missing_session_is_rejected_without_creating_a_directory(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / 'missing'
            status, result = self.invoke(module, ['veda_loot.py', '--session', str(session)])
            self.assertEqual(2, status)
            self.assertIn('session directory', result['reason'])
            self.assertFalse(session.exists())


if __name__ == '__main__':
    unittest.main()
