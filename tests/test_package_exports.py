"""Public package compatibility and offline imports in the bridge environment."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIGHT_IMPORT = """
import sys, enum, json
had_str_enum = hasattr(enum, 'StrEnum')
from veda.bridge_channel import DEFAULT_BRIDGE_SOCKET
assert hasattr(enum, 'StrEnum') == had_str_enum
heavy = [name for name in ('veda.agent', 'veda.knowledge', 'veda.research',
    'veda.research_catalog', 'veda.vision', 'veda.decision_protocol') if name in sys.modules]
assert not heavy, heavy
assert 'ps5rmtctl.service' not in sys.modules
print(json.dumps({'socket': DEFAULT_BRIDGE_SOCKET, 'heavy_modules': heavy}))
"""


class PackageExportTests(unittest.TestCase):
    def test_leaf_import_in_fresh_interpreter_loads_no_agent_or_sdk(self):
        result = subprocess.run([sys.executable, '-c', LIGHT_IMPORT], cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('/tmp/veda-ps5-bridge.sock', json.loads(result.stdout)['socket'])

    def test_all_public_exports_keep_original_identity_and_star_import(self):
        import veda
        exports = {
            'agent': ('AutonomousAgent', 'Decision', 'DecisionPolicy', 'NoopPolicy'),
            'knowledge': ('Claim', 'ClaimKind', 'KnowledgeBase', 'Source'),
            'research': ('ResearchIntake', 'ResearchNote'),
            'research_catalog': ('load_catalog',),
            'vision': ('LocalOllamaVisionProvider', 'StructuredGameState', 'VisionProvider'),
            'decision_protocol': ('DecisionBrief', 'build_decision_brief'),
        }
        expected = {name for names in exports.values() for name in names}
        self.assertEqual(expected, set(veda.__all__))
        self.assertTrue(expected.issubset(dir(veda)))
        for module, names in exports.items():
            actual = importlib.import_module('veda.' + module)
            for name in names:
                self.assertIs(getattr(actual, name), getattr(veda, name))
                self.assertIs(getattr(actual, name), vars(veda)[name])
        namespace = {}
        exec('from veda import *', namespace)
        self.assertTrue(expected.issubset(namespace))
        self.assertTrue(all(namespace[name] is getattr(veda, name) for name in expected))

    def test_unknown_attributes_and_normal_submodule_import_still_work(self):
        import veda
        with self.assertRaises(AttributeError):
            getattr(veda, 'not_a_veda_export')
        from veda import bridge_channel
        self.assertEqual('/tmp/veda-ps5-bridge.sock', bridge_channel.DEFAULT_BRIDGE_SOCKET)

    def test_actual_bridge_environment_can_import_leaf_and_show_help_without_sdk_start(self):
        python = ROOT / '.bridge-venv/bin/python'
        if not python.is_file():
            self.skipTest('Optional local bridge SDK environment is not installed')
        imported = subprocess.run([str(python), '-c', LIGHT_IMPORT], cwd=ROOT,
                                  capture_output=True, text=True, timeout=5)
        self.assertEqual(0, imported.returncode, imported.stderr)
        result = subprocess.run([str(ROOT / 'scripts/warm_bridge'), '--help'], cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('--socket', result.stdout)
        self.assertIn('--stdio', result.stdout)
        self.assertNotIn('"event":"ready"', result.stdout)
        self.assertEqual('', result.stderr)


if __name__ == '__main__':
    unittest.main()
