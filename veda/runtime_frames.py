"""Frame provenance and a reusable bounded capture source; no import side effects."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable
from uuid import uuid4

from .observation import capture_visible_ps5_feed


MAX_FRAME_BYTES = 32 * 1024 * 1024


class CaptureFailure(OSError):
    """An ordinary capture-provider failure, with no implied controller action."""


@dataclass(frozen=True)
class RuntimeFrame:
    frame_id: str
    image_path: Path
    sha256: str
    observed_at: str
    acquired_ns: int
    ephemeral: bool = False
    source: str = "saved_frame"

    @classmethod
    def from_path(cls, path: Path, *, frame_id: str | None = None,
                  observed_at: str | None = None, source: str = "saved_frame",
                  ephemeral: bool = False, clock=time.monotonic_ns):
        payload = path.read_bytes()
        if not payload:
            raise ValueError("empty frame")
        return cls(frame_id or uuid4().hex, path, hashlib.sha256(payload).hexdigest(),
                   observed_at or datetime.now(timezone.utc).isoformat(), clock(), ephemeral, source)

    def identity(self) -> dict:
        return {"frame_id": self.frame_id, "image_path": str(self.image_path),
                "sha256": self.sha256, "observed_at": self.observed_at,
                "acquired_ns": self.acquired_ns, "ephemeral": self.ephemeral,
                "source": self.source}


@dataclass(frozen=True)
class FrameSnapshot:
    """One bounded immutable pending-input image, independent of cache eviction."""
    frame: RuntimeFrame
    payload: bytes


def snapshot_frame(frame: RuntimeFrame, *, max_bytes=MAX_FRAME_BYTES) -> FrameSnapshot:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_FRAME_BYTES:
        raise ValueError("evidence byte budget must be positive and bounded")
    with frame.image_path.open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if not payload or len(payload) > max_bytes:
        raise ValueError("evidence frame is empty or exceeds the byte budget")
    if hashlib.sha256(payload).hexdigest() != frame.sha256:
        raise ValueError("frame content changed before archival")
    return FrameSnapshot(frame, payload)


def retain_snapshot(snapshot: FrameSnapshot, directory: Path) -> dict:
    """Publish hash-addressed evidence without replacing an existing file.

    The returned identity names the durable bytes and retains the original path.
    Reuse requires exact bytes; a conflicting or damaged archive is an error.
    Neither file storage nor its receipt grants observation/controller authority.
    """
    frame, payload = snapshot.frame, snapshot.payload
    if (type(payload) is not bytes or not payload or len(payload) > MAX_FRAME_BYTES
            or hashlib.sha256(payload).hexdigest() != frame.sha256):
        raise ValueError("invalid bounded evidence snapshot")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    suffix = frame.image_path.suffix
    if len(suffix) > 16:
        suffix = ".frame"
    target = directory / f"{frame.sha256}{suffix}"
    # Link a fully flushed staging file into place atomically, without overwrite.
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as stream:
        staging = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(staging, target)
        except FileExistsError:
            pass
        with target.open("rb") as stream:
            archived = stream.read(MAX_FRAME_BYTES + 1)
        if archived != payload:
            raise ValueError("existing retained evidence differs from its hash identity")
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        staging.unlink(missing_ok=True)
    return {**frame.identity(), "image_path": str(target),
            "original_image_path": str(frame.image_path), "ephemeral": False,
            "durable": True}


class BoundedCaptureSource:
    """Reuse one directory/source and retain only the configured recent frames.

    The default remains the existing bounded screenshot subprocess. A persistent
    window-capture provider can be injected; merely using this source is not a
    claim that screenshot encoding or subprocess costs have disappeared.
    """
    provenance = "live_capture"
    def __init__(self, *, capture: Callable = capture_visible_ps5_feed,
                 max_frames: int = 3, max_frame_bytes: int = 32 * 1024 * 1024,
                 clock=time.monotonic_ns):
        if max_frames < 2 or max_frame_bytes < 1:
            raise ValueError("frame storage must retain at least a before/after pair")
        self.capture, self.clock = capture, clock
        self.max_frames, self.max_frame_bytes = max_frames, max_frame_bytes
        self._directory = tempfile.TemporaryDirectory(prefix="veda-runtime-")
        self._frames = deque()

    def next_frame(self) -> RuntimeFrame:
        # Conservative age bound: capture/encoding/hashing time is part of the
        # evidence age, not a reason to restart its freshness clock afterward.
        requested_ns = self.clock()
        requested_at = datetime.now(timezone.utc).isoformat()
        try:
            path = Path(self.capture(Path(self._directory.name)))
        except (OSError, TimeoutError, RuntimeError) as error:
            raise CaptureFailure("capture provider failed: " + str(error)) from error
        if path.parent.resolve() != Path(self._directory.name).resolve():
            raise ValueError("capture must return a frame in its bounded source directory")
        if path.stat().st_size > self.max_frame_bytes:
            path.unlink()
            raise ValueError("frame exceeds configured byte budget")
        frame = RuntimeFrame.from_path(path, source="live_capture", ephemeral=True,
                                       observed_at=requested_at, clock=lambda: requested_ns)
        self._frames.append(frame)
        while len(self._frames) > self.max_frames:
            self._frames.popleft().image_path.unlink(missing_ok=True)
        return frame

    def retain(self, frame: RuntimeFrame, directory: Path) -> Path:
        """Save boundary/mismatch evidence only, maintaining content identity."""
        return Path(retain_snapshot(snapshot_frame(frame), directory)["image_path"])

    def close(self):
        self._directory.cleanup()
