#!/usr/bin/env python3
"""Check a saved, source-bound inspection journal without game input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.evidence_advisory import check_evidence, load_evidence_bundle


def document(path):
    with path.open('rb') as stream:
        data = stream.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("input JSON exceeds the 4 MB bound")
    return json.loads(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="JSON with journal and source_files; paths resolve beside this file")
    parser.add_argument("--plan", type=Path, help="optional next-action plan JSON")
    parser.add_argument("--replay-as-of", help="explicit historical clock; otherwise require current evidence")
    args = parser.parse_args(argv)
    try:
        bundle = load_evidence_bundle(args.evidence)
        result = check_evidence(bundle["journal"], source_files=bundle['source_files'],
                                plan=document(args.plan) if args.plan else None,
                                mode="replay" if args.replay_as_of else "review", as_of=args.replay_as_of)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["advisory_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
