"""Slay the Spire vocabulary and boundary for a future visual/PS5 adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Protocol


@dataclass(frozen=True)
class SlayTheSpireState:
    character: str
    ascension: int
    act: int
    screen: str
    hp: int | None = None
    max_hp: int | None = None
    energy: int | None = None
    deck: tuple[str, ...] = ()
    relics: tuple[str, ...] = ()
    potions: tuple[str, ...] = ()
    enemies: tuple[dict[str, Any], ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def as_observation(self) -> dict[str, Any]:
        result = asdict(self)
        result["tags"] = list(self.tags)
        return result


class SlayTheSpireAdapter(Protocol):
    """Implement using screenshot/UI recognition and a PS5 controller bridge."""
    def observe(self) -> dict[str, Any]: ...
    def legal_actions(self, state: dict[str, Any]) -> list[dict[str, Any]]: ...
    def execute(self, action: dict[str, Any]) -> None: ...
