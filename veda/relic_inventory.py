"""Evidence-backed relic inventory for runs where the relic bar is hard to read."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = "veda.relic-inventory.v1"


def load_relic_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": SCHEMA, "relics": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA or not isinstance(document.get("relics"), list):
        raise ValueError("not a VEDA relic inventory")
    return document


def record_relic(
    *, inventory_path: Path, name: str, property_text: str, source: str,
    confidence: float, act: int | None = None, floor: int | None = None,
    screenshot: Path | None = None, asset_dir: Path | None = None,
) -> dict[str, Any]:
    """Record a confirmed relic and its exact observed or researched property."""
    if not name.strip() or not property_text.strip() or not source.strip():
        raise ValueError("name, property_text, and source are required")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if screenshot is not None and not screenshot.is_file():
        raise FileNotFoundError(f"screenshot not found: {screenshot}")
    document = load_relic_inventory(inventory_path)
    evidence = None
    if screenshot is not None:
        if asset_dir is None:
            raise ValueError("asset_dir is required when recording a screenshot")
        asset_dir.mkdir(parents=True, exist_ok=True)
        filename = f"relic-{uuid4().hex[:8]}{screenshot.suffix.lower() or '.png'}"
        shutil.copy2(screenshot, asset_dir / filename)
        evidence = f"assets/relics/{filename}"
    entry = {
        "id": str(uuid4()), "name": name.strip(), "property": property_text.strip(),
        "source": source.strip(), "confidence": confidence, "act": act, "floor": floor,
        "screenshot": evidence, "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    document["relics"].append(entry)
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


def inventory_summary(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the latest evidence for each relic without treating uncertain reads as fact."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in document["relics"]:
        key = entry["name"].casefold()
        if key not in latest or entry["recorded_at"] > latest[key]["recorded_at"]:
            latest[key] = entry
    return sorted(latest.values(), key=lambda entry: entry["name"].casefold())
