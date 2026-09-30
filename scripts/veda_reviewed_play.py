#!/usr/bin/env python3
"""Run the persistent Codex-reviewed input adapter; startup sends no input."""
import argparse
from contextlib import contextmanager, nullcontext
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


def terminal_response(result, *, full_timing=False):
    """Keep action/recovery fields intact; do not repeat timing history per tap."""
    if full_timing:
        return result
    result = deepcopy(result)
    for container in (result, result.get('summary', {})):
        timing = container.get('timing')
        if not isinstance(timing, dict) or timing.get('schema') != 'veda.play-timing-summary.v1':
            continue
        compact = {k: timing[k] for k in ('phase', 'measurement_complete', 'verified_input_count', 'new_alerts') if k in timing}
        for name in ('move', 'floor'):
            scope = timing.get(name)
            compact[name] = None if scope is None else {k: scope[k] for k in
                ('id', 'kind', 'active_seconds', 'target_seconds', 'over_target', 'measurement_basis',
                 'watchdog_due', 'watchdog_remaining_seconds', 'watchdog_class', 'watchdog_fallback') if k in scope}
        compact.update(compact=True, detail='operation summary or --verbose-timing; full history remains in timing.json')
        container['timing'] = compact
    return result


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


def timed_requests(stream, session, server=None):
    """Read bounded JSONL while reporting deadlines even during model silence.

    Use raw reads so an extra buffered line never waits on an empty OS pipe.
    Polling emits diagnostics only; it cannot dispatch, retry or close input.
    """
    learning = getattr(session, 'decision_policy', 'strict') == 'learning'
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
                if not learning:
                    raise ValueError('request exceeds byte limit')
                while line and not line.endswith(b'\n' if isinstance(line, bytes) else '\n'):
                    line = reader.readline(MAX_BYTES + 1)
                yield ValueError('request exceeds byte limit; oversized line discarded')
                continue
            yield line
        return
    pending = b''
    discarding = False
    while not session.closed:
        if b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            if len(line) > MAX_BYTES:
                if not learning:
                    raise ValueError('request exceeds byte limit')
                yield ValueError('request exceeds byte limit; oversized line discarded')
                continue
            yield line
            continue
        if len(pending) > MAX_BYTES:
            if not learning:
                raise ValueError('request exceeds byte limit')
            pending = b''; discarding = True
            yield ValueError('request exceeds byte limit; discarding through the next newline')
        sources = ([] if descriptor is None else [descriptor]) + ([server.listener] if server else [])
        ready, _, _ = select.select(sources, [], [], 5)
        if not ready:
            timing = session.timing_summary(poll=True)
            if timing.get('new_alerts'):
                alerts = timing['new_alerts']
                watchdog = next((item for item in alerts if item.get('code') == 'watchdog_due'), None)
                print(json.dumps({'status': 'watchdog_due' if watchdog else 'timing_overrun',
                    'alerts': alerts, 'watchdog': watchdog,
                    'phase': timing.get('phase'), 'controller_input_sent': False,
                    'required': ('Use the watchdog fallback once against the current inspected state, then verify; '
                                 'preserve any pending input and never replay uncertain delivery.'
                                 if watchdog else
                                 'Resolve the named delay; preserve any pending input and ordinary checks.')}), flush=True)
            continue
        if server and server.listener in ready:
            submission = server.accept()
            if submission is not None:
                yield submission
            continue
        chunk = os.read(descriptor, min(65536, MAX_BYTES + 1 - len(pending)))
        if not chunk:
            if pending.strip():
                raise ValueError('incomplete JSONL request at EOF; newline required')
            if server:
                descriptor = None
                continue
            return
        if discarding:
            if b'\n' not in chunk:
                continue
            _, chunk = chunk.split(b'\n', 1)
            discarding = False
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
    parser.add_argument('--decision-policy', choices=('strict', 'learning'),
                        help='Defaults to learning in codex mode and strict in shadow mode. Learning continues after '
                             'recoverable review errors; control and pending-input checks remain enforced.')
    parser.add_argument('--verbose-timing', action='store_true', help='Include full timing history in every response.')
    parser.add_argument('--request-server', action='store_true', help='Enable response-driven veda_submit.py on a private local socket.')
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error("an existing run database is required")
    try:
        telemetry = PlayTelemetry(TelemetryDatabase(args.database.resolve()))
        with jsonl_terminal(sys.stdin), ReviewedPlaySession(args.directory, run_id=args.run_id, telemetry=telemetry,
                mode=args.mode, controller_factory=lambda: BridgeClient(args.socket),
                decision_policy=args.decision_policy or ('learning' if args.mode == 'codex' else 'strict')) as session:
            from veda.adapter_channel import RequestServer
            with RequestServer(args.directory) if args.request_server else nullcontext() as server:
                return serve(session, server, args)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "armed": False,
                          "required": "Inspect pending state and owned bridge cleanup before resuming."}), flush=True)
        return 130
    return 0


def serve(session, server, args):
    from veda.adapter_channel import Submission
    print(json.dumps(terminal_response({"status": "ready_unarmed", "summary": session.summary(),
                "next_operation": "bridge_preflight" if args.mode == "codex" else "summary",
                "request_socket": str(server.path) if server else None,
                "request_format": "newline-terminated JSONL; request_file preferred"}, full_timing=args.verbose_timing)), flush=True)
    for incoming in timed_requests(sys.stdin, session, server):
        submission = incoming if isinstance(incoming, Submission) else None
        line = submission.raw if submission else incoming
        full_timing = args.verbose_timing
        try:
            if isinstance(line, Exception):
                raise line
            if len(line) > MAX_BYTES:
                raise ValueError("request exceeds byte limit")
            from veda.request_envelope import load_request
            value = load_request(line)
            full_timing = full_timing or isinstance(value, dict) and value.get('operation') in {'summary', 'timing'}
            result = session.handle(value)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
            result = session.recoverable_error(error)
        response = terminal_response(result, full_timing=full_timing)
        if submission:
            submission.reply(response)
        else:
            print(json.dumps(response, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
