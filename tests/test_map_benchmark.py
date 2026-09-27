"""Check the independent oracle, metamorphic variants, and read-only CLI boundary."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.veda_decisions import main
from veda.map_benchmark import evaluate_fixture, read_object, write_new_json


FIXTURE = Path(__file__).resolve().parents[1] / '.veda/evals/dynamic-map-scenarios.json'


class MapBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.document, self.digest = read_object(FIXTURE)

    def test_independent_oracle_all_profiles_and_graph_variants(self):
        original = deepcopy(self.document)
        result = evaluate_fixture(self.document, fixture_sha256=self.digest)
        self.assertEqual(66, result['total'])
        self.assertEqual(0, result['failed'], result['results'])
        self.assertEqual(self.digest, result['fixture_sha256'])
        self.assertEqual(original, self.document)
        self.assertFalse(result['runtime_authorized'])
        self.assertFalse(result['controller_authorized'])
        self.assertIn('strategy quality', result['excludes'])
        self.assertIn('floor latency', result['excludes'])

    def test_wrong_independent_expectation_fails_every_affected_variant(self):
        self.document['cases'][0]['expected']['per_first_node']['left']['reachable_rest_min_distance']['last-rest'] = 9
        result = evaluate_fixture(self.document, fixture_sha256=self.digest)
        self.assertEqual('failed', result['status'])
        self.assertEqual(9, result['failed'])
        self.assertTrue(all(row['case_id'] == 'branch_merge' for row in result['results'] if not row['passed']))

    def test_absence_is_not_unknown_or_truthy_integer(self):
        self.document['cases'][0]['expected']['per_first_node']['left']['rest_reachable'] = 1
        result = evaluate_fixture(self.document, fixture_sha256=self.digest)
        self.assertEqual(9, result['failed'])

    def test_incomplete_oracle_and_empty_suite_cannot_pass(self):
        self.document['cases'][0]['expected']['per_first_node'] = {}
        with self.assertRaises(ValueError):
            evaluate_fixture(self.document, fixture_sha256=self.digest)
        self.document['cases'] = []
        with self.assertRaises(ValueError):
            evaluate_fixture(self.document, fixture_sha256=self.digest)

    def test_json_rejects_duplicate_nonfinite_and_oversized_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', '[]', ' ' * 1_000_001):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    read_object(path)

    def test_cli_preserves_existing_output_and_returns_failure_for_wrong_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'report.json'
            write_new_json(target, {'sentinel': True})
            with redirect_stdout(io.StringIO()):
                self.assertEqual(2, main(['benchmark', '--output', str(target)]))
            self.assertEqual({'sentinel': True}, json.loads(target.read_text()))
            self.document['cases'][0]['expected']['per_first_node']['left']['rest_reachable'] = False
            source = Path(directory) / 'wrong.json'
            write_new_json(source, self.document)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, main(['benchmark', '--input', str(source)]))

    def test_brief_cli_is_facts_only_and_never_selects(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'map.json'
            write_new_json(source, self.document['cases'][0]['input'])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, main(['brief', '--input', str(source)]))
            brief = json.loads(output.getvalue())
            self.assertEqual(['left', 'right'], [row['node_id'] for row in brief['options']])
            self.assertFalse(brief['freshness_established'])
            self.assertFalse(brief['controller_authorized'])
            self.assertNotIn('selected_node_id', brief)

    def test_brief_cli_explains_invalid_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'cycle.json'
            write_new_json(source, self.document['invalid_cases'][0]['input'])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(2, main(['brief', '--input', str(source)]))
            self.assertIn('acyclic', json.loads(output.getvalue())['reason'])


if __name__ == '__main__':
    unittest.main()
