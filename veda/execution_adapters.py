"""Bounded adapters for the same execution loop in replay and armed operation."""
from __future__ import annotations

import hashlib
import copy
import json
import math
import os
from pathlib import Path
import select
import subprocess
import threading
import time

from .execution import Reading, RuntimeStop
from .runtime_frames import RuntimeFrame


class ReplaySource:
    def __init__(self, manifest: dict, base: Path):
        self.manifest, self.base = manifest, base
        self.rows = iter(manifest["frames"])
        self.current = None

    def next_frame(self):
        self.current = next(self.rows)
        frame = RuntimeFrame.from_path((self.base / self.current["image"]).resolve(),
            frame_id=self.current["frame_id"], observed_at=self.current["observed_at"],
            source=self.manifest.get("evidence_kind", "recorded_state_replay"))
        if frame.sha256 != self.current["sha256"]:
            raise RuntimeStop("replay image differs from its recorded digest")
        return frame

    def close(self):
        pass


class RecordedInterpreter:
    """Replays recorded recognition results; it does NOT validate vision accuracy."""
    def __init__(self, source):
        self.source = source

    def read(self, frame, **kwargs):
        data = self.source.current["reading"]
        return Reading.from_dict({**data, "frame_id": frame.frame_id, "image_sha256": frame.sha256})

    def close(self):
        pass


class ReplayController:
    offline = True

    def __init__(self, expected_commands=()):
        self.expected = iter(expected_commands)
        self.commands = []

    def call(self, command):
        expected = next(self.expected, None)
        self.commands.append(command)
        if expected is None or command.get("buttons") != expected:
            return {"status": "not_sent", "request_id": command["request_id"],
                    "error": "replay command differs from recorded expectation"}
        return {"status": "ok", "ok": True, "request_id": command["request_id"]}

    def close(self):
        pass


class AnalysisWorker:
    """One persistent local recognition worker using a bounded JSONL protocol.

    An independently validated recognizer must produce the full Reading
    contract (including UI evidence) for each image digest. No worker is
    bundled or implicitly selected, and calibration is required before live
    mode can launch it. This transport does not make any model request.
    """
    def __init__(self, command: list[str], *, timeout_seconds=5, max_response_bytes=2_000_000,
                 max_request_bytes=65536, recognizer_identity: dict | None = None):
        if (not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command)
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0 or type(max_response_bytes) is not int or max_response_bytes <= 0
                or type(max_request_bytes) is not int or max_request_bytes <= 0):
            raise ValueError("bounded worker command and limits required")
        self.command, self.timeout = list(command), timeout_seconds
        self.limit, self.process = max_response_bytes, None
        self.request_limit = max_request_bytes
        self.recognizer_identity = copy.deepcopy(recognizer_identity)
        self.recognizer_fingerprint = None
        if recognizer_identity is not None:
            from .runtime_authorization import recognizer_fingerprint
            self.recognizer_fingerprint = recognizer_fingerprint(self.recognizer_identity)
        self._closed = False
        self._lock = threading.Lock()

    def _start(self):
        if self._closed:
            raise RuntimeStop("recognition worker is closed; explicit restart required")
        if self.process is None:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                start_new_session=True)
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        elif self.process.poll() is not None:
            raise RuntimeStop("recognition worker exited; no automatic restart")

    @staticmethod
    def _wait(fd, deadline, *, writing):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("recognition worker write deadline" if writing else "recognition worker response deadline")
            try:
                ready = select.select([] if writing else [fd], [fd] if writing else [], [], remaining)
            except InterruptedError:
                continue
            if ready[1 if writing else 0]:
                return

    @staticmethod
    def _check_unsolicited(reader):
        # A read on a nonblocking pipe cannot wait for a partial/trailing line.
        # Also perform this before the next request to catch late extra output.
        try:
            trailing = os.read(reader, 1)
        except BlockingIOError:
            return
        if trailing:
            raise RuntimeStop("recognition worker produced unsolicited trailing output")

    def read(self, frame, *, purpose, previous=None):
        deadline = time.monotonic() + self.timeout
        if not self._lock.acquire(timeout=self.timeout):
            raise RuntimeStop("recognition worker already has a request in flight")
        try:
            request = {"frame": frame.identity(), "purpose": purpose,
                       "previous_frame_id": previous.frame_id if previous else None}
            if self.recognizer_fingerprint is not None:
                request['recognizer_fingerprint'] = self.recognizer_fingerprint
            payload = (json.dumps(request, allow_nan=False) + "\n").encode()
            if len(payload) > self.request_limit:
                raise RuntimeStop("recognition worker request exceeds bound")
            self._start()
            writer, reader = self.process.stdin.fileno(), self.process.stdout.fileno()
            self._check_unsolicited(reader)
            sent, response = 0, bytearray()
            while sent < len(payload):
                self._wait(writer, deadline, writing=True)
                try:
                    written = os.write(writer, payload[sent:sent + 4096])
                except (BlockingIOError, InterruptedError):
                    continue
                if written <= 0:
                    raise RuntimeStop("recognition worker pipe made no write progress")
                sent += written
            while b"\n" not in response:
                self._wait(reader, deadline, writing=False)
                try:
                    chunk = os.read(reader, min(4096, self.limit + 1 - len(response)))
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    raise RuntimeStop("recognition worker closed before response")
                response.extend(chunk)
                if len(response) > self.limit:
                    raise RuntimeStop("recognition worker response exceeds bound")
            if response.index(b"\n") != len(response) - 1:
                raise RuntimeStop("recognition worker produced unsolicited trailing output")
            self._check_unsolicited(reader)
            data = json.loads(response)
            if not isinstance(data, dict):
                raise RuntimeStop("recognition worker response must be an object")
            fingerprint = data.pop('recognizer_fingerprint', None)
            if self.recognizer_fingerprint is not None and fingerprint != self.recognizer_fingerprint:
                raise RuntimeStop("recognition worker configuration fingerprint differs from authorization")
            result = Reading.from_dict(data)
            if result.frame_id != frame.frame_id or result.image_sha256 != frame.sha256:
                raise RuntimeStop("recognition worker response references a different frame")
            return result
        except BaseException as error:
            # A partial request/response cannot become another request's reply.
            # Close rather than automatically restarting or reusing the worker.
            try:
                self.close()
            except Exception as cleanup_error:
                # Preserve the protocol/deadline failure. A caller can retry
                # cleanup, but must never retry the unverified request.
                error.add_note(f"recognition worker cleanup failed: {cleanup_error}")
            raise
        finally:
            self._lock.release()

    def close(self):
        self._closed = True
        process = self.process
        if process:
            try:
                # Signal/reap the exact child owned by Popen, avoiding a
                # process-group signal racing a worker that already exited.
                # Repeated calls may retry cleanup after an earlier failure.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
            finally:
                process.stdin.close()
                process.stdout.close()


class WarmController:
    offline = False

    def __init__(self, client):
        self.client = client

    def call(self, command):
        return self.client.call(command)

    def close(self):
        try:
            result = self.client.call({"action": "close"})
            if result.get("status") != "ok":
                raise RuntimeStop("warm bridge cleanup could not be confirmed")
        finally:
            self.client.close()
