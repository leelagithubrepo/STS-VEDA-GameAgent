#!/usr/bin/env python3
"""Summarize declared map facts or evaluate offline fixtures. Never sends input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.map_benchmark import evaluate_fixture, read_object, write_new_json
from veda.map_brief import summarize_routes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    brief = commands.add_parser('brief', help='Read declared visible graph; return facts without selecting a route.')
    brief.add_argument('--input', type=Path, required=True)
    benchmark = commands.add_parser('benchmark', help='Check independent synthetic graph expectations.')
    benchmark.add_argument('--input', type=Path, default=Path(__file__).resolve().parents[1] / '.veda/evals/dynamic-map-scenarios.json')
    for command in (brief, benchmark):
        command.add_argument('--output', type=Path, help='Optional NEW file; otherwise print JSON.')
    args = parser.parse_args(argv)
    try:
        value, digest = read_object(args.input)
        result = summarize_routes(**value) if args.operation == 'brief' else evaluate_fixture(value, fixture_sha256=digest)
        if args.output:
            write_new_json(args.output, result)
        else:
            print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error),
                          'controller_input_sent': False, 'database_modified': False}))
        return 2
    except (TypeError, KeyError, AttributeError, RecursionError):
        print(json.dumps({'status': 'needs_review', 'reason': 'Malformed input; use the documented graph or fixture schema.',
                          'controller_input_sent': False, 'database_modified': False}))
        return 2
    if args.output:
        print(json.dumps({'status': result.get('status', 'brief_created'), 'output': str(args.output.resolve()),
                          'controller_input_sent': False, 'database_modified': False}))
    return 1 if result.get('status') == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
