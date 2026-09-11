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

This program deliberately accepts only a small allowlist of controller
operations.  It never prints the PSN user, host, pairing data, or API token.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from ps5rmtctl.config import get_default
from ps5rmtctl.core import PS5Error
from ps5rmtctl.service import PS5Service


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def _parse_line(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("command must be a JSON object")
    return payload


async def _handle(service: PS5Service, command: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    action = command.get("action")
    if action == "tap":
        buttons = command.get("buttons")
        if not isinstance(buttons, list) or not buttons or not all(isinstance(button, str) for button in buttons):
            raise ValueError("tap needs a non-empty string array: buttons")
        result = await service.tap(
            buttons,
            delay=float(command.get("delay", 0.12)),
            gap=float(command.get("gap", 0.10)),
        )
        return {"action": action, "buttons": result}, False
    if action == "stick":
        result = await service.stick(
            str(command.get("stick", "left")), command.get("x", 0.0), command.get("y", 0.0)
        )
        return {"action": action, "stick": result}, False
    if action == "status":
        status = await service.status()
        # Avoid returning identifiers that are irrelevant to the decision loop.
        return {"action": action, "on": status["on"], "session_ready": status["session_ready"]}, False
    if action == "close":
        return {"action": action}, True
    raise ValueError("action must be one of: tap, stick, status, close")


async def _run(args: argparse.Namespace) -> int:
    host = get_default("host")
    user = get_default("user")
    if not host or not user:
        _emit({"ok": False, "error": "bridge is not configured; run scripts/bridge setup"})
        return 2

    service = PS5Service(host, user, idle_timeout=args.idle_timeout)
    started = time.monotonic()
    try:
        await service.connect()
        _emit({"ok": True, "event": "ready", "connect_ms": round((time.monotonic() - started) * 1000)})
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            began = time.monotonic()
            try:
                response, should_close = await _handle(service, _parse_line(line))
                response.update({"ok": True, "latency_ms": round((time.monotonic() - began) * 1000)})
                _emit(response)
                if should_close:
                    break
            except (ValueError, TypeError, PS5Error) as exc:
                _emit({"ok": False, "error": str(exc)})
    finally:
        await service.release_all()
        await service.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="VEDA persistent PS5 controller session (JSONL stdin/stdout).")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=0.0,
        help="Release after inactivity in seconds; 0 keeps the session warm until closed.",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
