"""Durable local mailbox between standalone VEDA and an LLM reviewer.

The mailbox is intentionally local and file-backed for the current standalone
workflow. It makes receipt explicit; a separate Terminal chat does not count
as having received feedback until it reads and acknowledges the message.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = "veda.review-mailbox.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_mailbox(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": SCHEMA, "messages": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA or not isinstance(document.get("messages"), list):
        raise ValueError("not a VEDA review mailbox")
    return document


def _write(document: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def send_message(*, mailbox_path: Path, sender: str, recipient: str, kind: str, body: str, related_id: str | None = None) -> dict[str, Any]:
    if sender not in {"veda", "llm"} or recipient not in {"veda", "llm"} or sender == recipient:
        raise ValueError("messages must be exchanged between veda and llm")
    if kind not in {"review_request", "review_feedback", "implementation_confirmation"}:
        raise ValueError("unsupported mailbox message kind")
    if not body.strip():
        raise ValueError("message body must not be empty")
    document = load_mailbox(mailbox_path)
    message = {
        "id": str(uuid4()), "sender": sender, "recipient": recipient, "kind": kind,
        "body": body.strip(), "related_id": related_id, "sent_at": _now(), "received_at": None,
    }
    document["messages"].append(message)
    _write(document, mailbox_path)
    return message


def receive_messages(*, mailbox_path: Path, recipient: str) -> list[dict[str, Any]]:
    """Return unread messages and atomically mark their receipt."""
    if recipient not in {"veda", "llm"}:
        raise ValueError("recipient must be veda or llm")
    document = load_mailbox(mailbox_path)
    unread = [message for message in document["messages"] if message["recipient"] == recipient and message["received_at"] is None]
    for message in unread:
        message["received_at"] = _now()
    if unread:
        _write(document, mailbox_path)
    return unread


def mailbox_status(*, mailbox_path: Path) -> dict[str, int]:
    document = load_mailbox(mailbox_path)
    return {
        "veda_unread": sum(item["recipient"] == "veda" and item["received_at"] is None for item in document["messages"]),
        "llm_unread": sum(item["recipient"] == "llm" and item["received_at"] is None for item in document["messages"]),
    }
