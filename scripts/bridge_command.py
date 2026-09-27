#!/usr/bin/env python3
"""Send one JSONL command to a warm_bridge Unix-socket session."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.bridge_client import BridgeClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("socket")
    parser.add_argument("command", help="JSON command object")
    parser.add_argument("--connect-timeout", type=float, default=1.0)
    parser.add_argument("--read-timeout", type=float, default=5.0)
    parser.add_argument("--write-timeout", type=float, default=1.0)
    args = parser.parse_args()
    try:
        payload = json.loads(args.command)
        with BridgeClient(args.socket, connect_timeout=args.connect_timeout,
                          read_timeout=args.read_timeout, write_timeout=args.write_timeout) as client:
            response = client.call(payload)
    except (ValueError, TypeError):
        response = {"ok": False, "status": "not_sent", "error": "invalid_command_or_timeout"}
    print(json.dumps(response, separators=(",", ":")))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
