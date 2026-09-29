#!/usr/bin/env python3
"""Reuse a map route and package one checked step or focus result. No input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.map_survey import read_json
from veda.map_travel import (focus_result, focus_snapshot, plan_map, last_snapshot,
                             snapshot_from_result, reuse_verified_focus, load_session_cache, save_cache)
from veda.menu_controls import CONTROL_PROFILE
from veda.menu_requests import write_menu_request
from veda.menu_results import write_menu_result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--snapshot', type=Path, help='Current inspected map facts; reuse during focus navigation.')
    mode.add_argument('--last-result', action='store_true', help='Reuse the sealed verified actual map state from this session.')
    mode.add_argument('--after-result', type=Path, help='Reuse an exact sealed verified map result when it is not retained in the session.')
    mode.add_argument('--focus-result', metavar='NODE_ID', help='Actual observed focus after the exact pending map tap.')
    mode.add_argument('--reobserve-result', type=Path, help='Actual corrected map snapshot after pending focus; never claims room entry.')
    p.add_argument('--connections', type=Path, help='Reviewed current outgoing edges; excludes future forks.')
    p.add_argument('--fresh-cache', action='store_true', help='Retain old cache files but start new planning memory.')
    p.add_argument('--verbose', action='store_true', help='Include full draft/cache in a planning preview.')
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
    p.add_argument('--output', type=Path, help='Optional new path; otherwise generated inside the session.')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    try:
        binding = any((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.execute))
        if binding and not all((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.session)):
            raise ValueError('binding requires session, capture, reviewer, evidence-note and reviewed')
        if (args.last_result or args.after_result) and not args.session:
            raise ValueError('verified result reuse needs its session')
        if binding and not args.output:
            from uuid import uuid4
            root = args.session if args.session.is_dir() else args.session.parent
            directory = root/'map-packets'
            directory.mkdir(exist_ok=True)
            args.output = directory/('result-' if args.focus_result or args.reobserve_result else 'action-')
            args.output = args.output.with_name(args.output.name + uuid4().hex + '.json')
        if args.reobserve_result:
            if not binding or not args.unchanged or not args.observed_result or args.execute:
                raise ValueError('map reobservation needs exact result review, observed-result and unchanged resources/facts/inventory')
            from veda.menu_results import read_pending
            root = args.session if args.session.is_dir() else args.session.parent
            action_id = (read_json(root/'state.json').get('pending') or {}).get('action_id')
            pending, _ = read_pending(args.session, action_id)
            snapshot = read_json(args.reobserve_result)
            if snapshot.get('context') == 'session':
                snapshot['context'] = pending['request']['context']
            if args.connections:
                from veda.map_edges import immediate_snapshot
                snapshot = immediate_snapshot(snapshot, read_json(args.connections))
            before = pending['request']; obs = before['observation']
            if snapshot['context'] not in ('session', before['context']) or any(
                snapshot[k] != before['inventory'] if k == 'inventory' else snapshot[k] != obs[k]
                for k in ('resources', 'facts', 'inventory')):
                raise ValueError('map focus correction cannot change gameplay facts/resources/inventory/context')
            draft = {'schema':'veda.menu-result.v1','action_id':action_id,'resources':'unchanged',
                'facts':'unchanged','inventory':'unchanged','result':{'kind':'map_reobservation','ui':snapshot['ui']},
                'observed_result':args.observed_result}
            result = write_menu_result(draft, session=args.session, action_id=action_id,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, control_profile=CONTROL_PROFILE, output=args.output)
        elif args.focus_result is not None:
            if (not binding or not args.observed_result or args.execute
                    or any((args.cache, args.cache_output, args.decision, args.view, args.focus))):
                raise ValueError('focus result needs observed-result, review/binding fields; no execute or planning flags')
            if not args.action_id:
                root = args.session if args.session.is_dir() else args.session.parent
                args.action_id = (read_json(root/'state.json').get('pending') or {}).get('action_id')
            draft = focus_result(args.session, args.action_id, args.focus_result,
                                 unchanged=args.unchanged, observed_result=args.observed_result)
            result = write_menu_result(draft, session=args.session, action_id=args.action_id,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, control_profile=CONTROL_PROFILE, output=args.output)
        else:
            if args.action_id or args.observed_result or args.unchanged and args.focus is None:
                raise ValueError('action-id/observed-result are result-only; unchanged requires focus')
            snapshot = (read_json(args.snapshot) if args.snapshot else last_snapshot(args.session)
                        if args.last_result else snapshot_from_result(read_json(args.after_result), args.session))
            if args.session:
                from veda.shop import resolve_snapshot
                snapshot = resolve_snapshot(snapshot, args.session)
            if args.connections:
                from veda.map_edges import immediate_snapshot
                snapshot = immediate_snapshot(snapshot, read_json(args.connections))
            if args.focus is not None:
                snapshot = focus_snapshot(snapshot, args.focus, unchanged=args.unchanged)
            elif binding and args.snapshot:
                snapshot = reuse_verified_focus(snapshot, args.session, args.capture)
            result = plan_map(snapshot, read_json(args.cache) if args.cache else load_session_cache(args.session, snapshot) if args.session and not args.fresh_cache else None,
                              read_json(args.decision) if args.decision else None,
                              [read_json(view) for view in args.view])
            if args.cache_output or binding and args.session:
                result['cache_file'] = save_cache(result['cache'], output=args.cache_output,
                                                  session=args.session if binding else None)
            if binding and result['status'] == 'planned':
                result = write_menu_request(result['draft'], capture=args.capture, session=args.session,
                    reviewer=args.reviewer, evidence_note=args.evidence_note, reviewed=args.reviewed,
                    control_profile=CONTROL_PROFILE, output=args.output, execute=args.execute)
        if not args.verbose:
            result = {k:v for k,v in result.items() if k not in {'cache','draft'}}
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'instruction': 'Repair the unsent map declaration or choose from the current reachable nodes; no controller input was sent.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
