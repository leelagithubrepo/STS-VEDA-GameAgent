#!/usr/bin/env python3
"""Prepare routine loot, one retained card choice, or an observed result. No input."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.loot import plan_loot, resolve_snapshot, snapshot_from_result
from veda.loot_results import last_result, observed_result, output_path
from veda.menu_requests import read_menu_draft, write_menu_request
from veda.menu_results import write_menu_result, validate_menu_result
from veda.menu_controls import CONTROL_PROFILE


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path)
    source.add_argument('--after-result', type=Path, help='Reuse exact already-verified actual result.')
    source.add_argument('--last-result', action='store_true')
    source.add_argument('--result', choices=['focus', 'gold', 'offers', 'confirmation', 'acquired', 'returned', 'map'])
    p.add_argument('--decision', type=Path)
    p.add_argument('--choose')
    p.add_argument('--reason')
    p.add_argument('--decision-output', type=Path)
    p.add_argument('--fallback', action='store_true',
                   help='Use the bounded routine fallback when a full potion belt has no replacement decision.')
    p.add_argument('--session', type=Path)
    p.add_argument('--ui', type=Path, help='Actual changed reward rows/card offers, grid and focus.')
    p.add_argument('--actual', type=Path, help='Observed gold or acquired-item delta; not a prediction.')
    p.add_argument('--unchanged', action='store_true', help='Resources, inventory and other facts inspected unchanged.')
    p.add_argument('--focused-id')
    p.add_argument('--hint-button', choices=['cross', 'triangle', 'circle', 'square'])
    p.add_argument('--hint-text')
    p.add_argument('--observed-result')
    p.add_argument('--no-op-reconciliation', action='store_true',
                   help='Reconcile a delivered commit whose exact after-frame proves no semantic change.')
    p.add_argument('--capture', type=Path)
    p.add_argument('--reviewer')
    p.add_argument('--evidence-note')
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--output', type=Path, help='Default: a new packet in this session/loot-packets.')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--verbose', action='store_true', help='Include full draft in a preview.')
    args = p.parse_args(argv)
    try:
        binding = any((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.execute))
        if binding and not all((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.session)):
            raise ValueError('binding requires session, capture, reviewer, evidence-note and reviewed')
        if (args.result or args.after_result or args.last_result) and not args.session:
            raise ValueError('result and result reuse require session')
        if args.result:
            if any((args.choose, args.reason, args.decision, args.decision_output, args.execute)) or not args.observed_result:
                raise ValueError('results need observed-result; omit decision and execute flags')
            if bool(args.hint_button) != bool(args.hint_text):
                raise ValueError('supply both actual confirmation hint button and text')
            hint = {'button': args.hint_button, 'hint_text': args.hint_text} if args.hint_button else None
            draft = observed_result(args.session, args.result, note=args.observed_result,
                unchanged=args.unchanged, focused_id=args.focused_id, hint=hint,
                ui=read_menu_draft(args.ui) if args.ui else None,
                actual=read_menu_draft(args.actual) if args.actual else None,
                no_op_reconciliation=args.no_op_reconciliation)
            if binding:
                result = write_menu_result(draft, session=args.session, action_id=draft['action_id'],
                    capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                    reviewed=args.reviewed, control_profile=CONTROL_PROFILE,
                    output=args.output or output_path(args.session, action_id=draft['action_id']))
            else:
                result = validate_menu_result(draft, session=args.session, action_id=draft['action_id'], control_profile=CONTROL_PROFILE)
        else:
            if any((args.ui, args.actual, args.unchanged, args.focused_id, args.hint_button, args.hint_text, args.observed_result)):
                raise ValueError('observed fields require --result')
            if args.snapshot:
                value = read_menu_draft(args.snapshot)
                if args.session:
                    value = resolve_snapshot(value, args.session)
            else:
                packet = last_result(args.session) if args.last_result else read_menu_draft(args.after_result)
                value = snapshot_from_result(packet, args.session)
            decision = read_menu_draft(args.decision) if args.decision else None
            if args.choose or args.reason:
                if decision or not (args.choose and args.reason):
                    raise ValueError('supply choose and reason together, or reuse decision')
                from veda.loot import decision_key
                decision = {'option_id': args.choose, 'reason': args.reason, 'decision_key': decision_key(value)}
            if args.fallback and decision:
                raise ValueError('fallback cannot be combined with a saved strategic decision')
            result = plan_loot(value, decision, fallback=args.fallback)
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
                    result['options'] = [{k: v for k, v in o.items() if k in {'id', 'label', 'costs', 'role', 'reward'}} for o in result['options']]
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'instruction': 'Repair the unsent declaration or reconcile actual focus/menu; keep the reward decision.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
