#!/usr/bin/env python3
"""Send one request to the existing reviewed adapter; return on its reply."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.adapter_channel import submit, ReplyUnknown


def _pending_recovery(session, result):
    """Reconcile a durable attempted action before bridge preflight.

    Only an exact verify packet already written for the pending action may be
    submitted.  Verification/finalization do not send controller input.
    """
    if not isinstance(result, dict) or result.get('status') not in {'recoverable_review', 'bridge_access_blocked'}:
        return result
    pending = result.get('pending')
    if not isinstance(pending, dict) or pending.get('status') not in {'attempted', 'verified_pending_log'}:
        return result
    original_delivery = result.get('controller_input_sent')
    if pending.get('status') == 'verified_pending_log':
        finalized = submit(session, {'operation': 'finalize'})
        if finalized.get('status') == 'run_complete':
            return {**finalized, 'controller_input_sent': False,
                    'recovery': {'automatic': True, 'packet_found': False, 'input_replayed': False,
                                 'original_controller_input_sent': original_delivery}}
        if finalized.get('status') in {'verified', 'finalized', 'run_complete'}:
            return {'status': 'pending_recovered', 'ready': False, 'armed': bool(result.get('armed')),
                    'controller_input_sent': False,
                    'next_operation': 'resume_play' if result.get('armed') else 'bridge_preflight',
                    'recovery': {'automatic': True, 'packet_found': False, 'input_replayed': False,
                                 'finalized': True, 'original_controller_input_sent': original_delivery}}
        return {**finalized, 'status': 'pending_reconciliation_required',
                'controller_input_sent': False, 'next_operation': 'finalize',
                'required': finalized.get('required') or 'Finalize the retained verified result before arming. Never resend.',
                'recovery': {'automatic': True, 'packet_found': False, 'input_replayed': False,
                             'original_controller_input_sent': original_delivery}}
    from veda.pending_recovery import find_matching_verify_request
    packet = find_matching_verify_request(session, pending.get('action_id'))
    if packet is None:
        result = dict(result)
        result.update(status='pending_reconciliation_required', ready=False,
                      controller_input_sent=False, next_operation='verify',
                      required=('Capture and inspect a fresh settled after-image, then submit an exact '
                                 'verify packet for this action_id. Never resend the attempted input.'),
                      recovery={'automatic': True, 'packet_found': False, 'input_replayed': False,
                                'original_controller_input_sent': original_delivery})
        return result
    verified = submit(session, {'request_file': str(packet)})
    if verified.get('status') == 'run_complete':
        return {**verified, 'controller_input_sent': False,
                'recovery': {'automatic': True, 'packet_found': True, 'packet': str(packet),
                             'input_replayed': False, 'original_controller_input_sent': original_delivery}}
    if verified.get('status') not in {'verified', 'finalized'}:
        return {**verified, 'status': 'pending_reconciliation_required',
                'controller_input_sent': False,
                'next_operation': verified.get('next_operation', 'verify'),
                'required': verified.get('required') or 'Correct only the verify packet; never resend the attempted input.',
                'recovery': {'automatic': True, 'packet_found': True, 'packet': str(packet), 'input_replayed': False,
                             'original_controller_input_sent': original_delivery}}
    # ReviewedPlaySession.verify normally finalizes atomically and returns
    # ``verified``. Only legacy/repair replies explicitly requesting a second
    # finalize need that follow-up operation.
    if verified.get('requires_finalize'):
        finalized = submit(session, {'operation': 'finalize'})
    else:
        finalized = verified
    if finalized.get('status') not in {'finalized', 'verified', 'run_complete'}:
        return {**finalized, 'status': 'pending_reconciliation_required',
                'controller_input_sent': False,
                'required': 'Result verified but not finalized; submit finalize before arming. Never resend.',
                'recovery': {'automatic': True, 'packet_found': True, 'packet': str(packet), 'input_replayed': False}}
    if finalized.get('status') == 'run_complete':
        return {**finalized, 'controller_input_sent': False,
                'recovery': {'automatic': True, 'packet_found': True, 'packet': str(packet),
                             'input_replayed': False, 'original_controller_input_sent': original_delivery}}
    return {'status': 'pending_recovered', 'ready': False, 'armed': bool(result.get('armed')),
            'controller_input_sent': False,
            'next_operation': 'resume_play' if result.get('armed') else 'bridge_preflight',
            'recovery': {'automatic': True, 'packet_found': True, 'packet': str(packet),
                         'input_replayed': False, 'finalized': True,
                         'original_controller_input_sent': original_delivery}}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=not bool(os.environ.get('VEDA_PLAY_SESSION')), type=Path,
                   default=os.environ.get('VEDA_PLAY_SESSION'), help='Explicit session; defaults to this launcher process’s VEDA_PLAY_SESSION.')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--request', type=Path, help='Existing ordinary request packet; sent exactly once.')
    source.add_argument('--operation', choices=['summary', 'bridge_preflight', 'finalize', 'stop'])
    p.add_argument('--capture-after', action='store_true', help='Capture only after acknowledged input; inspect the image before verifying.')
    p.add_argument('--window-id', type=int, default=(int(os.environ['VEDA_GAME_WINDOW_ID'])
                   if os.environ.get('VEDA_GAME_WINDOW_ID', '').isdigit() else None),
                   help='Reuse the selected QuickTime Movie Recording window for capture-after.')
    args = p.parse_args(argv)
    # A stale terminal command can arrive after reconciliation has already
    # cleared the durable pending action. Treat finalize as an idempotent
    # no-op instead of reopening the old recovery loop.
    if args.operation == 'finalize':
        session_path = Path(args.session)
        state_path = session_path if session_path.is_file() else session_path / 'state.json'
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if not state.get('pending'):
                print(json.dumps({'status': 'no_pending_action', 'ready': False,
                                  'controller_input_sent': False,
                                  'next_operation': 'summary',
                                  'required': 'Refresh the current screen and prepare a new action.'}))
                return 0
        except (OSError, ValueError, TypeError):
            pass
    request = {'request_file': str(args.request.resolve())} if args.request else {'operation': args.operation}
    try:
        result = submit(args.session, request)
        if args.operation == 'bridge_preflight':
            result = _pending_recovery(args.session, result)
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
            result['observation_path'] = str(capture_game_window(DEFAULT_CAPTURE_DIR, window_id=args.window_id))
            result['observation_reviewed'] = False
        except Exception as error:
            result['capture_error'] = str(error)
            result['required'] = 'Input was sent. Capture and inspect its result; never resend.'
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
