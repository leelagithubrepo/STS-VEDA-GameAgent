#!/usr/bin/env python3
"""Attach scoped menu controls to a reviewed request. Never connects or sends input."""
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
    parser.add_argument('--request', type=Path, required=True,
                        help='Reviewed choice request with explicit menu family and ordinary outcome constraints.')
    parser.add_argument('--output', type=Path, required=True, help='New request file; never overwritten.')
    parser.add_argument('--control-profile', required=True, choices=[CONTROL_PROFILE])
    args = parser.parse_args(argv)
    try:
        result = package(args.request, args.output, control_profile=args.control_profile)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error),
                          'controller_input_sent': False}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
