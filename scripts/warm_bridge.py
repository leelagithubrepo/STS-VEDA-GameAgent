#!/usr/bin/env python3
"""Keep one PS5 Remote Play session warm for VEDA's autonomous turn loop.

Run through ``scripts/warm_bridge`` so the VEDA-specific bridge configuration
is used.  Commands and responses are newline-delimited JSON, which keeps the
controller process alive between decisions instead of paying the Remote Play
handshake cost on every button press.

Input examples::

    {"action": "tap", "buttons": ["right", "cross"], "delay": 0.15}
    {"action": "status"}
    {"action": "close"}

Card and End Turn sequencing is deliberately kept in
``veda.controller_state_machine``. Callers re-observe after each reviewed
operation; the adapter can group up to four known hand-direction taps before
verifying final focus. The bridge remains a transport and never guesses
whether a card resolved.

This program deliberately accepts only a small allowlist of controller
operations.  It never prints the PSN user, host, pairing data, or API token.
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import inspect
import json
import logging
import math
from pathlib import Path
import socket
import stat
import sys
import time
from typing import Any
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.bridge_channel import DEFAULT_BRIDGE_SOCKET

if __package__:
    from .bridge_health import BridgeHealth, BridgeHealthError
else:
    from bridge_health import BridgeHealth, BridgeHealthError


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


async def _emit_command_response(payload: dict[str, Any], writer: Any | None) -> None:
    encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    if writer is None:
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    else:
        writer.write(encoded)
        await asyncio.wait_for(writer.drain(), 1.0)


def _parse_line(line: str | bytes) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("command must be a JSON object")
    return payload


class _CommandDispatcher:
    """Session-local exactly-once admission, not a game delivery guarantee.

    Retain all admitted IDs for this session. A full ledger rejects new work
    instead of evicting old IDs that could later replay controller input.
    """

    def __init__(self, service: Any, health: BridgeHealth, *, command_timeout: float = 3.0,
                 max_requests: int = 4096) -> None:
        if not math.isfinite(command_timeout) or command_timeout <= 0:
            raise ValueError("command timeout must be finite and positive")
        if type(max_requests) is not int or max_requests <= 0:
            raise ValueError("request capacity must be positive")
        self.service, self.health = service, health
        self.command_timeout = command_timeout
        self.max_requests = max_requests
        self.stopped = asyncio.Event()
        self.faulted = False
        self._lock = asyncio.Lock()
        self._requests: dict[str, tuple[str, asyncio.Future]] = {}

    async def dispatch(self, command: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        request_id = command.get("request_id", str(uuid4()))

        def failure(status: str, error: str) -> dict[str, Any]:
            return {"ok": False, "request_id": request_id, "status": status, "error": error}

        try:
            if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                raise ValueError("invalid ID")
            fingerprint = json.dumps(command, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError):
            # Do not reflect arbitrary user data in the correlation field.
            request_id = None
            return failure("not_sent", "invalid_request_id_or_command"), False
        previous = self._requests.get(request_id)
        if previous is not None:
            if previous[0] != fingerprint:
                return failure("error", "request_id_conflict"), False
            try:
                response, closing = await asyncio.wait_for(asyncio.shield(previous[1]), self.command_timeout)
                return dict(response, duplicate=True), closing
            except asyncio.TimeoutError:
                return failure("unknown_outcome", "request_still_in_flight"), False
        if self.stopped.is_set() or self.health.failed.is_set():
            return failure("not_sent", "session_stopped"), True
        if len(self._requests) >= self.max_requests:
            return failure("not_sent", "request_capacity_reached"), False
        future = asyncio.get_running_loop().create_future()
        self._requests[request_id] = (fingerprint, future)
        began = time.monotonic()
        acquired = False
        task = None
        response = failure("not_sent", "command_not_started")
        closing = False
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), self.command_timeout)
                acquired = True
            except asyncio.TimeoutError:
                response = failure("not_sent", "command_queue_timeout")
                return response, False
            if self.stopped.is_set() or self.health.failed.is_set():
                response = failure("not_sent", "session_stopped")
                return response, True
            remaining = self.command_timeout - (time.monotonic() - began)
            if remaining <= 0:
                response = failure("not_sent", "command_queue_timeout")
                return response, False
            task = asyncio.create_task(_handle(self.service, self.health, command))
            done, _ = await asyncio.wait((task,), timeout=remaining)
            if task not in done:
                # Cancellation cannot prove that the controller never received
                # the command. Latch and shut down; never admit a later input.
                task.cancel()
                task.add_done_callback(_consume_task_result)
                self.faulted = True
                self.stopped.set()
                response = failure("unknown_outcome", "command_timeout")
                return response, True
            result, closing = task.result()
            response = dict(result, ok=True, status="ok", request_id=request_id,
                            latency_ms=round((time.monotonic() - began) * 1000))
            if closing:
                self.stopped.set()
            return response, closing
        except (ValueError, TypeError):
            response = failure("error", "invalid_command_or_argument")
            return response, False
        except BridgeHealthError:
            self.faulted = True
            self.stopped.set()
            response = failure("unknown_outcome", "controller_transport_failed")
            return response, True
        except asyncio.CancelledError:
            if task is not None:
                task.cancel()
                task.add_done_callback(_consume_task_result)
                response = failure("unknown_outcome", "command_interrupted")
                self.faulted = True
                self.stopped.set()
            raise
        except Exception:
            self.faulted = True
            self.stopped.set()
            response = failure("unknown_outcome", "command_processing_failed")
            return response, True
        finally:
            if acquired:
                self._lock.release()
            if not future.done():
                future.set_result((response, closing or self.stopped.is_set()))


def _consume_task_result(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _serve_client(reader: Any, writer: Any, dispatcher: _CommandDispatcher,
                        health: BridgeHealth, *, idle_timeout: float = 0.0,
                        request_timeout: float = 2.0, max_request_bytes: int = 16384) -> None:
    try:
        while not dispatcher.stopped.is_set():
            try:
                line = await _read_command(reader, health, idle_timeout,
                                           request_timeout=request_timeout,
                                           max_request_bytes=max_request_bytes)
                if line is None:
                    dispatcher.stopped.set()
                if not line:
                    break
                response, closing = await dispatcher.dispatch(_parse_line(line))
            except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
                # A malformed or partial frame cannot be correlated safely.
                await _emit_command_response({"ok": False, "status": "not_sent", "request_id": None,
                                              "error": "invalid_or_incomplete_frame"}, writer)
                break
            await _emit_command_response(response, writer)
            if closing:
                break
    except (OSError, asyncio.TimeoutError, BridgeHealthError):
        pass  # Client departure never causes a command replay.
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, asyncio.TimeoutError):
            pass


def _owned_readiness(health: BridgeHealth) -> dict[str, Any]:
    """Inspect the connected transport without another discovery/network poll.

    PS5Service.status() performs DDP discovery with a timeout as long as this
    wrapper's entire command deadline. Discovery is not the health of the owned
    authenticated session and must not shut that session down during arming.
    """
    health.check_ready()
    snapshot = health.status()
    if (snapshot.get("transport_ready") is not True or snapshot.get("refresh_running") is not True
            or snapshot.get("error_present") is not False or snapshot.get("error_code") is not None):
        raise BridgeHealthError("owned bridge transport or refresh is not ready")
    return {"readiness_source": "owned_live_transport", "on": None, "power_state_probed": False,
            "session_ready": True, "health": snapshot}


async def _handle(service: Any, health: BridgeHealth, command: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    action = command.get("action")
    if action == "tap":
        buttons = command.get("buttons")
        if not isinstance(buttons, list) or not buttons or not all(isinstance(button, str) for button in buttons):
            raise ValueError("tap needs a non-empty string array: buttons")
        delay = float(command.get("delay", 0.12))
        gap = float(command.get("gap", 0.10))
        if not all(math.isfinite(value) and value >= 0 for value in (delay, gap)):
            raise ValueError("tap timing must be finite and non-negative")
        result = await health.execute(
            lambda: service.tap(buttons, delay=delay, gap=gap), input_action="tap")
        return {"action": action, "buttons": result}, False
    if action == "stick":
        result = await health.execute(lambda: service.stick(
            str(command.get("stick", "left")), command.get("x", 0.0), command.get("y", 0.0)
        ), input_action="stick")
        return {"action": action, "stick": result}, False
    if action == "status":
        return {"action": action, **_owned_readiness(health)}, False
    if action == "close":
        return {"action": action}, True
    raise ValueError("action must be one of: tap, stick, status, close")


async def _run(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket) if args.socket else None
    if socket_path is not None:
        try:
            socket_path.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            _emit({"ok": False, "error": "socket_path_unavailable", **_channel_metadata(socket_path)})
            return 2
        else:
            # Never detach an existing socket, file, or symlink from its owner.
            # Inspect the previous process/cleanup before removing stale paths.
            _emit({"ok": False, "error": "socket_path_exists", **_channel_metadata(socket_path)})
            return 2
    # Lazy imports keep parsing and transport tests independent of the live SDK.
    from ps5rmtctl.config import get_default
    from ps5rmtctl.service import PS5Service
    from pyremoteplay.stream_packets import FeedbackHeader

    # The SDK's transport diagnostics can include host/account details. Our
    # JSON health channel reports fixed error codes and counters only.
    logging.getLogger("pyremoteplay").setLevel(logging.CRITICAL)
    logging.getLogger("ps5rmtctl").setLevel(logging.CRITICAL)
    try:
        host = get_default("host")
        user = get_default("user")
    except Exception:
        _emit({"ok": False, "error": "bridge configuration could not be read"})
        return 2
    if not host or not user:
        _emit({"ok": False, "error": "bridge is not configured; run scripts/bridge setup"})
        return 2

    try:
        service = PS5Service(host, user, idle_timeout=0)
    except Exception:
        _emit({"ok": False, "error": "bridge service could not be initialized"})
        return 2
    health = None
    stdin_transport = None
    socket_server = None
    listening_socket = None
    owned_socket_identity = None
    client_tasks: set[asyncio.Task] = set()
    exit_code = 0
    started = time.monotonic()
    try:
        await service.connect()
        health = BridgeHealth(service, FeedbackHeader.Type.STATE)
        await health.start()
        dispatcher = _CommandDispatcher(service, health, command_timeout=args.command_timeout,
                                        max_requests=args.max_requests)
        if socket_path:
            async def accept_client(reader, writer):
                if len(client_tasks) >= 8:
                    writer.close()
                    await writer.wait_closed()
                    return
                task = asyncio.current_task()
                client_tasks.add(task)
                try:
                    await _serve_client(reader, writer, dispatcher, health, idle_timeout=args.idle_timeout,
                                        request_timeout=args.request_timeout,
                                        max_request_bytes=args.max_request_bytes)
                finally:
                    client_tasks.discard(task)

            # Passing path= to asyncio may unlink an existing Unix socket.
            # Bind ourselves so a concurrent owner wins atomically and is never
            # replaced, then give asyncio only the socket we actually own.
            listening_socket, owned_socket_identity = _bind_command_socket(socket_path)
            socket_server = await asyncio.start_unix_server(
                accept_client, sock=listening_socket, limit=args.max_request_bytes,
                **_socket_cleanup_options(asyncio.get_running_loop()))
            listening_socket = None  # Server owns descriptor after success.
            _require_owned_socket(socket_path, owned_socket_identity)
            socket_path.chmod(0o600)
            _require_owned_socket(socket_path, owned_socket_identity)
        else:
            reader = asyncio.StreamReader(limit=args.max_request_bytes)
            stdin_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        _emit({"ok": True, "event": "ready", "connect_ms": round((time.monotonic() - started) * 1000),
               **_owned_readiness(health), **_channel_metadata(socket_path)})
        if socket_path:
            stopped_task = asyncio.create_task(dispatcher.stopped.wait())
            fault_task = asyncio.create_task(health.failed.wait())
            try:
                done, _ = await asyncio.wait((stopped_task, fault_task), return_when=asyncio.FIRST_COMPLETED)
                if fault_task in done or dispatcher.faulted:
                    raise BridgeHealthError("controller transport failed")
            finally:
                for task in (stopped_task, fault_task):
                    task.cancel()
                await asyncio.gather(stopped_task, fault_task, return_exceptions=True)
        else:
            while not dispatcher.stopped.is_set():
                try:
                    line = await _read_command(reader, health, args.idle_timeout,
                                               request_timeout=args.request_timeout,
                                               max_request_bytes=args.max_request_bytes)
                    if not line:
                        if line is None:
                            _emit({"ok": True, "event": "idle_timeout"})
                        break
                    response, should_close = await dispatcher.dispatch(_parse_line(line))
                except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
                    await _emit_command_response({"ok": False, "status": "not_sent", "request_id": None,
                                                  "error": "invalid_or_incomplete_frame"}, None)
                    break
                await _emit_command_response(response, None)
                if should_close:
                    if dispatcher.faulted:
                        exit_code = 3
                    break
    except CommandSocketError as error:
        _emit({"ok": False, "error": error.code, **_channel_metadata(socket_path)})
        exit_code = 2
    except BridgeHealthError:
        _emit({"ok": False, "event": "controller_fault", "error": "controller transport failed; closing session",
               "health": health.status() if health else None})
        exit_code = 3
    except Exception:
        _emit({"ok": False, "error": "bridge connection or command processing failed; closing session"})
        exit_code = 2
    finally:
        if stdin_transport is not None:
            stdin_transport.close()
        if socket_server is not None:
            socket_server.close()
            await socket_server.wait_closed()
        if listening_socket is not None:
            listening_socket.close()
        if client_tasks:
            # Let the closing command acknowledgement drain before releasing
            # the transport; bound idle client shutdown as well.
            _, pending = await asyncio.wait(tuple(client_tasks), timeout=1.0)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if not _unlink_owned_socket(socket_path, owned_socket_identity):
            exit_code = 3
        if not await _close_bridge(service, health):
            exit_code = 3
    return exit_code


class CommandSocketError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _channel_metadata(socket_path):
    return {"command_channel": "unix_socket" if socket_path is not None else "stdio",
            "socket_path": str(socket_path) if socket_path is not None else None}


def _socket_cleanup_options(loop):
    # Python 3.13+ also snapshots and unlinks a path when given sock=. Keep
    # cleanup exclusively under our bind-time ownership check. Older asyncio
    # does not accept this option and does not perform that automatic cleanup.
    if "cleanup_socket" in inspect.signature(loop.create_unix_server).parameters:
        return {"cleanup_socket": False}
    return {}


def _require_owned_socket(path, identity):
    try:
        current = path.lstat()
    except OSError:
        raise CommandSocketError("socket_path_changed") from None
    if not stat.S_ISSOCK(current.st_mode) or (current.st_dev, current.st_ino) != identity:
        raise CommandSocketError("socket_path_changed")


def _bind_command_socket(path):
    """Atomically bind a new endpoint without unlinking another owner's path."""
    path = Path(path)
    listener = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.setblocking(False)
        listener.bind(str(path))
        identity = path.lstat()
        return listener, (identity.st_dev, identity.st_ino)
    except OSError as error:
        if listener is not None:
            listener.close()
        raise CommandSocketError("socket_path_exists" if error.errno in {errno.EADDRINUSE, errno.EEXIST}
                                 else "socket_bind_failed") from None


def _unlink_owned_socket(path, identity):
    if path is None or identity is None:
        return True
    try:
        current = path.lstat()
        if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == identity:
            path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


async def _close_bridge(service: Any, health: BridgeHealth | None) -> bool:
    """Contain SDK cleanup failures; never expose its raw exception/traceback."""
    try:
        if health is not None:
            await health.close()
        else:
            try:
                await service.release_all()
            finally:
                await service.close()
    except Exception:
        _emit({"ok": False, "event": "controller_fault",
               "error": "controller cleanup failed; session release was not confirmed"})
        return False
    return True


async def _read_command(reader: Any, health: BridgeHealth, idle_timeout: float = 0.0, *,
                        request_timeout: float = 2.0, max_request_bytes: int = 16384):
    """Interrupt an idle stdin wait immediately on transport failure.

    StreamReader is cancellable; unlike a blocking executor readline, it does
    not keep a failed controller process alive waiting for another command.
    """
    async def read_frame():
        first = await reader.read(1)
        if not first:
            return b""
        if first == b"\n":
            raise ValueError("empty frame")
        rest = await asyncio.wait_for(reader.readline(), request_timeout)
        line = first + rest
        if len(line) > max_request_bytes or not line.endswith(b"\n"):
            raise ValueError("oversized or incomplete frame")
        return line

    read_task = asyncio.create_task(read_frame())
    fault_task = asyncio.create_task(health.failed.wait())
    try:
        done, _ = await asyncio.wait(
            (read_task, fault_task), timeout=idle_timeout if idle_timeout > 0 else None,
            return_when=asyncio.FIRST_COMPLETED)
        if fault_task in done:
            raise BridgeHealthError("controller transport failed")
        if read_task not in done:
            return None
        return read_task.result()
    finally:
        for task in (read_task, fault_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(read_task, fault_task, return_exceptions=True)


async def _next_socket_client(clients: asyncio.Queue, health: BridgeHealth):
    client_task = asyncio.create_task(clients.get())
    fault_task = asyncio.create_task(health.failed.wait())
    try:
        done, _ = await asyncio.wait((client_task, fault_task), return_when=asyncio.FIRST_COMPLETED)
        if fault_task in done:
            raise BridgeHealthError("controller transport failed")
        return client_task.result()
    finally:
        for task in (client_task, fault_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(client_task, fault_task, return_exceptions=True)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="VEDA persistent PS5 controller session (local Unix-socket JSONL by default).")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=0.0,
        help="Close after this many seconds without a JSON command; 0 disables this timeout.",
    )
    channel = parser.add_mutually_exclusive_group()
    channel.add_argument(
        "--socket",
        default=DEFAULT_BRIDGE_SOCKET,
        help="Unix socket for JSONL commands (default: %(default)s); existing paths are never replaced.",
    )
    channel.add_argument("--stdio", action="store_true", help="Use inherited stdin/stdout JSONL instead of a Unix socket.")
    parser.add_argument("--command-timeout", type=float, default=3.0,
                        help="Maximum seconds queued or executing one command; timeout stops the session.")
    parser.add_argument("--request-timeout", type=float, default=2.0,
                        help="Maximum seconds to finish a JSONL frame after its first byte.")
    parser.add_argument("--max-request-bytes", type=int, default=16384)
    parser.add_argument("--max-requests", type=int, default=4096,
                        help="Per-session deduplication capacity; additional new IDs are rejected.")
    args = parser.parse_args(argv)
    if args.stdio:
        args.socket = None
    elif not args.socket.strip():
        parser.error("--socket must be a nonempty path")
    if not math.isfinite(args.idle_timeout) or args.idle_timeout < 0:
        parser.error("--idle-timeout must be finite and non-negative")
    if any(not math.isfinite(value) or value <= 0 for value in (args.command_timeout, args.request_timeout)):
        parser.error("command and request timeouts must be finite and positive")
    if not 64 <= args.max_request_bytes <= 65536 or not 1 <= args.max_requests <= 65536:
        parser.error("request limits must be bounded: bytes 64..65536, count 1..65536")
    return args


def main(argv=None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
