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
    act: int | None = None
    floor: int | None = None
    energy: int | None = None
    hand: list[str] = field(default_factory=list)
    pending: list[LedgerEvent] = field(default_factory=list)
    confirmed: list[LedgerEvent] = field(default_factory=list)

    def propose(self, kind: str, value: str, source: str) -> None:
        self.pending.append(LedgerEvent(kind, value, source))

    def confirm(self, kind: str, value: str, source: str) -> None:
        event = LedgerEvent(kind, value, source)
        self.pending = [item for item in self.pending if item != event]
        self.confirmed.append(event)
        collection = {"card": self.deck, "relic": self.relics, "potion": self.potions, "upgrade": self.upgrades}.get(kind)
        if collection is not None:
            # Cards and potion slots have meaningful duplicates. Relics and
            # upgrades remain unique facts.
            if kind in {"card", "potion"} or value not in collection:
                collection.append(value)

    def confirm_snapshot(self, *, hp: int | None, max_hp: int | None, gold: int | None) -> None:
        self.hp, self.max_hp, self.gold = hp, max_hp, gold

    def confirm_combat_snapshot(self, *, energy: int | None, hand: tuple[str, ...], source: str) -> None:
        """Record only cards visibly confirmed in the current hand."""
        self.energy = energy
        self.hand = list(hand)
        self.confirmed.append(LedgerEvent("combat_snapshot", ", ".join(hand), source))

    def discard_potion(self, name: str, source: str) -> None:
        """Remove exactly one confirmed potion slot, preserving duplicates."""
        try:
            self.potions.remove(name)
        except ValueError as exc:
            raise ValueError(f"cannot discard unknown potion: {name}") from exc
        self.confirmed.append(LedgerEvent("potion_discarded", name, source))

    def replace_potion(self, *, discard: str, gain: str, source: str) -> None:
        """Make a full-inventory potion choice explicit and auditable."""
        self.discard_potion(discard, source)
        self.confirm("potion", gain, source)

    def confirm_location(self, *, act: int | None, floor: int | None) -> None:
        self.act, self.floor = act, floor
