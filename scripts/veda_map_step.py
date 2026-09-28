#!/usr/bin/env python3
"""Reuse a map route and package one checked step or focus result. No input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.map_survey import read_json
from veda.map_travel import focus_result, focus_snapshot, plan_map
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_requests import write_menu_request
from veda.menu_results import write_menu_result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--snapshot', type=Path, help='Current inspected map facts; reuse during focus navigation.')
    mode.add_argument('--focus-result', metavar='NODE_ID', help='Actual observed focus after the exact pending map tap.')
    p.add_argument('--cache', type=Path, help='Private route/map cache for this run and act.')
    p.add_argument('--cache-output', type=Path, help='New cache version; no file is overwritten.')
    p.add_argument('--decision', type=Path, help='Choose node_ids and reason once; reuse the cache thereafter.')
    p.add_argument('--view', type=Path, action='append', default=[], help='Optional new bound static map view; no resurvey required.')
    p.add_argument('--focus', help='Actual focus in the inspected frame; preserve the other snapshot fields.')
    p.add_argument('--unchanged', action='store_true', help='Explicitly reviewed all other map facts/resources/inventory/options as unchanged.')
    p.add_argument('--session', type=Path)
    p.add_argument('--action-id')
    p.add_argument('--observed-result')
    p.add_argument('--capture', type=Path)
    p.add_argument('--reviewer')
    p.add_argument('--evidence-note')
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    try:
        binding = any((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.execute, args.session))
        if binding and not all((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.session)):
            raise ValueError('binding requires session, capture, reviewer, evidence-note, reviewed and output')
        if args.focus_result is not None:
            if (not binding or not args.action_id or not args.observed_result or args.execute
                    or any((args.cache, args.cache_output, args.decision, args.view, args.focus))):
                raise ValueError('focus result needs action-id, observed-result, review/binding fields; no execute or planning flags')
            draft = focus_result(args.session, args.action_id, args.focus_result,
                                 unchanged=args.unchanged, observed_result=args.observed_result)
            result = write_menu_result(draft, session=args.session, action_id=args.action_id,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, control_profile=CONTROL_PROFILE, output=args.output)
        else:
            if args.action_id or args.observed_result or args.unchanged and args.focus is None:
                raise ValueError('action-id/observed-result are result-only; unchanged requires focus')
            snapshot = read_json(args.snapshot)
            if args.focus is not None:
                snapshot = focus_snapshot(snapshot, args.focus, unchanged=args.unchanged)
            result = plan_map(snapshot, read_json(args.cache) if args.cache else None,
                              read_json(args.decision) if args.decision else None,
                              [read_json(view) for view in args.view])
            if args.cache_output:
                # Private immutable versions cannot overwrite a session or source.
                with args.cache_output.open('x') as stream:
                    json.dump(result['cache'], stream, indent=2, sort_keys=True, allow_nan=False)
                    stream.write('\n')
                result['cache_file'] = str(args.cache_output.resolve())
            if binding and result['status'] == 'planned':
                result = write_menu_request(result['draft'], capture=args.capture, session=args.session,
                    reviewer=args.reviewer, evidence_note=args.evidence_note, reviewed=args.reviewed,
                    control_profile=CONTROL_PROFILE, output=args.output, execute=args.execute)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'instruction': 'Repair the unsent map declaration or choose from the current reachable nodes; no controller input was sent.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
