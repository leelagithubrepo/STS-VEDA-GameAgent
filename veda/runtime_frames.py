"""Frame provenance and a reusable bounded capture source; no import side effects."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import time
from typing import Callable
from uuid import uuid4

from .observation import capture_visible_ps5_feed


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
        path = Path(self.capture(Path(self._directory.name)))
        if path.parent.resolve() != Path(self._directory.name).resolve():
            raise ValueError("capture must return a frame in its bounded source directory")
        if path.stat().st_size > self.max_frame_bytes:
            path.unlink()
            raise ValueError("frame exceeds configured byte budget")
        frame = RuntimeFrame.from_path(path, source="live_capture", ephemeral=True, clock=self.clock)
        self._frames.append(frame)
        while len(self._frames) > self.max_frames:
            self._frames.popleft().image_path.unlink(missing_ok=True)
        return frame

    def retain(self, frame: RuntimeFrame, directory: Path) -> Path:
        """Save boundary/mismatch evidence only, maintaining content identity."""
        data = frame.image_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != frame.sha256:
            raise ValueError("frame content changed before archival")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{frame.frame_id}{frame.image_path.suffix}"
        with target.open("xb") as stream:
            stream.write(data)
        return target

    def close(self):
        self._directory.cleanup()
