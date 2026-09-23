"""Load reviewed research claims from a serializable, inspectable catalog."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .knowledge import Claim, ClaimKind, KnowledgeBase, Source


def load_catalog(path: Path, knowledge: KnowledgeBase) -> list[Claim]:
    document = json.loads(path.read_text(encoding="utf-8"))
    claims: list[Claim] = []
    for item in document["claims"]:
        sources = tuple(Source(**{**source, "captured_at": datetime.fromisoformat(
            source.get("captured_at", document["captured_at"]))}) for source in item["sources"])
        claims.append(knowledge.add(Claim(
            statement=item["statement"], kind=ClaimKind(item["kind"]), sources=sources,
            tags=frozenset(item.get("tags", [])), conditions=item.get("conditions"),
            rationale=item.get("rationale"), confidence=item["confidence"],
            **({"id": item["id"]} if "id" in item else {}),
        )))
    return claims
