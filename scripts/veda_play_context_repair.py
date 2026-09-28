#!/usr/bin/env python3
"""Validate or apply audited first-floor metadata repair; never play.

Validate the completed-combat map variant before obtaining a new image:
  python3 scripts/veda_play_context_repair.py --draft REPAIR.json --database MEMORY.sqlite3 --session RUN/state.json
After inspecting the exact fresh capture, repeat with:
  --apply --backup NEW-BACKUP.sqlite3 --capture CAPTURE.png --reviewer REVIEWER --evidence-note NOTE --reviewed

The closed_first_floor variant explicitly names the closed combat, orphaned
turn, and expected closure timestamp. It cannot choose a map node or reconstruct
missing gameplay turns. Apply creates the backup and requires an idle session.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.menu_requests import read_menu_draft
from veda.play_context_repair import apply_context_repair, validate_context_repair


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path, help='New backup file; created before any repair writes.')
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--reviewer')
    parser.add_argument('--evidence-note')
    parser.add_argument('--reviewed', action='store_true')
    args = parser.parse_args(argv)
    try:
        value = read_menu_draft(args.draft)
        if args.apply:
            if not all((args.backup, args.capture, args.reviewer, args.evidence_note, args.reviewed)):
                raise ValueError('apply requires new backup path and explicit exact fresh capture review')
            result = apply_context_repair(value, database=args.database, session=args.session, backup=args.backup,
                capture=args.capture, reviewer=args.reviewer, evidence_note=args.evidence_note, reviewed=args.reviewed)
        else:
            if any((args.backup, args.capture, args.reviewer, args.evidence_note, args.reviewed)):
                raise ValueError('read-only validation takes no capture, review or backup arguments')
            result = validate_context_repair(value, database=args.database, session=args.session)
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False,
                          'runtime_authorized': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
