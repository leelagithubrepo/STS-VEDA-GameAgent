"""Passive visual capture for VEDA.

This module never touches the controller bridge. It only captures the Mac
display currently showing the PS5 feed and writes a timestamped PNG for later
state interpretation and audit.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE_DIR = PROJECT_ROOT / "artifacts" / "observations"


def capture_visible_ps5_feed(output_dir: Path = DEFAULT_CAPTURE_DIR) -> Path:
    """Save one image of the current display without changing any UI state."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir / f"ps5_observation_{timestamp}.png"
    try:
        subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-t", "png", str(destination)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        destination.unlink(missing_ok=True)
        detail = (getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or str(exc)).strip()
        raise RuntimeError(
            "Passive screen capture failed. Allow the terminal or editor in "
            "System Settings → Privacy & Security → Screen & System Audio Recording. "
            f"Details: {detail}"
        ) from exc
    if not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Passive screen capture produced no image")
    return destination
