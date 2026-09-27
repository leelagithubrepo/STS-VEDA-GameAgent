#!/usr/bin/env python3
"""Build compact menu actions or results from inspected images. Never sends input."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.choice_execution import plan_choice_step
from veda.menu_controls import bind_reviewed_menu_controls
from veda.neow_start import CONTROL_PROFILE
from veda.reviewed_play import inventory_digest
from veda.saved_frame_reader import _identity


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key in reviewed request')
        value[key] = item
    return value


def _nonfinite(_):
    raise ValueError('reviewed request must contain finite JSON values')


def package(request_path, output, *, control_profile, now=None):
    with Path(request_path).open('rb') as stream:
        data = stream.read(256_001)
    if len(data) > 256_000:
        raise ValueError('reviewed request exceeds its byte bound')
    request = json.loads(data, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if (not isinstance(request, dict) or request.get('operation') != 'prepare'
            or request.get('kind') != 'choice'):
        raise ValueError('an ordinary reviewed choice prepare request is required')
    observation, source = request['observation'], request['source']
    frame = observation['frame']
    if (request['context'] != observation['context']
            or inventory_digest(request['inventory']) != observation['inventory_digest']
            or request['review'] != observation['review']
            or source['sha256'] != frame['image_sha256']
            or source['captured_at'] != frame['observed_at']):
        raise ValueError('request review, source, context or inventory differs')
    source_path = Path(source['path'])
    if _identity(source_path)[0] != source['sha256']:
        raise ValueError('reviewed source bytes changed')
    now = now or datetime.now(timezone.utc)
    request['observation'] = bind_reviewed_menu_controls(
        observation, control_profile=control_profile, now=now, max_age_seconds=30)
    plan_choice_step(request['observation'], request['choice'],
                     now=now, max_age_seconds=30)
    payload = json.dumps(request, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if _identity(source_path)[0] != source['sha256']:
        raise ValueError('reviewed source changed during packaging')
    destination = Path(output).expanduser()
    # Never overwrite a source, existing request or live session file.
    with destination.open('x') as stream:
        stream.write(payload)
    return {'request_file': str(destination.resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--draft', type=Path,
                        help='Source-free menu decision; validate it before taking the action image.')
    inputs.add_argument('--request', type=Path,
                        help='Legacy complete reviewed request; prefer --draft for live preparation.')
    inputs.add_argument('--result', type=Path,
                        help='Compact actual result; derive correlation and mutation records from the pending action.')
    parser.add_argument('--session', type=Path, help='Existing state.json; required only with --result, read-only.')
    parser.add_argument('--validate', action='store_true',
                        help='Check only the draft; produces no action request or controller authority.')
    parser.add_argument('--capture', type=Path, help='Exact inspected PNG with original capture receipt.')
    parser.add_argument('--reviewer', help='Name of the person or advisor inspecting the exact image.')
    parser.add_argument('--evidence-note', help='What was actually inspected and any source limitations.')
    parser.add_argument('--reviewed', action='store_true',
                        help='Declare the draft facts and selected choice match this exact fresh image.')
    parser.add_argument('--output', type=Path, help='New request file; never overwritten.')
    parser.add_argument('--control-profile', required=True, choices=[CONTROL_PROFILE])
    args = parser.parse_args(argv)
    if args.result is not None and args.session is None:
        parser.error('--result requires --session pointing to the existing state.json')
    if args.result is None and args.session is not None:
        parser.error('--session is only valid with --result')
    if args.request is not None:
        if (args.validate or args.reviewed or any(value is not None for value in
                (args.capture, args.reviewer, args.evidence_note))):
            parser.error('draft/capture review options cannot be combined with --request')
        if not args.output:
            parser.error('--request requires --output')
    elif args.validate:
        if (args.reviewed or any(value is not None for value in
                (args.output, args.capture, args.reviewer, args.evidence_note))):
            parser.error('--validate checks a source-free draft only; omit capture, review and output options')
    elif not (args.capture and args.reviewer and args.evidence_note and args.reviewed and args.output):
        parser.error('--draft preparation requires --capture, --reviewer, --evidence-note, --reviewed and --output')
    try:
        if args.request:
            result = package(args.request, args.output, control_profile=args.control_profile)
        elif args.result is not None:
            from veda.menu_requests import read_menu_draft
            from veda.menu_results import validate_menu_result, write_menu_result
            draft = read_menu_draft(args.result)
            common = dict(session=args.session, action_id=draft.get('action_id'),
                          control_profile=args.control_profile)
            if args.validate:
                result = validate_menu_result(draft, **common)
            else:
                result = write_menu_result(draft, **common, capture=args.capture, reviewer=args.reviewer,
                    evidence_note=args.evidence_note, reviewed=args.reviewed, output=args.output)
        else:
            from veda.menu_requests import read_menu_draft, validate_menu_draft, write_menu_request
            draft = read_menu_draft(args.draft)
            if args.validate:
                result = validate_menu_draft(draft, control_profile=args.control_profile)
            else:
                result = write_menu_request(draft, capture=args.capture, reviewer=args.reviewer,
                    evidence_note=args.evidence_note, reviewed=args.reviewed,
                    control_profile=args.control_profile, output=args.output)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error),
                          'controller_input_sent': False}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
