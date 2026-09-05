#!/usr/bin/env python3
"""Open and close a VEDA Remote Play session without sending controller input."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("VEDA_ASSUME_PS5_ON", "1")

from ps5rmtctl.config import get_default
from ps5rmtctl.core import PS5


async def main() -> None:
    host = get_default("host")
    user = get_default("user")
    if not host or not user:
        raise SystemExit("VEDA bridge has no saved host or user; run ./scripts/bridge setup first.")
    async with PS5(host, user).session(timeout=15):
        print("Remote Play session ready (no controller input sent).")


if __name__ == "__main__":
    asyncio.run(main())
