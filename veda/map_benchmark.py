"""Offline graph-contract evaluation against separately authored expectations.

No model, screenshot reader, database, bridge, or preferred-route oracle is used.
The timed portion measures these local functions only, never a gameplay floor.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from time import perf_counter

from .map_brief import summarize_routes


MAX_BYTES = 1_000_000


def read_object(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('input exceeds one megabyte')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('nonfinite JSON number')

    def finite_float(raw_number):
        number = float(raw_number)
        if not math.isfinite(number):
            raise ValueError('nonfinite JSON number')
        return number

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
    if not isinstance(value, dict):
        raise ValueError('input must be a JSON object')
    return value, sha256(raw).hexdigest()


def write_new_json(path, value):
    """Serialize first, then exclusively create; never overwrite prior evidence."""
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
    with Path(path).open('x') as stream:
        stream.write(encoded)


def _variant(original, name):
    value = deepcopy(original)
    mapping = {row['node_id']: row['node_id'] for row in value['visible_nodes']}
    if name == 'renamed':
        mapping = {key: f'scenario-node-{index:03d}' for index, key in enumerate(sorted(mapping))}
        value['current_node_id'] = mapping[value['current_node_id']]
        for row in value['visible_nodes']:
            row['node_id'] = mapping[row['node_id']]
        for row in value['visible_edges']:
            row['from_node_id'] = mapping[row['from_node_id']]
            row['to_node_id'] = mapping[row['to_node_id']]
    if name != 'original':
        value['visible_nodes'].reverse()
        value['visible_edges'].reverse()
    return value, mapping


def _mismatches(actual, expected, mapping, resources):
    errors = []

    def equal(label, got, wanted):
        # bool and integer must not compare as equivalent contract values.
        if json.dumps(got, sort_keys=True) != json.dumps(wanted, sort_keys=True):
            errors.append({'field': label, 'expected': wanted, 'actual': got})

    equal('resources', actual['resources'], resources)
    for key in ('freshness_established', 'controller_authorized', 'runtime_authorized'):
        equal(key, actual[key], False)
    equal('resources_role', actual['resources_role'], 'reviewer_context_only')
    options = {row['node_id']: row for row in actual['options']}
    equal('confirmed_first_node_ids', sorted(options),
          sorted(mapping[key] for key in expected['confirmed_first_node_ids']))
    equal('unconfirmed_first_node_ids', sorted(row['node_id'] for row in actual['unverified_reachable']),
          sorted(mapping[key] for key in expected.get('unconfirmed_first_node_ids', [])))
    equal('blocked', actual['blocked'], not bool(expected['confirmed_first_node_ids']))
    for original_id, wanted in expected['per_first_node'].items():
        node_id = mapping[original_id]
        if node_id not in options:
            continue  # Missing option is already a failure above.
        row = options[node_id]
        for key in ('reachable_rest_min_distance', 'reachable_merchant_min_distance'):
            equal(original_id + '.' + key, row[key], {mapping[k]: v for k, v in wanted[key].items()})
        for key in ('has_rest_path_avoiding_known_elites', 'has_confirmed_elite_free_rest_path'):
            equal(original_id + '.' + key, row[key], wanted[key])
        equal(original_id + '.rest.reachable', row['rest']['reachable'], wanted['rest_reachable'])
        for key in ('event_node_ids', 'uncertain_node_ids'):
            if key in wanted:
                equal(original_id + '.' + key, row[key], sorted(mapping[k] for k in wanted[key]))
    return errors


def evaluate_fixture(document, *, fixture_sha256):
    if document.get('schema') != 'veda.dynamic-map-scenarios.v1':
        raise ValueError('expected veda.dynamic-map-scenarios.v1 fixture')
    cases, invalid, profiles = document.get('cases'), document.get('invalid_cases'), document.get('resource_profiles')
    if (not isinstance(cases, list) or not 1 <= len(cases) <= 100
            or not isinstance(invalid, list) or len(invalid) > 100
            or not isinstance(profiles, dict) or not 1 <= len(profiles) <= 12
            or any(not isinstance(value, dict) for value in profiles.values())):
        raise ValueError('fixture needs bounded cases, invalid cases, and resource profiles')
    ids = [case['case_id'] for case in cases + invalid]
    if any(not isinstance(key, str) or not key.strip() for key in ids) or len(set(ids)) != len(ids):
        raise ValueError('fixture case IDs must be unique nonempty strings')
    # No empty oracle can produce a passing case by omitting per-option checks.
    for case in cases:
        expected = case['expected']
        if (len(set(expected['confirmed_first_node_ids'])) != len(expected['confirmed_first_node_ids'])
                or set(expected['confirmed_first_node_ids']) != set(expected['per_first_node'])):
            raise ValueError('every expected first option needs its own explicit oracle')
    results = []
    started = perf_counter()
    for case in cases:
        for profile_name, resources in sorted(profiles.items()):
            for variant in ('original', 'reordered', 'renamed'):
                value, mapping = _variant(case['input'], variant)
                value['resources'] = deepcopy(resources)
                original = deepcopy(value)
                try:
                    output = summarize_routes(**value)
                    errors = _mismatches(output, case['expected'], mapping, resources)
                    if value != original:
                        errors.append({'field': 'input_mutated'})
                except ValueError as exc:
                    errors = [{'field': 'unexpected_rejection', 'reason': str(exc)}]
                results.append({'case_id': case['case_id'], 'resource_profile': profile_name,
                                'variant': variant, 'passed': not errors, 'failures': errors})
    for case in invalid:
        if case['expected'].get('must_reject') is not True:
            raise ValueError('invalid case must explicitly require rejection')
        for variant in ('original', 'reordered', 'renamed'):
            value, _ = _variant(case['input'], variant)
            try:
                summarize_routes(**value)
            except ValueError as exc:
                errors = []
                reason = str(exc)
            else:
                errors = [{'field': 'invalid_graph_accepted'}]
                reason = None
            results.append({'case_id': case['case_id'], 'variant': variant,
                            'passed': not errors, 'failures': errors, 'rejection_reason': reason})
    failed = sum(not row['passed'] for row in results)
    return {
        'schema': 'veda.map-benchmark.v1', 'fixture_sha256': fixture_sha256,
        'status': 'passed' if not failed else 'failed', 'total': len(results),
        'passed': len(results) - failed, 'failed': failed, 'results': results,
        'local_evaluation_ms': round((perf_counter() - started) * 1000, 3),
        'scope': 'synthetic graph facts and uncertainty contracts only',
        'excludes': ['strategy quality', 'screenshot recognition', 'model performance',
                     'controller delivery', 'floor latency', 'win rate', 'learning'],
        'controller_input_sent': False, 'database_modified': False,
        'controller_authorized': False, 'runtime_authorized': False,
    }
