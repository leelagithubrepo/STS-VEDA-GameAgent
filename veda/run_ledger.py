"""Confirmed run inventory; pending visual guesses never become inventory."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LedgerEvent:
    kind: str
    value: str
    source: str


@dataclass
class RunLedger:
    deck: list[str] = field(default_factory=list)
    relics: list[str] = field(default_factory=list)
    potions: list[str] = field(default_factory=list)
    upgrades: list[str] = field(default_factory=list)
    hp: int | None = None
    max_hp: int | None = None
    gold: int | None = None
    pending: list[LedgerEvent] = field(default_factory=list)
    confirmed: list[LedgerEvent] = field(default_factory=list)

    def propose(self, kind: str, value: str, source: str) -> None:
        self.pending.append(LedgerEvent(kind, value, source))

    def confirm(self, kind: str, value: str, source: str) -> None:
        event = LedgerEvent(kind, value, source)
        self.pending = [item for item in self.pending if item != event]
        self.confirmed.append(event)
        collection = {"card": self.deck, "relic": self.relics, "potion": self.potions, "upgrade": self.upgrades}.get(kind)
        if collection is not None and value not in collection:
            collection.append(value)

    def confirm_snapshot(self, *, hp: int | None, max_hp: int | None, gold: int | None) -> None:
        self.hp, self.max_hp, self.gold = hp, max_hp, gold
