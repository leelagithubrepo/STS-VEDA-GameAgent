"""Provider-neutral, read-only screenshot → Slay the Spire state interface."""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


SCREEN_TYPES = frozenset({
    "TITLE", "CHARACTER_SELECT", "NEOW", "MAP", "COMBAT", "CARD_REWARD",
    "SHOP", "REST_SITE", "EVENT", "TREASURE", "BOSS_RELIC", "RUN_COMPLETE", "UNKNOWN",
})

_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_INTEGER = {"type": ["integer", "null"], "minimum": 0}
OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "screen_type": {"type": "string", "enum": sorted(SCREEN_TYPES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "selected_item": _NULLABLE_STRING, "visible_actions": {"type": "array", "items": {"type": "string"}},
        "character": _NULLABLE_STRING, "ascension": _NULLABLE_INTEGER, "act": _NULLABLE_INTEGER, "floor": _NULLABLE_INTEGER,
        "hp": _NULLABLE_INTEGER, "max_hp": _NULLABLE_INTEGER, "energy": _NULLABLE_INTEGER,
        "block": _NULLABLE_INTEGER, "gold": _NULLABLE_INTEGER,
        "player_strength": _NULLABLE_INTEGER, "player_weak": _NULLABLE_INTEGER, "player_frail": _NULLABLE_INTEGER,
        "boss_name": _NULLABLE_STRING, "boss_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "encounter_kind": _NULLABLE_STRING, "encounter_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "map_nodes": {"type": "array", "items": {"type": "object", "properties": {
            "node_id": {"type": "string"}, "kind": {"type": "string"}, "reachable": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        }, "required": ["node_id", "kind", "reachable", "confidence"], "additionalProperties": False}},
        "end_turn_damage": _NULLABLE_INTEGER, "end_turn_damage_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "hand": {"type": "array", "items": {"type": "string"}},
        "enemies": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "hp": _NULLABLE_INTEGER, "max_hp": _NULLABLE_INTEGER,
            "block": _NULLABLE_INTEGER, "intent": _NULLABLE_STRING,
            "intent_total_damage": _NULLABLE_INTEGER, "intent_damage_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        }, "required": ["name", "hp", "max_hp", "block", "intent", "intent_total_damage", "intent_damage_confidence"], "additionalProperties": False}},
        "description": {"type": "string"},
    },
    "required": ["screen_type", "confidence", "selected_item", "visible_actions", "character", "ascension", "act", "floor", "hp", "max_hp", "energy", "block", "gold", "player_strength", "player_weak", "player_frail", "boss_name", "boss_confidence", "encounter_kind", "encounter_confidence", "map_nodes", "end_turn_damage", "end_turn_damage_confidence", "hand", "enemies", "description"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class VisibleEnemy:
    name: str
    hp: int | None
    max_hp: int | None
    intent: str | None
    block: int | None = None
    intent_total_damage: int | None = None
    intent_damage_confidence: float = 0.0


@dataclass(frozen=True)
class VisibleMapNode:
    node_id: str
    kind: str
    reachable: bool
    confidence: float


@dataclass(frozen=True)
class StructuredGameState:
    """The stable output contract all vision providers must produce.

    Fields are nullable whenever the screenshot does not prove their value.
    This prevents a provider from inventing game state to fill a schema.
    """
    screen_type: str
    confidence: float
    selected_item: str | None = None
    visible_actions: tuple[str, ...] = ()
    character: str | None = None
    ascension: int | None = None
    act: int | None = None
    floor: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    energy: int | None = None
    block: int | None = None
    gold: int | None = None
    player_strength: int | None = None
    player_weak: int | None = None
    player_frail: int | None = None
    boss_name: str | None = None
    boss_confidence: float = 0.0
    encounter_kind: str | None = None
    encounter_confidence: float = 0.0
    map_nodes: tuple[VisibleMapNode, ...] = ()
    end_turn_damage: int | None = None
    end_turn_damage_confidence: float = 0.0
    hand: tuple[str, ...] = ()
    enemies: tuple[VisibleEnemy, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.screen_type not in SCREEN_TYPES:
            raise ValueError(f"unsupported screen type: {self.screen_type}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        for value in (self.ascension, self.act, self.floor, self.hp, self.max_hp, self.energy, self.block, self.gold, self.player_strength, self.player_weak, self.player_frail, self.end_turn_damage):
            if value is not None and value < 0:
                raise ValueError("numeric game-state values cannot be negative")
        for label, confidence in (("end-turn damage", self.end_turn_damage_confidence), ("boss", self.boss_confidence), ("encounter", self.encounter_confidence)):
            if not 0 <= confidence <= 1:
                raise ValueError(f"{label} confidence must be between 0 and 1")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StructuredGameState":
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if set(value) != expected:
            raise ValueError("vision response fields do not match the state contract")
        enemies = tuple(VisibleEnemy(**enemy) for enemy in value["enemies"])
        map_nodes = tuple(VisibleMapNode(**node) for node in value["map_nodes"])
        return cls(
            screen_type=value["screen_type"], confidence=float(value["confidence"]),
            selected_item=value["selected_item"], visible_actions=tuple(value["visible_actions"]),
            character=value["character"], ascension=value["ascension"], act=value["act"], floor=value["floor"], hp=value["hp"],
            max_hp=value["max_hp"], energy=value["energy"], block=value["block"], gold=value["gold"],
            player_strength=value["player_strength"], player_weak=value["player_weak"], player_frail=value["player_frail"],
            boss_name=value["boss_name"], boss_confidence=float(value["boss_confidence"]),
            encounter_kind=value["encounter_kind"], encounter_confidence=float(value["encounter_confidence"]), map_nodes=map_nodes,
            end_turn_damage=value["end_turn_damage"], end_turn_damage_confidence=float(value["end_turn_damage_confidence"]),
            hand=tuple(value["hand"]), enemies=enemies, description=value["description"],
        )

    def as_observation(self) -> dict[str, Any]:
        """Normalize visual state for the generic VEDA agent loop."""
        value = asdict(self)
        value["tags"] = [self.screen_type.lower(), *("combat" if self.screen_type == "COMBAT" else "",)]
        value["tags"] = [tag for tag in value["tags"] if tag]
        return value


@dataclass(frozen=True)
class ActionReadiness:
    """A conservative authorization check between vision and any game input."""
    ready: bool
    reasons: tuple[str, ...] = ()


def combat_action_readiness(state: StructuredGameState) -> ActionReadiness:
    """Require the minimum visually verified combat state before VEDA can act.

    This is deliberately stricter than schema validity. Unknown enemy intents
    make damage planning impossible, so an otherwise valid combat parse remains
    observation-only until those fields are legible and calibrated.
    """
    reasons: list[str] = []
    if state.screen_type != "COMBAT":
        reasons.append("screen is not confirmed as combat")
    if state.confidence < 0.85:
        reasons.append("vision confidence is below the action threshold")
    if state.hp is None or state.max_hp is None or state.energy is None or state.block is None:
        reasons.append("player HP, Block, or energy is missing")
    if state.end_turn_damage is None or state.end_turn_damage_confidence < 0.9:
        reasons.append("end-of-turn damage is unconfirmed")
    if not state.hand:
        reasons.append("no playable hand was extracted")
    if not state.enemies:
        reasons.append("no enemy was extracted")
    for enemy in state.enemies:
        if enemy.hp is None or enemy.max_hp is None:
            reasons.append("an enemy HP value is missing")
            break
        if enemy.block is None:
            reasons.append("an enemy Block value is missing")
            break
        if enemy.intent is None or enemy.intent.strip().lower() in {"", "unknown", "unknown intent"}:
            reasons.append("an enemy intent is unknown")
            break
        if enemy.intent_total_damage is None or enemy.intent_damage_confidence < 0.9:
            reasons.append("an enemy's total intent damage is unconfirmed")
            break
    return ActionReadiness(not reasons, tuple(reasons))


class VisionProvider(Protocol):
    def observe(self, image_path: Path) -> StructuredGameState: ...


class LocalOllamaVisionProvider:
    """Local-only multimodal provider served at localhost by Ollama.

    It sends screenshot bytes exclusively to the configured local endpoint.
    There is no cloud endpoint, API key, or controller operation here.
    """
    def __init__(
        self,
        model: str = "blaifa/InternVL3_5:4B",
        endpoint: str = "http://127.0.0.1:11434/api/chat",
        timeout_seconds: float = 30,
        request: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.model, self.endpoint, self.timeout_seconds = model, endpoint, timeout_seconds
        self._request_override = request
        self.last_error: str | None = None
        self.last_raw_response: str | None = None

    def _request(self, body: bytes) -> bytes:
        if self._request_override:
            return self._request_override(body)
        result = subprocess.run(
            ["curl", "--silent", "--show-error", "--fail", "--max-time", str(self.timeout_seconds),
             "--request", "POST", "--header", "Content-Type: application/json", "--data-binary", "@-", self.endpoint],
            input=body, capture_output=True, check=True, timeout=self.timeout_seconds + 2,
        )
        return result.stdout

    @staticmethod
    def _fallback(reason: str) -> StructuredGameState:
        return StructuredGameState("UNKNOWN", 0.0, description=reason)

    @staticmethod
    def _normalize_model_state(value: dict[str, Any]) -> dict[str, Any]:
        """Normalize harmless presentation differences before contract validation."""
        normalized = dict(value)
        confidence = normalized.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 1 < confidence <= 100:
            normalized["confidence"] = confidence / 100
        return normalized

    def observe(self, image_path: Path) -> StructuredGameState:
        prompt = """You are a read-only Slay the Spire screen interpreter. Inspect only the game content;
ignore the Mac menu bar, dock, editor, terminal, chat, and all non-game windows. Do not invent values.

Screen taxonomy:
- TITLE: the main menu with choices such as Play, Compendium, Statistics, and Settings.
- CHARACTER_SELECT: a character portrait and its starting HP/gold/relic, character slots, and an Embark button.
- NEOW: the opening bonus-choice scene.
- MAP: connected floor nodes and routes.
- COMBAT: cards in hand, enemies, and End Turn.
- CARD_REWARD, SHOP, REST_SITE, EVENT, TREASURE, BOSS_RELIC: their respective post-floor choice screens.

On TITLE, set every combat/run field to null and hand/enemies/map_nodes to empty arrays. On CHARACTER_SELECT,
read the displayed character name, Ascension level, HP, max HP, and gold if legible. Read gold only from the value
directly beside the word "Gold" near the character name; do not use numbers from the unlock progress
text. On COMBAT, identify it from the hand of cards across the bottom and End Turn at lower right.
The player's HP is top left near the character name. Gold is top left directly beside the gold icon.
Current energy is the first number in the circular X/X counter at lower left; never use gold or enemy
HP as energy. Player Block is the shield value beside the player, if one is visibly present. Enemy HP
is shown under each enemy. Name an enemy only if its name is readable; otherwise use "unknown enemy".
For every enemy, report its current Block, the readable intent text, and the total damage that intent
will deal this turn. For a multi-hit intent such as 4x6, report 24, not 4 or 6. Use null and confidence
0 when the total cannot be read or calculated from visible text. Sum only visible player end-of-turn
damage statuses (for example, three Burns = 6) into end_turn_damage; use 0 only when you can verify
there is none. On MAP, report each currently reachable node with a stable local node_id, its visible
kind, and confidence. Report a boss_name only when the name itself is readable; boss art is not proof.
On COMBAT, set encounter_kind to enemy, elite, or boss only when the screen proves it; otherwise use
null and confidence 0. Also report player_strength, player_weak, and player_frail from visible player
status icons; use null when a count is not legible. Read a value only if it is visibly legible. Do not
recommend or execute any action."""
        payload = json.dumps({
            "model": self.model,
            "messages": [{
                "role": "user", "content": prompt,
                "images": [base64.b64encode(image_path.read_bytes()).decode("ascii")],
            }],
            # Qwen3-VL otherwise spends its response budget on hidden reasoning
            # and can return an empty visible JSON response. The same switch is
            # harmless for non-thinking local models.
            "format": OBSERVATION_SCHEMA, "stream": False, "think": False,
            "options": {"temperature": 0},
        }).encode("utf-8")
        try:
            self.last_raw_response = self._request(payload).decode("utf-8")
            response = json.loads(self.last_raw_response)
            self.last_error = None
            return StructuredGameState.from_dict(
                self._normalize_model_state(json.loads(response["message"]["content"]))
            )
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.last_error = type(error).__name__
            return self._fallback(
                f"Local vision unavailable or returned an invalid state ({self.last_error}); no state was guessed."
            )
