#!/usr/bin/env python3
"""Package one inspected combat tooltip dismissal. Never captures or sends input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.combat_inspection import (CONTROL_PROFILE, read_inspection_json, validate_inspection_draft,
    validate_inspection_result, write_inspection_request, write_inspection_result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--draft', type=Path, help='Source-free tooltip-clear review; validate before action capture.')
    inputs.add_argument('--result', type=Path, help='Actual tooltip-clear result; no inferred card or enemy facts.')
    parser.add_argument('--session', type=Path, help='Existing state.json read-only; required for results.')
    parser.add_argument('--validate', action='store_true', help='Validate structure only; produces no dispatchable request.')
    parser.add_argument('--execute', action='store_true', help='Package prepare-and-send for the already armed adapter; this helper sends nothing.')
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--reviewer')
    parser.add_argument('--evidence-note')
    parser.add_argument('--reviewed', action='store_true', help='Declare exact fresh image and supplied facts inspected.')
    parser.add_argument('--output', type=Path, help='New packet path; never overwrite.')
    parser.add_argument('--control-profile', required=True, choices=[CONTROL_PROFILE])
    args = parser.parse_args(argv)
    if args.execute and (args.validate or args.result is not None):
        parser.error('--execute requires binding a fresh --draft')
    if (args.result is not None) != (args.session is not None):
        parser.error('--result requires --session; --session applies only to results')
    if args.validate:
        if args.reviewed or any(v is not None for v in (args.capture, args.reviewer, args.evidence_note, args.output)):
            parser.error('--validate is source-free; omit capture, review and output options')
    elif not (args.capture and args.reviewer and args.evidence_note and args.reviewed and args.output):
        parser.error('binding requires --capture, --reviewer, --evidence-note, --reviewed and --output')
    value = None
    try:
        value = read_inspection_json(args.draft or args.result)
        if args.result is not None:
            common = dict(session=args.session, action_id=value.get('action_id'), control_profile=args.control_profile)
            if args.validate:
                result = validate_inspection_result(value, **common)
            else:
                result = write_inspection_result(value, **common, capture=args.capture, reviewer=args.reviewer,
                    evidence_note=args.evidence_note, reviewed=args.reviewed, output=args.output)
        elif args.validate:
            result = validate_inspection_draft(value, args.control_profile)
        else:
            result = write_inspection_request(value, capture=args.capture, reviewer=args.reviewer,
                evidence_note=args.evidence_note, reviewed=args.reviewed, output=args.output,
                control_profile=args.control_profile, execute=args.execute)
    except (ValueError, OSError, RecursionError) as error:
        from veda.helper_timing import record_helper_failure
        ids = value.get('context') if isinstance(value, dict) else None
        timing = record_helper_failure(error, run_id=ids.get('run_id') if isinstance(ids, dict) else None,
            session_path=args.session, output_path=args.output, capture=args.capture)
        print(json.dumps({'request_file': None, 'error': str(error), 'controller_input_sent': False,
                          'timing': timing}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
