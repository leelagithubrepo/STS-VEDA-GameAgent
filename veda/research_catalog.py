"""Load reviewed research claims from a serializable, inspectable catalog."""

from __future__ import annotations

import json
from pathlib import Path

from .knowledge import Claim, ClaimKind, KnowledgeBase, Source


def load_catalog(path: Path, knowledge: KnowledgeBase) -> list[Claim]:
    document = json.loads(path.read_text(encoding="utf-8"))
    claims: list[Claim] = []
    for item in document["claims"]:
        sources = tuple(Source(**source) for source in item["sources"])
        claims.append(knowledge.add(Claim(
            statement=item["statement"], kind=ClaimKind(item["kind"]), sources=sources,
            tags=frozenset(item.get("tags", [])), conditions=item.get("conditions"),
            rationale=item.get("rationale"), confidence=item["confidence"],
        )))
    return claims
