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
    modes.add_argument('--last-result', action='store_true', help='Reuse the sealed actual state and incomplete card decision.')
    modes.add_argument('--after-result', type=Path, help='Reuse an exact verified combat result packet.')
    modes.add_argument('--focus-result', metavar='CARD_ID', help='Actual observed hand focus; requires --unchanged.')
    modes.add_argument('--selection-result', metavar='CARD_ID', help='Actual selected card; optional --target for targeting.')
    modes.add_argument('--ui-result', type=Path, help='Actual complete UI, including unexpected enemy/player tooltip focus.')
    modes.add_argument('--actual-result', type=Path, help='Actual changed state/UI and others_unchanged declaration.')
    modes.add_argument('--boundary-result', type=Path, help='Actual victory/defeat/selection boundary; unchanged inventory declaration required.')
    parser.add_argument('--unchanged', action='store_true')
    parser.add_argument('--observed-result')
    parser.add_argument('--tooltip-kind', choices=('none', 'card_keyword'), default='none')
    parser.add_argument('--card', help='Choose the next actual card ID after the previous card resolves.')
    parser.add_argument('--target', help='Chosen enemy ID, or actual target for a selection result.')
    parser.add_argument('--reason')
    parser.add_argument('--plan', type=Path, help='Explicit next plan, including End Turn; requires reason.')
    parser.add_argument('--bounded-hand', action='store_true', help='Up to four known directional hand steps; verify final focus before select.')
    parser.add_argument('--single-step', action='store_true', help='Use one direction press at a time.')
    parser.add_argument('--session', type=Path, help='Existing state.json; binds action evidence to its input epoch, required for results.')
    parser.add_argument('--validate', action='store_true', help='Validate before capture; no executable request is produced.')
    parser.add_argument('--capture', type=Path, help='Exact inspected image with original capture receipt.')
    parser.add_argument('--reviewer')
    parser.add_argument('--evidence-note')
    parser.add_argument('--reviewed', action='store_true', help='Declare every supplied fact matches the exact image.')
    parser.add_argument('--output', type=Path, help='New packet path; never overwritten.')
    parser.add_argument('--execute', action='store_true', help='Package one atomic prepare-and-send request for an already armed adapter.')
    args = parser.parse_args(argv)
    compact_result = any(x is not None for x in (args.focus_result, args.selection_result, args.ui_result,
                                                args.actual_result, args.boundary_result))
    result_mode = bool(args.result or compact_result)
    if (result_mode or args.last_result or args.after_result) and not args.session:
        parser.error('results and reuse require --session')
    if args.execute and (result_mode or args.validate):
        parser.error('--execute is only for binding an action draft')
    if args.validate:
        if args.reviewed or any(x is not None for x in (args.capture, args.reviewer, args.evidence_note, args.output)):
            parser.error('--validate checks structure only; omit capture, review and output')
    elif not (args.capture and args.reviewer and args.evidence_note and args.reviewed and (args.output or args.session)):
        parser.error('binding needs capture, reviewer, evidence-note, reviewed and session or output')
    value = None
    try:
        from veda.combat_flow import last_snapshot, snapshot_from_result, observed_result, output_path
        if args.bounded_hand and args.single_step:
            raise ValueError('choose bounded-hand or single-step')
        if result_mode and any((args.card, args.plan, args.reason, args.bounded_hand, args.single_step)):
            raise ValueError('result review cannot contain planning flags')
        if not compact_result and (args.unchanged or args.observed_result):
            raise ValueError('unchanged/observed-result are compact result fields')
        if compact_result:
            value = observed_result(args.session, note=args.observed_result, unchanged=args.unchanged,
                focus=args.focus_result, selected=args.selection_result, target=args.target,
                ui=read_combat_draft(args.ui_result) if args.ui_result else None,
                actual=read_combat_draft(args.actual_result) if args.actual_result else None,
                boundary=read_combat_draft(args.boundary_result) if args.boundary_result else None,
                tooltip=args.tooltip_kind)
        else:
            value = (last_snapshot(args.session) if args.last_result else
                     snapshot_from_result(read_combat_draft(args.after_result), args.session) if args.after_result else
                     read_combat_draft(args.result or args.draft))
        if not result_mode:
            if value.get('context') == 'session':
                from veda.shop import session_context
                value['context'] = session_context(args.session)
            if args.card or args.plan:
                if not args.reason or args.card and args.plan:
                    raise ValueError('one next card or plan and its reason required')
                value['plan'] = (read_combat_draft(args.plan) if args.plan else
                    {'steps': [{'kind': 'card', 'card_id': args.card, **({'target': args.target} if args.target else {})}]})
                value['reasoning'] = args.reason
            elif args.reason or args.target:
                raise ValueError('reason/target need an explicit next card or plan')
            if 'plan' not in value:
                raise ValueError('no retained decision; supply the current card/plan and reason')
            if args.single_step:
                value['ui']['navigation_mode'] = 'single'
            elif args.bounded_hand:
                value['ui']['navigation_mode'] = 'bounded_hand'
        if not args.validate and not args.output:
            args.output = output_path(args.session, result=result_mode)
        if result_mode:
            common = dict(session=args.session, action_id=value.get('action_id'))
            result = validate_combat_result(value, **common) if args.validate else write_combat_result(value, **common,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, output=args.output)
        else:
            result = validate_combat_draft(value) if args.validate else write_combat_request(value,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note,
                reviewed=args.reviewed, output=args.output, execute=args.execute, session=args.session)
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
