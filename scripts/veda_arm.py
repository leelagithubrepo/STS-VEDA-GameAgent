#!/usr/bin/env python3
"""Prepare arming before image review; never connect, arm or send controller input."""
import argparse
import json
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.arm_preparation import (confirm_arm_review, read_document, stage_arm_review,
                                  validate_arm_draft, validate_outputs)
from veda.helper_timing import record_helper_failure
from veda.play_requests import PlayRequestError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('--draft', type=Path, required=True)
    stage = commands.add_parser('stage')
    stage.add_argument('--draft', type=Path, required=True)
    captures = stage.add_mutually_exclusive_group(required=True)
    captures.add_argument('--capture', type=Path)
    captures.add_argument('--capture-new', action='store_true')
    stage.add_argument('--output', type=Path, required=True)
    stage.add_argument('--request-output', type=Path, required=True)
    confirm = commands.add_parser('confirm')
    confirm.add_argument('--stage', type=Path, required=True)
    confirm.add_argument('--stage-sha256', required=True)
    confirm.add_argument('--run-id', required=True)
    confirm.add_argument('--screen', required=True)
    confirm.add_argument('--note', required=True)
    confirm.add_argument('--reviewed', action='store_true', required=True)
    args = parser.parse_args(argv)
    draft, capture, output = {}, None, None
    try:
        if args.operation in {'validate', 'stage'}:
            draft = read_document(args.draft)
            result = validate_arm_draft(draft)
            if args.operation == 'stage':
                output = args.output
                validate_outputs(args.output, args.request_output)
                if args.capture_new:
                    from veda.game_capture import capture_game_window
                    from veda.observation import DEFAULT_CAPTURE_DIR
                    capture = capture_game_window(DEFAULT_CAPTURE_DIR)
                else:
                    capture = args.capture
                result = stage_arm_review(draft, capture=capture, output=args.output,
                                          request_output=args.request_output)
        else:
            existing = read_document(args.stage)
            draft = existing.get('draft', {})
            source = existing.get('identity', {}).get('source', {}) if isinstance(existing.get('identity'), dict) else {}
            capture = source.get('path') if isinstance(source, dict) else None
            output = existing.get('request_output')
            result = confirm_arm_review(args.stage, stage_sha256=args.stage_sha256,
                run_id=args.run_id, screen=args.screen, note=args.note, reviewed=args.reviewed)
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        timing = record_helper_failure(error, run_id=draft.get('run_id') if isinstance(draft, dict) else None,
                                        output_path=output, capture=capture)
        print(json.dumps({'status': 'needs_review', 'error': str(error), 'request_file': None,
                          'timing': timing, 'controller_input_sent': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
