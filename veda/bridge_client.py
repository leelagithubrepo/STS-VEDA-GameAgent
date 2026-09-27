"""Persistent, deadline-bounded JSONL transport; never reconnects to replay input.

This module opens only the explicitly supplied local Unix socket. Importing it
does not load the controller SDK or establish any connection. A successful
transport response is not evidence that a game action resolved on screen.
"""
from __future__ import annotations

import json
import math
import socket
import threading
import time
from typing import Any
from uuid import UUID, uuid4


class BridgeClient:
    """One serialized persistent connection, with ambiguous delivery latched.

    ``not_sent`` means this call did not submit a command. ``unknown_outcome``
    means submission may have occurred: observe/reconcile before creating a
    new client. The client never retries a send, including on a fresh socket.
    Server ``ok`` acknowledges transport execution, not game-state changes.
    """

    def __init__(self, socket_path: str, *, connect_timeout: float = 1.0,
                 read_timeout: float = 5.0, write_timeout: float = 1.0,
                 max_response_bytes: int = 65536, max_request_bytes: int = 16384) -> None:
        for value in (connect_timeout, read_timeout, write_timeout):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("bridge deadlines must be finite and positive")
        for value in (max_response_bytes, max_request_bytes):
            if type(value) is not int or value < 64:
                raise ValueError("bridge frame limits must be integers of at least 64 bytes")
        self.socket_path = str(socket_path)
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._uncertain = False

    def __enter__(self) -> BridgeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _disconnect(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def close(self) -> None:
        # shutdown interrupts a pending read without waiting for the call lock.
        self._closed = True
        current = self._socket
        if current is not None:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._disconnect()

    @staticmethod
    def _failure(request_id: str, status: str, error: str) -> dict[str, Any]:
        return {"ok": False, "request_id": request_id, "status": status, "error": error}

    def call(self, command: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid4())
        try:
            if not isinstance(command, dict):
                raise ValueError("not an object")
            payload = dict(command)
            if "request_id" in payload:
                supplied = payload["request_id"]
                if not isinstance(supplied, str) or str(UUID(supplied)) != supplied:
                    raise ValueError("request ID must be a canonical UUID")
                request_id = supplied
            payload["request_id"] = request_id
            encoded = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
            if len(encoded) > self.max_request_bytes:
                return self._failure(request_id, "not_sent", "request_too_large")
        except (ValueError, TypeError, OverflowError):
            return self._failure(request_id, "not_sent", "invalid_command")

        if not self._lock.acquire(timeout=self.connect_timeout + self.write_timeout + self.read_timeout):
            return self._failure(request_id, "not_sent", "client_busy")
        may_have_sent = False
        try:
            if self._closed:
                return self._failure(request_id, "not_sent", "client_closed")
            if self._uncertain:
                return self._failure(request_id, "not_sent", "reconciliation_required")
            if self._socket is None:
                candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    candidate.settimeout(self.connect_timeout)
                    candidate.connect(self.socket_path)
                except OSError:
                    candidate.close()
                    return self._failure(request_id, "not_sent", "connect_failed")
                self._socket = candidate
            current = self._socket
            if self._closed:
                self._disconnect()
                return self._failure(request_id, "not_sent", "client_closed")
            current.settimeout(self.write_timeout)
            # sendall may deliver a partial or complete request before raising.
            may_have_sent = True
            current.sendall(encoded)
            deadline = time.monotonic() + self.read_timeout
            data = bytearray()
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                current.settimeout(remaining)
                chunk = current.recv(min(4096, self.max_response_bytes + 1 - len(data)))
                if not chunk:
                    raise EOFError
                data.extend(chunk)
                if len(data) > self.max_response_bytes:
                    raise ValueError("oversized response")
            # Unsolicited/trailing data cannot be assigned safely to a future call.
            if data.index(b"\n") != len(data) - 1:
                raise ValueError("extra response data")
            response = json.loads(data)
            if not isinstance(response, dict) or response.get("request_id") != request_id:
                raise ValueError("response correlation failed")
            if type(response.get("ok")) is not bool:
                raise ValueError("missing outcome")
            status = response.get("status", "ok" if response["ok"] else "error")
            if status not in {"ok", "not_sent", "unknown_outcome", "error"}:
                raise ValueError("invalid outcome")
            if response["ok"] != (status == "ok"):
                raise ValueError("inconsistent outcome")
            response["status"] = status
            if status == "unknown_outcome":
                self._uncertain = True
                self._disconnect()
            return response
        except (OSError, ValueError, EOFError, UnicodeError):
            self._disconnect()
            if may_have_sent:
                self._uncertain = True
            return self._failure(request_id, "unknown_outcome" if may_have_sent else "not_sent",
                                 "response_unconfirmed" if may_have_sent else "transport_unavailable")
        finally:
            self._lock.release()


PersistentBridgeClient = BridgeClient
