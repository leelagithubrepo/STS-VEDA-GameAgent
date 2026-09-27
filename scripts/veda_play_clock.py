#!/usr/bin/env python3
"""Measure reviewed-play progress; never connects, arms or sends game input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.play_timing import PlayTiming


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('start', 'status', 'begin_move', 'begin_floor',
                                            'phase', 'pause', 'resume', 'stale_capture'))
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--move-id')
    parser.add_argument('--floor-id')
    parser.add_argument('--kind')
    parser.add_argument('--label')
    parser.add_argument('--name')
    parser.add_argument('--reason')
    parser.add_argument('--category', choices=('paused', 'user_wait'))
    parser.add_argument('--capture-id')
    args = parser.parse_args(argv)
    path = args.session / 'timing.json'
    if args.operation != 'start' and not path.exists():
        parser.error('start the play clock before recording progress')
    try:
        timing = PlayTiming(path, run_id=args.run_id)
        fields = {key: value for key, value in vars(args).items()
                  if key not in {'operation', 'session', 'run_id'} and value is not None}
        if args.operation not in {'start', 'status'}:
            timing.event(args.operation, **fields)
        print(json.dumps({'timing': timing.summary(), 'controller_input_sent': False}, allow_nan=False))
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({'error': str(error), 'controller_input_sent': False}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
