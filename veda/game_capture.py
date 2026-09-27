"""Passive QuickTime-window capture, independent of which app is foreground.

A selected video window is not proof that its pixels contain a fresh PS5 feed.
The operating advisor must inspect every result. No device selection, activation,
controller, pairing, permission change, or network access occurs here.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def _probe_binary():
    source = ROOT / "scripts/game_windows.swift"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = ROOT / "artifacts/capture-tools"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("game-windows-" + digest[:20])
    if not target.is_file():
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            output = Path(temporary) / "probe"
            subprocess.run(["/usr/bin/xcrun", "swiftc", str(source), "-o", str(output)],
                           capture_output=True, check=True, timeout=60)
            os.replace(output, target)
    return target


def select_game_window(windows, *, window_id=None):
    """Require one exact known capture window; ambiguous/unread titles fail."""
    if window_id is not None and (type(window_id) is not int or window_id <= 0):
        raise ValueError("window ID must be a positive integer")
    if not isinstance(windows, list) or len(windows) > 64:
        raise ValueError("bounded video-window listing required")
    choices = []
    for window in windows:
        if (not isinstance(window, dict) or window.get("owner") != "QuickTime Player"
                or window.get("title") != "Movie Recording"
                or type(window.get("id")) is not int or window["id"] <= 0
                or type(window.get("width")) not in (int, float)
                or type(window.get("height")) not in (int, float)
                or not math.isfinite(window["width"]) or not math.isfinite(window["height"])
                or window["width"] < 320 or window["height"] < 180):
            continue
        if window_id is None or window["id"] == window_id:
            choices.append(window)
    if len(choices) != 1:
        raise ValueError("exactly one QuickTime Movie Recording window is required; inspect the capture window")
    return dict(choices[0])


def list_game_windows():
    result = subprocess.run([str(_probe_binary())], capture_output=True, check=True, timeout=5)
    if len(result.stdout) > 65536:
        raise ValueError("video-window listing exceeds bound")
    return json.loads(result.stdout)


def capture_game_window(output_dir, *, window_id=None):
    """Capture a known video window and write its source selection receipt."""
    if window_id is not None and (type(window_id) is not int or window_id <= 0):
        raise ValueError("window ID must be a positive integer")
    selected = select_game_window(list_game_windows(), window_id=window_id)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    began = datetime.now(timezone.utc)
    stamp = began.strftime("%Y%m%dT%H%M%S.%fZ")
    path = directory / f"ps5_observation_{stamp}_{uuid4().hex}.png"
    try:
        subprocess.run(["/usr/sbin/screencapture", "-x", "-o", "-l", str(selected["id"]),
                        "-t", "png", str(path)], check=True, capture_output=True, timeout=15)
        from .saved_frame_reader import _identity
        digest, dimensions = _identity(path)
        # Re-enumerate before publishing: a disappeared/replaced window is not
        # silently substituted with the foreground desktop or another movie.
        after = select_game_window(list_game_windows(), window_id=selected["id"])
        if any(after[k] != selected[k] for k in ("owner", "title", "width", "height")):
            raise ValueError("video-window identity or geometry changed during capture")
        if _identity(path) != (digest, dimensions):
            raise ValueError("captured image changed before source receipt publication")
        receipt = {"schema": "veda.game-window-capture.v1", "image_path": str(path.resolve()),
                   "image_sha256": digest, "dimensions": dimensions,
                   "capture_requested_at": began.isoformat(),
                   "capture_completed_at": datetime.now(timezone.utc).isoformat(),
                   "window": selected, "pixel_content_verified": False,
                   "controller_input_sent": False}
        path.with_suffix(".capture.json").write_text(json.dumps(receipt, indent=2) + "\n")
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        path.with_suffix(".capture.json").unlink(missing_ok=True)
        raise
