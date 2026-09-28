#!/usr/bin/env python3
"""Package a merchant action using canonical session IDs. Never sends input."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.map_survey import read_json
from veda.shop import plan_shop, resolve_snapshot, snapshot_from_result
from veda.menu_requests import write_menu_request
from veda.menu_controls import CONTROL_PROFILE


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path)
    source.add_argument('--after-result', type=Path, help='Exact already-verified result; reuse observed facts and learned focus.')
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--decision', type=Path)
    p.add_argument('--choose', help='Choose an actual option once; helper supplies its decision key.')
    p.add_argument('--reason')
    p.add_argument('--decision-output', type=Path, help='New saved decision file for reuse through focus taps.')
    p.add_argument('--capture', type=Path)
    p.add_argument('--reviewer')
    p.add_argument('--evidence-note')
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    try:
        value = (resolve_snapshot(read_json(args.snapshot), args.session) if args.snapshot else
                 snapshot_from_result(read_json(args.after_result), args.session))
        decision = read_json(args.decision) if args.decision else None
        if args.choose or args.reason:
            if decision or not (args.choose and args.reason):
                raise ValueError('choose and reason together, or reuse decision')
            preview = plan_shop(value)
            key = preview.get('decision_key', preview.get('decision', {}).get('decision_key'))
            decision = {'option_id': args.choose, 'reason': args.reason, 'decision_key': key}
        result = plan_shop(value, decision)
        binding = any((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.execute))
        if binding and not all((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output)):
            raise ValueError('binding requires capture, reviewer, evidence-note, reviewed and output')
        if args.decision_output and result['status'] == 'planned':
            with args.decision_output.open('x') as out:
                json.dump(result['decision'], out, indent=2); out.write('\n')
        if binding and result['status'] == 'planned':
            result = write_menu_request(result['draft'], capture=args.capture, session=args.session,
                reviewer=args.reviewer, evidence_note=args.evidence_note, reviewed=args.reviewed,
                control_profile=CONTROL_PROFILE, output=args.output, execute=args.execute)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'instruction': 'Repair the unsent declaration or inspect the actual focus/menu; keep playing without writing a new Python script.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
