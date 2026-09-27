#!/usr/bin/env python3
"""Run the persistent Codex-reviewed input adapter; startup sends no input."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.bridge_client import BridgeClient
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import MAX_BYTES, ReviewedPlaySession
from veda.telemetry_database import TelemetryDatabase


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", type=Path, default=Path("artifacts/veda-memory.sqlite3"))
    parser.add_argument("--mode", choices=("shadow", "codex"), default="shadow")
    parser.add_argument("--socket", default="/tmp/veda-ps5-bridge.sock")
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error("an existing run database is required")
    try:
        telemetry = PlayTelemetry(TelemetryDatabase(args.database.resolve()))
        with ReviewedPlaySession(args.directory, run_id=args.run_id, telemetry=telemetry,
                mode=args.mode, controller_factory=lambda: BridgeClient(args.socket)) as session:
            print(json.dumps({"status": "ready_unarmed", "summary": session.summary()}), flush=True)
            while not session.closed:
                line = sys.stdin.buffer.readline(MAX_BYTES + 1)
                if not line:
                    break
                try:
                    if len(line) > MAX_BYTES:
                        raise ValueError("request exceeds byte limit")
                    value = json.loads(line)
                    # File requests keep large reviewed packets out of terminal
                    # prompts. This reads JSON only; it cannot execute a script.
                    if isinstance(value, dict) and set(value) == {"request_file"}:
                        with Path(value["request_file"]).open("rb") as stream:
                            raw = stream.read(MAX_BYTES + 1)
                        if len(raw) > MAX_BYTES:
                            raise ValueError("request file exceeds byte limit")
                        value = json.loads(raw)
                    result = session.handle(value)
                except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                    session.disarm()
                    result = {"status": "stopped_for_review", "error": str(error), "armed": False,
                              "cleanup": session.cleanup}
                print(json.dumps(result, allow_nan=False), flush=True)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
