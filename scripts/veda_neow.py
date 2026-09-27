#!/usr/bin/env python3
"""Register an authorized reviewed Neow attempt or package Talk. Never sends input."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.neow_registration import read_review, register_neow_attempt, write_neow_talk_request, write_neow_talk_result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('artifacts/veda-memory.sqlite3'))
    commands = parser.add_subparsers(dest='operation', required=True)
    register = commands.add_parser('register', help='Bind the already-open authorized attempt; no bridge or capture.')
    register.add_argument('--review', type=Path, required=True, help='Small explicit review of the exact fresh Neow capture.')
    register.add_argument('--phrase', required=True)
    register.add_argument('--authorization-scope', choices=['current_visible_attempt'], required=True,
                          help='Use only when the user authorizes this already-open attempt; resume-only is insufficient.')
    register.add_argument('--previous-run-id')
    register.add_argument('--sessions-root', type=Path)
    talk = commands.add_parser('talk', help='Write one prepare request; submit it through the reviewed adapter.')
    talk.add_argument('--review', type=Path, required=True)
    talk.add_argument('--run-id', required=True)
    talk.add_argument('--output', type=Path, required=True)
    result = commands.add_parser('talk-result', help='Package the fresh observed result for the pending Talk.')
    result.add_argument('--review', type=Path, required=True)
    result.add_argument('--before-request', type=Path, required=True)
    result.add_argument('--action-id', required=True, help='Exact action ID returned by the adapter prepare step.')
    result.add_argument('--observed-result', required=True, help='Describe the visible change actually inspected.')
    result.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        review = read_review(args.review)
        if args.operation == 'register':
            result = register_neow_attempt(args.database, review, phrase=args.phrase,
                authorization_scope=args.authorization_scope, previous_run_id=args.previous_run_id,
                sessions_root=args.sessions_root)
        elif args.operation == 'talk-result':
            result = write_neow_talk_result(args.before_request, review, action_id=args.action_id,
                observed_result=args.observed_result, output=args.output)
        else:
            result = write_neow_talk_request(args.database, review, run_id=args.run_id, output=args.output)
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False}))
        return 2
    except (KeyError, TypeError, AttributeError, sqlite3.Error):
        print(json.dumps({'status': 'needs_review', 'reason': 'malformed review or unavailable telemetry',
                          'controller_input_sent': False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
