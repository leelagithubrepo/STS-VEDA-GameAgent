#!/usr/bin/env python3
"""Validate a helper name without starting it or touching the controller."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.helper_registry import helper_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("helper")
    parser.add_argument("--session", type=Path,
                        help="Optional reviewed-play session. Reuses this session's helper registry receipt.")
    args = parser.parse_args()
    try:
        path = helper_path(args.helper)
        result = {"supported": True, "helper": path.name, "path": str(path), "cached": False}
        if args.session is not None:
            session = args.session.expanduser().resolve()
            # The registry is a local capability receipt only.  It neither
            # identifies a run nor grants controller authority.
            if not session.is_dir():
                raise ValueError("helper cache session directory does not exist")
            receipt_path = session / ".helper-registry.json"
            receipt = {}
            try:
                raw = json.loads(receipt_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("schema") == "veda.helper-registry.v1" and isinstance(raw.get("helpers"), dict):
                    receipt = raw["helpers"]
            except (OSError, ValueError, TypeError):
                receipt = {}
            cached = receipt.get(path.name)
            if isinstance(cached, dict) and cached.get("path") == str(path):
                result["cached"] = True
            else:
                receipt[path.name] = {"path": str(path)}
                temporary = receipt_path.with_name(".helper-registry-" + uuid4().hex)
                try:
                    temporary.write_text(json.dumps({"schema": "veda.helper-registry.v1", "helpers": receipt},
                                                    sort_keys=True) + "\n", encoding="utf-8")
                    temporary.replace(receipt_path)
                finally:
                    temporary.unlink(missing_ok=True)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"supported": False, "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
