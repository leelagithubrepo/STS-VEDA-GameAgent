#!/usr/bin/env python3
"""Package a merchant action or inspected result. Never sends controller input."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.map_survey import read_json
from veda.shop import plan_shop, resolve_snapshot, snapshot_from_result
from veda.shop_results import last_result, observed_result, output_path
from veda.menu_requests import write_menu_request
from veda.menu_results import write_menu_result, validate_menu_result
from veda.menu_controls import CONTROL_PROFILE


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path)
    source.add_argument('--after-result', type=Path, help='Exact already-verified result.')
    source.add_argument('--last-result', action='store_true', help='Reuse the sealed last verified merchant result from this session.')
    source.add_argument('--result', choices=['opened', 'focus', 'confirmation', 'purchased', 'left', 'map'])
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--decision', type=Path)
    p.add_argument('--choose')
    p.add_argument('--reason')
    p.add_argument('--decision-output', type=Path)
    p.add_argument('--focused-id', help='Actual observed focus or selected item in a result.')
    p.add_argument('--stock', type=Path, help='Inspected priced slots, actual focus and Leave hint, for an opened result.')
    p.add_argument('--actual', type=Path, help='Small explicitly observed purchase delta; never a prediction.')
    p.add_argument('--unchanged', action='store_true', help='Inspected resources, inventory, facts and UI details not supplied as changed as unchanged.')
    p.add_argument('--hint-button', choices=['cross', 'triangle', 'circle'])
    p.add_argument('--hint-text')
    p.add_argument('--observed-result')
    p.add_argument('--capture', type=Path)
    p.add_argument('--reviewer')
    p.add_argument('--evidence-note')
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--output', type=Path, help='Optional new packet path; default is generated within this session.')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--verbose', action='store_true', help='Include full draft in a source-free preview.')
    args = p.parse_args(argv)
    try:
        binding = any((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.execute))
        if binding and not all((args.capture, args.reviewer, args.evidence_note, args.reviewed)):
            raise ValueError('binding requires capture, reviewer, evidence-note and reviewed')
        if args.result:
            if any((args.choose, args.reason, args.decision, args.decision_output, args.execute)) or not args.observed_result:
                raise ValueError('results need observed-result; omit decision and execute flags')
            if bool(args.hint_button) != bool(args.hint_text):
                raise ValueError('supply both the actual hint button and hint text')
            hint = {'button': args.hint_button, 'hint_text': args.hint_text} if args.hint_button else None
            draft = observed_result(args.session, args.result, note=args.observed_result,
                unchanged=args.unchanged, focused_id=args.focused_id, hint=hint,
                stock=read_json(args.stock) if args.stock else None, actual=read_json(args.actual) if args.actual else None)
            if binding:
                result = write_menu_result(draft, session=args.session, action_id=draft['action_id'],
                    capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                    reviewed=args.reviewed, control_profile=CONTROL_PROFILE,
                    output=args.output or output_path(args.session, action_id=draft['action_id']))
            else:
                result = validate_menu_result(draft, session=args.session, action_id=draft['action_id'], control_profile=CONTROL_PROFILE)
        else:
            if any((args.focused_id, args.stock, args.actual, args.unchanged, args.hint_button, args.hint_text, args.observed_result)):
                raise ValueError('observed result fields require --result')
            packet = last_result(args.session) if args.last_result else read_json(args.after_result) if args.after_result else None
            value = resolve_snapshot(read_json(args.snapshot), args.session) if args.snapshot else snapshot_from_result(packet, args.session)
            decision = read_json(args.decision) if args.decision else None
            if args.choose or args.reason:
                if decision or not (args.choose and args.reason):
                    raise ValueError('choose and reason together, or reuse decision')
                preview = plan_shop(value)
                key = preview.get('decision_key', preview.get('decision', {}).get('decision_key'))
                decision = {'option_id': args.choose, 'reason': args.reason, 'decision_key': key}
            result = plan_shop(value, decision)
            if args.decision_output and result['status'] == 'planned':
                with args.decision_output.open('x') as out:
                    json.dump(result['decision'], out, indent=2); out.write('\n')
            if binding and result['status'] == 'planned':
                result = write_menu_request(result['draft'], capture=args.capture, session=args.session,
                    reviewer=args.reviewer, evidence_note=args.evidence_note, reviewed=args.reviewed,
                    control_profile=CONTROL_PROFILE, output=args.output or output_path(args.session), execute=args.execute)
            elif not args.verbose:
                result.pop('draft', None)
                if 'options' in result:
                    result['options'] = [{k: v for k, v in o.items() if k in {'id', 'label', 'costs', 'role'}} for o in result['options']]
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'instruction': 'Repair the unsent declaration or inspect the actual focus/menu; keep playing without writing a new Python script.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
