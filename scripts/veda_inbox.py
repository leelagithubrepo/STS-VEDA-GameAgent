#!/usr/bin/env python3
"""Read and acknowledge standalone VEDA's local review mailbox."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.mailbox import mailbox_status, receive_messages


ROOT = Path(__file__).resolve().parents[1]
MAILBOX = ROOT / "artifacts" / "mailbox" / "review-mailbox.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read VEDA feedback before the next meaningful decision")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(mailbox_status(mailbox_path=MAILBOX))
        return 0
    messages = receive_messages(mailbox_path=MAILBOX, recipient="veda")
    if not messages:
        print("No unread LLM feedback.")
        return 0
    for message in messages:
        print(f"[{message['kind']}] {message['body']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
