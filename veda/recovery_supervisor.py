"""Small, side-band recovery supervisor for external PlayStation failures.

The supervisor never sends controller input and is only started after the live
loop has already paused. Keeping it separate prevents health probes from being
issued during an ordinary move.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


INFRASTRUCTURE_MARKERS = (
    "playstation", "remote play", "network", "feed", "transport", "bridge",
    "socket", "connection", "timeout", "unreachable", "disconnected",
)


def classify_failure(reason: str, *, input_sent: bool = False) -> str:
    """Return a stable class used by the launcher and learning replay."""
    text = str(reason or "").lower()
    if any(marker in text for marker in INFRASTRUCTURE_MARKERS):
        return "infrastructure_post_input" if input_sent else "infrastructure_pre_input"
    return "post_input_unknown" if input_sent else "pre_input"


def retry_delays(limit: int = 6) -> list[float]:
    """Bounded exponential delays; called only while paused."""
    if type(limit) is not int or limit < 0 or limit > 64:
        raise ValueError("retry limit must be an integer from 0 through 64")
    delays = []
    value = 1.0
    for _ in range(limit):
        delays.append(min(30.0, value))
        value = min(30.0, value * 2.0)
    return delays


@dataclass(frozen=True)
class RecoveryEvent:
    run_id: str
    event: str
    failure_class: str
    reason: str
    pending_action_id: str | None
    recorded_at: str
    attempt: int = 0


class RecoveryJournal:
    def __init__(self, run_dir: Path, run_id: str):
        self.path = Path(run_dir) / "recovery-events.jsonl"
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, reason: str, *, pending_action_id: str | None,
               input_sent: bool, attempt: int = 0) -> dict[str, Any]:
        record = RecoveryEvent(
            run_id=self.run_id, event=event,
            failure_class=classify_failure(reason, input_sent=input_sent),
            reason=str(reason), pending_action_id=pending_action_id,
            recorded_at=datetime.now(timezone.utc).isoformat(), attempt=attempt,
        )
        payload = asdict(record)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
        return payload


def recovery_summary(path: Path) -> dict[str, int]:
    """Summarize journal evidence for the continuous-improvement report."""
    counts = {"interruptions": 0, "recoveries": 0, "failed_recoveries": 0}
    if not Path(path).exists():
        return counts
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line).get("event")
        except json.JSONDecodeError:
            continue
        if event == "interruption": counts["interruptions"] += 1
        elif event == "recovered": counts["recoveries"] += 1
        elif event == "recovery_failed": counts["failed_recoveries"] += 1
    return counts
