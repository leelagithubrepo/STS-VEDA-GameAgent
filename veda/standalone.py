"""Watch-only standalone runtime for VEDA.

This module intentionally has no controller imports.  It makes VEDA useful
outside an editor by continually collecting auditable observations, while
leaving all PS5 input under human control.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .observation import DEFAULT_CAPTURE_DIR, capture_visible_ps5_feed


def observation_status(image: Path, *, captured_at: datetime | None = None) -> dict[str, str]:
    """Build the small, stable status document written by the watcher."""
    timestamp = captured_at or datetime.now(timezone.utc)
    return {
        "schema": "veda.standalone-observation.v1",
        "mode": "watch_only",
        "captured_at": timestamp.isoformat(),
        "image": str(image),
        "controller_input": "disabled",
    }


def write_observation_status(image: Path, status_file: Path) -> dict[str, str]:
    """Atomically publish the latest passive observation for dashboards/tools."""
    status = observation_status(image)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_file.with_suffix(status_file.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    temporary.replace(status_file)
    return status


def capture_and_publish(
    *,
    capture: Callable[[Path], Path] = capture_visible_ps5_feed,
    capture_dir: Path = DEFAULT_CAPTURE_DIR,
    status_file: Path | None = None,
) -> dict[str, str]:
    """Capture one frame and publish its path without interpreting or acting."""
    image = capture(capture_dir)
    destination = status_file or capture_dir.parent / "standalone-status.json"
    return write_observation_status(image, destination)
