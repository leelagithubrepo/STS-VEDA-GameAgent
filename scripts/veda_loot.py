#!/usr/bin/env python3
"""Prepare routine combat loot, or one reusable card/skip choice. Never sends input."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.loot import plan_loot
from veda.menu_requests import read_menu_draft, write_menu_request
from veda.menu_controls import CONTROL_PROFILE


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', required=True, type=Path)
    p.add_argument('--decision', type=Path, help='Reuse one choice while focus moves; changed rewards invalidate it.')
    p.add_argument('--session', type=Path)
    p.add_argument('--capture', type=Path)
    p.add_argument('--reviewer')
    p.add_argument('--evidence-note')
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args(argv)
    try:
        result = plan_loot(read_menu_draft(args.snapshot), read_menu_draft(args.decision) if args.decision else None)
        binding = any((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.execute, args.session))
        if binding and not all((args.capture, args.reviewer, args.evidence_note, args.reviewed, args.output, args.session)):
            raise ValueError('binding requires session, capture, reviewer, evidence-note, reviewed and output')
        if binding and result['status'] == 'planned':
            result = write_menu_request(result['draft'], capture=args.capture, session=args.session,
                reviewer=args.reviewer, evidence_note=args.evidence_note, reviewed=args.reviewed,
                control_profile=CONTROL_PROFILE, output=args.output, execute=args.execute)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
