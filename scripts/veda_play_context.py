#!/usr/bin/env python3
"""Print compact recorded play context. Read-only; historical evidence is not live state."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.play_context import DEFAULT_DATABASE, read_play_context


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DEFAULT_DATABASE)
    parser.add_argument('--run-id', help='Select an exact active run when more than one is recorded.')
    parser.add_argument('--sessions-root', type=Path, help='Reviewed-play directory; defaults beside the database.')
    args = parser.parse_args(argv)
    try:
        result = read_play_context(args.database, run_id=args.run_id, sessions_root=args.sessions_root)
    except (ValueError, OSError) as error:
        print(json.dumps({'schema': 'veda.play-context.v1', 'status': 'error', 'error': str(error),
                          'live': False, 'controller_authorized': False, 'runtime_authorized': False}), flush=True)
        return 2
    print(json.dumps(result, allow_nan=False, separators=(',', ':')), flush=True)
    return 0 if result['selection_status'] == 'selected' else 2


if __name__ == '__main__':
    raise SystemExit(main())
