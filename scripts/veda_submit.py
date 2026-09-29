#!/usr/bin/env python3
"""Send one request to the existing reviewed adapter; return on its reply."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.adapter_channel import submit, ReplyUnknown


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=True, type=Path)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--request', type=Path, help='Existing ordinary request packet; sent exactly once.')
    source.add_argument('--operation', choices=['summary', 'bridge_preflight', 'finalize', 'stop'])
    p.add_argument('--capture-after', action='store_true', help='Capture only after acknowledged input; inspect the image before verifying.')
    args = p.parse_args(argv)
    request = {'request_file': str(args.request.resolve())} if args.request else {'operation': args.operation}
    try:
        result = submit(args.session, request)
    except ReplyUnknown as error:
        print(json.dumps({'status': 'reply_unknown', 'reason': str(error), 'must_not_repeat': True,
                          'controller_input_sent': None}))
        return 2
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'submission_not_sent', 'reason': str(error), 'controller_input_sent': False}))
        return 2
    if args.capture_after and result.get('status') == 'awaiting_fresh_review':
        try:
            from veda.game_capture import capture_game_window
            from veda.observation import DEFAULT_CAPTURE_DIR
            result['observation_path'] = str(capture_game_window(DEFAULT_CAPTURE_DIR))
            result['observation_reviewed'] = False
        except Exception as error:
            result['capture_error'] = str(error)
            result['required'] = 'Input was sent. Capture and inspect its result; never resend.'
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
