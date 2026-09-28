#!/usr/bin/env python3
"""Run the persistent Codex-reviewed input adapter; startup sends no input."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import select
import sys
import termios

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.bridge_client import BridgeClient
from veda.bridge_channel import DEFAULT_BRIDGE_SOCKET
from veda.play_telemetry import PlayTelemetry
from veda.reviewed_play import MAX_BYTES, ReviewedPlaySession
from veda.telemetry_database import TelemetryDatabase


@contextmanager
def jsonl_terminal(stream):
    """Remove the PTY's short canonical-line limit, restoring it on every exit.

    JSONL still requires a newline; this does not accept partial JSON or infer
    a request boundary from a timeout. Signals remain enabled for Ctrl-C.
    """
    if not stream.isatty():
        yield
        return
    descriptor = stream.fileno()
    original = termios.tcgetattr(descriptor)
    configured = deepcopy(original)
    configured[3] &= ~(termios.ICANON | termios.ECHO)
    configured[6][termios.VMIN] = 1
    configured[6][termios.VTIME] = 0
    termios.tcsetattr(descriptor, termios.TCSANOW, configured)
    try:
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSANOW, original)


def timed_requests(stream, session):
    """Read bounded JSONL while reporting deadlines even during model silence.

    Use raw reads so an extra buffered line never waits on an empty OS pipe.
    Polling emits diagnostics only; it cannot dispatch, retry or close input.
    """
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError):
        # In-memory replay streams have no OS descriptor or waiting interval.
        # Retain the bounded JSONL reader for these offline callers.
        reader = getattr(stream, 'buffer', stream)
        while not session.closed:
            line = reader.readline(MAX_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_BYTES:
                raise ValueError('request exceeds byte limit')
            yield line
        return
    pending = b''
    while not session.closed:
        if b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            if len(line) > MAX_BYTES:
                raise ValueError('request exceeds byte limit')
            yield line
            continue
        if len(pending) > MAX_BYTES:
            raise ValueError('request exceeds byte limit')
        ready, _, _ = select.select([descriptor], [], [], 5)
        if not ready:
            timing = session.timing_summary(poll=True)
            if timing.get('new_alerts'):
                print(json.dumps({'status': 'timing_overrun', 'alerts': timing['new_alerts'],
                    'phase': timing.get('phase'), 'controller_input_sent': False,
                    'required': 'Resolve the named delay; preserve any pending input and ordinary checks.'}), flush=True)
            continue
        chunk = os.read(descriptor, min(65536, MAX_BYTES + 1 - len(pending)))
        if not chunk:
            if pending.strip():
                raise ValueError('incomplete JSONL request at EOF; newline required')
            return
        pending += chunk


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        'Requests are newline-terminated JSONL; prefer a short request_file pointer. '
        'In codex mode, check operation bridge_preflight before capturing arm evidence. '
        'Startup and preflight grant no controller authority.')
    parser.add_argument("directory", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", type=Path, default=Path("artifacts/veda-memory.sqlite3"))
    parser.add_argument("--mode", choices=("shadow", "codex"), default="shadow")
    parser.add_argument("--socket", default=DEFAULT_BRIDGE_SOCKET)
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error("an existing run database is required")
    try:
        telemetry = PlayTelemetry(TelemetryDatabase(args.database.resolve()))
        with jsonl_terminal(sys.stdin), ReviewedPlaySession(args.directory, run_id=args.run_id, telemetry=telemetry,
                mode=args.mode, controller_factory=lambda: BridgeClient(args.socket)) as session:
            print(json.dumps({"status": "ready_unarmed", "summary": session.summary(),
                "next_operation": "bridge_preflight" if args.mode == "codex" else "summary",
                "request_format": "newline-terminated JSONL; request_file preferred"}), flush=True)
            for line in timed_requests(sys.stdin, session):
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
                              "cleanup": session.cleanup, "timing": session.timing_summary()}
                print(json.dumps(result, allow_nan=False), flush=True)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "armed": False,
                          "required": "Inspect pending state and owned bridge cleanup before resuming."}), flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
