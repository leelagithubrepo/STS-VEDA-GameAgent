#!/usr/bin/env python3
"""Validate compact combat facts before capture; bind exact reviewed evidence. No input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.combat_requests import (read_combat_draft, validate_combat_draft, validate_combat_result,
                                   write_combat_request, write_combat_result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--draft', type=Path, help='Single source-free combat state, UI and one proposed action.')
    modes.add_argument('--result', type=Path, help='Actual after-state or boundary tied to an attempted action.')
    parser.add_argument('--session', type=Path, help='Existing state.json, read-only; required with --result.')
    parser.add_argument('--validate', action='store_true', help='Validate before capture; no executable request is produced.')
    parser.add_argument('--capture', type=Path, help='Exact inspected image with original capture receipt.')
    parser.add_argument('--reviewer')
    parser.add_argument('--evidence-note')
    parser.add_argument('--reviewed', action='store_true', help='Declare every supplied fact matches the exact image.')
    parser.add_argument('--output', type=Path, help='New packet path; never overwritten.')
    parser.add_argument('--execute', action='store_true', help='Package one atomic prepare-and-send request for an already armed adapter.')
    args = parser.parse_args(argv)
    if bool(args.result) != bool(args.session):
        parser.error('--session is required only with --result')
    if args.execute and (args.result or args.validate):
        parser.error('--execute is only for binding an action draft, never validation or results')
    if args.validate:
        if args.reviewed or any(x is not None for x in (args.capture, args.reviewer, args.evidence_note, args.output)):
            parser.error('--validate checks source-free structure only; omit capture, review and output')
    elif not (args.capture and args.reviewer and args.evidence_note and args.reviewed and args.output):
        parser.error('binding requires --capture, --reviewer, --evidence-note, --reviewed and --output')
    value = None
    try:
        value = read_combat_draft(args.result or args.draft)
        if args.result:
            common = dict(session=args.session, action_id=value.get('action_id'))
            result = validate_combat_result(value, **common) if args.validate else write_combat_result(value, **common,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, output=args.output)
        else:
            result = validate_combat_draft(value) if args.validate else write_combat_request(value,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, output=args.output, execute=args.execute)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, AttributeError) as error:
        from veda.helper_timing import record_helper_failure
        ids = value.get('context') if isinstance(value, dict) else None
        timing = record_helper_failure(error, run_id=ids.get('run_id') if isinstance(ids, dict) else None,
            session_path=args.session, output_path=args.output, capture=args.capture)
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'timing': timing}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
