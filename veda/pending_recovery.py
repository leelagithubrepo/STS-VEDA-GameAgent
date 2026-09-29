"""Safe discovery of already-written result packets for pending actions."""

import json
from pathlib import Path
from .adapter_channel import session_directory
from .request_envelope import MAX_BYTES


def find_matching_verify_request(session, action_id):
    """Return the newest exact verify packet for *action_id*, if present.

    This intentionally looks only at ordinary JSON files inside the session
    directory and requires the packet's operation and action id to match.  It
    never guesses from a capture, and it never treats a prepared/action packet
    as a result packet.
    """
    directory = session_directory(session)
    if not directory.is_dir() or not isinstance(action_id, str) or not action_id:
        return None
    matches = []
    for path in directory.rglob("*.json"):
        if path.name == "state.json":
            continue
        # Do not follow links or accept files outside the owned session tree.
        try:
            if path.is_symlink() or not path.is_file() or path.resolve().parent != path.parent.resolve():
                continue
        except OSError:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if (isinstance(value, dict) and value.get("operation") == "verify"
                and value.get("action_id") == action_id
                and {'operation_id', 'after', 'action_id', 'operation'} <= set(value)):
            try:
                matches.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
    return max(matches, key=lambda item: item[0])[1] if matches else None
