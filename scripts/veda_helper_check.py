#!/usr/bin/env python3
"""Validate a helper name without starting it or touching the controller."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.helper_registry import helper_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("helper")
    args = parser.parse_args()
    try:
        path = helper_path(args.helper)
        print(json.dumps({"supported": True, "helper": path.name, "path": str(path)}))
        return 0
    except ValueError as error:
        print(json.dumps({"supported": False, "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
