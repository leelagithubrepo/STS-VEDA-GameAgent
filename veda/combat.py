"""A small, explicit combat arithmetic boundary for recommended sequences.

It is intentionally a calculator, not a strategy policy: callers provide a
candidate sequence and receive either a rejection or a prediction based only
on supplied, visible values.
"""

from __future__ import annotations

from dataclasses import dataclass


def _scaled(value: int, *, weak: bool = False, vulnerable: bool = False) -> int:
    """Apply the relevant base-game multiplicative modifiers, rounding down."""
    if weak:
        value = int(value * 0.75)
    if vulnerable:
        value = int(value * 1.5)
    return value


@dataclass(frozen=True)
class CardEffect:
    name: str
    cost: int
    card_type: str
    attack_damage: int = 0
    block: int = 0
    weak: int = 0
    vulnerable: int = 0
    target: str | None = None
    exhausts_hand: bool = False
    damage_per_exhausted: int = 0


@dataclass(frozen=True)
class CombatEnemy:
    name: str
    hp: int
    block: int = 0
    artifact: int = 0
    vulnerable: int = 0


@dataclass(frozen=True)
class CombatSnapshot:
    energy: int | None
    player_hp: int | None = None
    player_weak: int = 0
    player_frail: int = 0
    player_block: int = 0
    incoming_damage: int | None = None
    end_turn_damage: int | None = None
    hand_size: int | None = None
    hand: tuple[str, ...] | None = None
    enemies: tuple[CombatEnemy, ...] = ()


@dataclass(frozen=True)
class SequenceCheck:
    legal: bool
    reasons: tuple[str, ...]
    energy_spent: int
    energy_remaining: int | None
    player_block: int | None
    enemies: tuple[CombatEnemy, ...]
    projected_player_hp: int | None = None
    incoming_damage: int | None = None
    end_turn_damage: int | None = None
    lethal: bool = False


def choose_verified_sequence(snapshot: CombatSnapshot, candidates: tuple[tuple[CardEffect, ...], ...]) -> SequenceCheck | None:
    """Pick the safest arithmetic-verified candidate without inventing strategy.

    A future policy or local planner proposes legal-looking sequences.  VEDA
    owns the deterministic selection: reject lethal/illegal candidates, then
    prefer lower remaining enemy HP, higher projected player HP, and lower
    energy spend.  Unknown combat effects are intentionally outside this
    calculator and must be escalated rather than guessed.
    """
    checks = [validate_and_predict(snapshot, sequence) for sequence in candidates]
    allowed = [check for check in checks if check.legal and not check.lethal]
    if not allowed:
        return None
    return min(
        allowed,
        key=lambda check: (
            sum(enemy.hp for enemy in check.enemies),
            -(check.projected_player_hp if check.projected_player_hp is not None else -10_000),
            check.energy_spent,
        ),
    )


def validate_and_predict(snapshot: CombatSnapshot, cards: tuple[CardEffect, ...]) -> SequenceCheck:
    """Reject unknown/impossible sequences, otherwise predict only direct effects."""
    if snapshot.energy is None:
        return SequenceCheck(False, ("current energy is unconfirmed",), 0, None, None, snapshot.enemies)
    energy = snapshot.energy
    reasons: list[str] = []
    enemies = list(snapshot.enemies)
    block = snapshot.player_block
    spent = 0
    cards_in_hand = snapshot.hand_size
    # A named hand is stronger evidence than a count. It prevents a plan from
    # using a card that is merely in the deck, or using one copy twice.
    available_cards = list(snapshot.hand) if snapshot.hand is not None else None
    if available_cards is not None:
        cards_in_hand = len(available_cards)

    for card in cards:
        if available_cards is not None:
            try:
                available_cards.remove(card.name)
            except ValueError:
                reasons.append(f"{card.name} is not in the confirmed hand")
                continue
        if card.cost < 0:
            reasons.append(f"{card.name} has an invalid negative cost")
            continue
        if card.cost > energy:
            reasons.append(f"not enough energy for {card.name}: needs {card.cost}, has {energy}")
            continue
        if (card.attack_damage or card.weak or card.vulnerable) and card.target is None:
            reasons.append(f"{card.name} needs a confirmed target")
            continue
        target_index = next((i for i, enemy in enumerate(enemies) if enemy.name == card.target and enemy.hp > 0), None)
        if (card.attack_damage or card.weak or card.vulnerable) and target_index is None:
            reasons.append(f"{card.name} targets an absent or defeated enemy")
            continue

        energy -= card.cost
        spent += card.cost
        if cards_in_hand is not None:
            cards_in_hand = max(0, cards_in_hand - 1)
        if card.block:
            gained = _scaled(card.block, weak=False, vulnerable=False)
            if snapshot.player_frail:
                gained = int(gained * 0.75)
            block += gained
        if target_index is not None:
            enemy = enemies[target_index]
            artifact = enemy.artifact
            weak = card.weak
            vulnerable = card.vulnerable
            # Debuffs resolve in the order specified on the card. Artifact
            # consumes one debuff at a time rather than cancelling the card.
            if weak and artifact:
                artifact -= 1
                weak = 0
            if vulnerable and artifact:
                artifact -= 1
                vulnerable = 0
            enemy_vulnerable = enemy.vulnerable + vulnerable
            if card.exhausts_hand:
                if cards_in_hand is None:
                    reasons.append(f"{card.name} needs a confirmed hand size")
                    continue
                exhausted = cards_in_hand
                damage = _scaled(
                    card.damage_per_exhausted, weak=snapshot.player_weak > 0, vulnerable=enemy_vulnerable > 0,
                ) * exhausted
                cards_in_hand = 0
            else:
                damage = _scaled(card.attack_damage, weak=snapshot.player_weak > 0, vulnerable=enemy_vulnerable > 0)
            absorbed = min(enemy.block, damage)
            enemies[target_index] = CombatEnemy(
                name=enemy.name, hp=max(0, enemy.hp - (damage - absorbed)),
                block=enemy.block - absorbed, artifact=artifact, vulnerable=enemy_vulnerable,
            )

    projected_hp: int | None = None
    lethal = False
    # A survival forecast is only made when every required value was explicitly
    # observed.  `0` is meaningful: it means a verified non-attacking turn or
    # no end-of-turn status damage.
    if snapshot.player_hp is not None and snapshot.incoming_damage is not None and snapshot.end_turn_damage is not None:
        # Combat ends immediately on the final kill: no enemy intent or
        # end-of-turn status damage resolves after that point.
        combat_ended = not any(enemy.hp > 0 for enemy in enemies)
        incoming = 0 if combat_ended else snapshot.incoming_damage
        end_turn = 0 if combat_ended else snapshot.end_turn_damage
        post_attack_block = max(0, block - incoming)
        projected_hp = snapshot.player_hp - max(0, incoming - block)
        projected_hp -= max(0, end_turn - post_attack_block)
        lethal = projected_hp <= 0
        if lethal:
            reasons.append(
                "sequence is lethal after confirmed incoming and end-of-turn damage"
            )
    elif any(value is not None for value in (snapshot.player_hp, snapshot.incoming_damage, snapshot.end_turn_damage)):
        reasons.append("survival forecast needs confirmed player HP, incoming damage, and end-of-turn damage")

    return SequenceCheck(
        not reasons, tuple(reasons), spent, energy, block, tuple(enemies),
        projected_hp, snapshot.incoming_damage, snapshot.end_turn_damage, lethal,
    )
