"""Provider-neutral, read-only screenshot → Slay the Spire state interface."""

from __future__ import annotations

import base64
from contextlib import nullcontext
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4


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
        "player_strength": _NULLABLE_INTEGER, "player_weak": _NULLABLE_INTEGER,
        "player_vulnerable": _NULLABLE_INTEGER, "player_frail": _NULLABLE_INTEGER,
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
            "intent_hits": {"type": ["array", "null"], "items": {"type": "integer", "minimum": 0}},
            "intent_total_damage": _NULLABLE_INTEGER, "intent_damage_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        }, "required": ["name", "hp", "max_hp", "block", "intent", "intent_hits", "intent_total_damage", "intent_damage_confidence"], "additionalProperties": False}},
        "description": {"type": "string"},
    },
    "required": ["screen_type", "confidence", "selected_item", "visible_actions", "character", "ascension", "act", "floor", "hp", "max_hp", "energy", "block", "gold", "player_strength", "player_weak", "player_vulnerable", "player_frail", "boss_name", "boss_confidence", "encounter_kind", "encounter_confidence", "map_nodes", "end_turn_damage", "end_turn_damage_confidence", "hand", "enemies", "description"],
    "additionalProperties": False,
}


OBSERVATION_SCHEMA['properties']['hand_complete'] = {'type': ['boolean', 'null']}
OBSERVATION_SCHEMA['properties']['hand_details'] = {'type': 'array', 'items': {'type': 'object', 'properties': {
    'name': {'type': 'string'}, 'title_color': {'type': ['string', 'null'], 'enum': ['green','teal','white',None]},
    'upgraded': {'type': ['boolean','null']}, 'current_cost': _NULLABLE_INTEGER,
}, 'required': ['name','title_color','upgraded','current_cost'], 'additionalProperties': False}}
OBSERVATION_SCHEMA['required'] += ['hand_complete', 'hand_details']


@dataclass(frozen=True)
class VisibleEnemy:
    name: str
    hp: int | None
    max_hp: int | None
    intent: str | None
    block: int | None = None
    intent_hits: tuple[int, ...] | None = None
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
    player_vulnerable: int | None = None
    player_frail: int | None = None
    boss_name: str | None = None
    boss_confidence: float = 0.0
    encounter_kind: str | None = None
    encounter_confidence: float = 0.0
    map_nodes: tuple[VisibleMapNode, ...] = ()
    end_turn_damage: int | None = None
    end_turn_damage_confidence: float = 0.0
    hand: tuple[str, ...] = ()
    hand_complete: bool | None = None
    hand_details: tuple[dict[str, Any], ...] = ()
    enemies: tuple[VisibleEnemy, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.screen_type not in SCREEN_TYPES:
            raise ValueError(f"unsupported screen type: {self.screen_type}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        for value in (self.ascension, self.act, self.floor, self.hp, self.max_hp, self.energy, self.block, self.gold, self.player_strength, self.player_weak, self.player_vulnerable, self.player_frail, self.end_turn_damage):
            if value is not None and value < 0:
                raise ValueError("numeric game-state values cannot be negative")
        for label, confidence in (("end-turn damage", self.end_turn_damage_confidence), ("boss", self.boss_confidence), ("encounter", self.encounter_confidence)):
            if not 0 <= confidence <= 1:
                raise ValueError(f"{label} confidence must be between 0 and 1")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StructuredGameState":
        value = {"hand_complete": None, "hand_details": [], **value}
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if set(value) != expected:
            raise ValueError("vision response fields do not match the state contract")
        enemies = tuple(
            VisibleEnemy(
                **{**enemy, "intent_hits": None if enemy["intent_hits"] is None else tuple(enemy["intent_hits"])}
            )
            for enemy in value["enemies"]
        )
        map_nodes = tuple(VisibleMapNode(**node) for node in value["map_nodes"])
        return cls(
            screen_type=value["screen_type"], confidence=float(value["confidence"]),
            selected_item=value["selected_item"], visible_actions=tuple(value["visible_actions"]),
            character=value["character"], ascension=value["ascension"], act=value["act"], floor=value["floor"], hp=value["hp"],
            max_hp=value["max_hp"], energy=value["energy"], block=value["block"], gold=value["gold"],
            player_strength=value["player_strength"], player_weak=value["player_weak"],
            player_vulnerable=value["player_vulnerable"], player_frail=value["player_frail"],
            boss_name=value["boss_name"], boss_confidence=float(value["boss_confidence"]),
            encounter_kind=value["encounter_kind"], encounter_confidence=float(value["encounter_confidence"]), map_nodes=map_nodes,
            end_turn_damage=value["end_turn_damage"], end_turn_damage_confidence=float(value["end_turn_damage_confidence"]),
            hand=tuple(value["hand"]), hand_complete=value["hand_complete"],
            hand_details=tuple(value["hand_details"]), enemies=enemies, description=value["description"],
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


def explicit_nonattack_intent(intent: str | None) -> bool:
    """Recognize the observed nonattack category, not an exact hidden move.

    A question mark or the title 'Unknown' alone supplies no such proof. These
    labels require the readable '(not attacking)' tooltip; they do not establish
    zero total enemy-turn danger from summons, buffs, or other enemies.
    """
    if not isinstance(intent, str):
        return False
    return ' '.join(intent.casefold().replace('\u2019', "'").split()) in {
        'unknown (not attacking)', 'unknown_not_attacking',
        "this enemy's intentions are unknown (not attacking).",
    }


def combat_action_readiness(state: StructuredGameState) -> ActionReadiness:
    """Require the minimum visually verified combat state before VEDA can act.

    Unreadable intent remains an evidence gap. A readable 'Unknown (not
    attacking)' tooltip is a known nonattack category with an unknown move.
    This verifies displayed facts only; checked planning still needs reviewed
    effects or a conservative bound for every possible outcome it relies on.
    """
    reasons: list[str] = []
    if state.screen_type != "COMBAT":
        reasons.append("screen is not confirmed as combat")
    if state.confidence < 0.85:
        reasons.append("vision confidence is below the action threshold")
    if state.hp is None or state.max_hp is None or state.energy is None or state.block is None:
        reasons.append("player HP, Block, or energy is missing")
    if state.player_vulnerable is None:
        reasons.append("player Vulnerable is unconfirmed")
    if state.end_turn_damage is None or state.end_turn_damage_confidence < 0.9:
        reasons.append("end-of-turn damage is unconfirmed")
    if not state.hand and state.hand_complete is not True:
        reasons.append("no confirmed hand was extracted")
    if not state.enemies:
        reasons.append("no enemy was extracted")
    for enemy in state.enemies:
        if enemy.hp is None or enemy.max_hp is None:
            reasons.append("an enemy HP value is missing")
            break
        if enemy.block is None:
            reasons.append("an enemy Block value is missing")
            break
        if enemy.intent is None or enemy.intent.strip().lower() in {"", "unknown", "unknown intent", "?"}:
            reasons.append("an enemy intent is unknown")
            break
        if explicit_nonattack_intent(enemy.intent) and (
                enemy.intent_hits != () or type(enemy.intent_total_damage) is not int
                or enemy.intent_total_damage != 0):
            reasons.append("a nonattacking tooltip requires confirmed empty hits and zero displayed attack damage")
            break
        if enemy.intent_total_damage is None or enemy.intent_damage_confidence < 0.9:
            reasons.append("an enemy's total intent damage is unconfirmed")
            break
        if state.player_vulnerable is not None and state.player_vulnerable > 0 and enemy.intent_total_damage > 0:
            if enemy.intent_hits is None:
                reasons.append("player Vulnerable requires per-hit enemy intent data")
                break
            if sum(enemy.intent_hits) != enemy.intent_total_damage:
                reasons.append("per-hit enemy intent data does not match its total")
                break
    return ActionReadiness(not reasons, tuple(reasons))


class VisionProvider(Protocol):
    def observe(self, image_path: Path) -> StructuredGameState: ...


class IncompleteVisionResponse(ValueError):
    """The model explicitly reported truncation; never complete its missing facts."""


class LocalOllamaVisionProvider:
    """Local-only multimodal provider served at localhost by Ollama.

    It sends screenshot bytes exclusively to the configured local endpoint.
    There is no cloud endpoint, API key, or controller operation here.
    """
    def __init__(
        self,
        model: str = "blaifa/InternVL3_5:4b",
        endpoint: str = "http://127.0.0.1:11434/api/chat",
        timeout_seconds: float = 30,
        request: Callable[[bytes], bytes] | None = None,
        trace: Any | None = None,
        context_tokens: int = 8192,
        max_output_tokens: int = 2048,
    ) -> None:
        if (type(context_tokens) is not int or not 4096 <= context_tokens <= 32768
                or type(max_output_tokens) is not int or not 256 <= max_output_tokens < context_tokens):
            raise ValueError("vision context and response budgets must be bounded integers")
        self.model, self.endpoint, self.timeout_seconds = model, endpoint, timeout_seconds
        self.context_tokens, self.max_output_tokens = context_tokens, max_output_tokens
        self._request_override = request
        self.trace = trace
        self.last_error: str | None = None
        self.last_raw_response: str | None = None
        self.last_latency_ms: float | None = None

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

    def observe(self, image_path: Path, *, frame_id: str | None = None) -> StructuredGameState:
        started = time.perf_counter()
        request_id = uuid4().hex
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
For every enemy, report its current Block, the readable intent text, the total damage that intent
will deal this turn, and its individual hit values. For a multi-hit intent such as 4x6, report
intent_hits [4, 4, 4, 4, 4, 4] and intent_total_damage 24. For a single 16-damage attack, report
intent_hits [16] and intent_total_damage 16. For a confirmed non-attacking intent, report an empty
intent_hits array and total 0. If the tooltip explicitly reads 'Unknown (not attacking)', preserve
that full category as intent (or unknown_not_attacking), with empty hits and total 0 for that
enemy's displayed attack only. Its exact move is still unknown; do not rename it Spawn or claim
zero enemy-turn danger. A bare question mark, obscured intent, or intent hidden by Runic Dome is
not proof of nonattack: use null for unread damage. Use null and confidence 0 when the total or
individual hits cannot be read or calculated from visible text. Sum only visible player end-of-turn
damage statuses (for example, three Burns = 6) into end_turn_damage; use 0 only when you can verify
there is none. On MAP, report each currently reachable node with a stable local node_id, its visible
kind, and confidence. Report a boss_name only when the name itself is readable; boss art is not proof.
On COMBAT, set encounter_kind to enemy, elite, or boss only when the screen proves it; otherwise use
null and confidence 0. Also report player_strength, player_weak, player_vulnerable, and player_frail from visible player
status icons; use null when a count is not legible. Read a value only if it is visibly legible. Do not
recommend or execute any action. Set hand_complete true only if the whole hand is visible (including an empty hand).
For each visible card, report hand_details with name, title_color, upgraded, and current_cost.
Green/teal titles mean upgraded; white means base. Unreadable color, upgrade or cost is null.
Displayed intent already includes visible damage modifiers: do not apply Vulnerable a second time."""
        def span(stage):
            return self.trace.span(stage, frame_id=frame_id, request_id=request_id) if self.trace else nullcontext()
        with span("image_preparation"):
            encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = json.dumps({
            "model": self.model,
            "messages": [{
                "role": "user", "content": prompt,
                "images": [encoded_image],
            }],
            # Qwen3-VL otherwise spends its response budget on hidden reasoning
            # and can return an empty visible JSON response. The same switch is
            # harmless for non-thinking local models.
            "format": OBSERVATION_SCHEMA, "stream": False, "think": False,
            # The schema/prompt can occupy ~4K tokens. Explicit capacity avoids
            # inheriting a model default that leaves almost no response room.
            "options": {"temperature": 0, "num_ctx": self.context_tokens,
                        "num_predict": self.max_output_tokens},
        }).encode("utf-8")
        try:
            with span("model_request_round_trip"):
                self.last_raw_response = self._request(payload).decode("utf-8")
            with span("parsing"):
                response = json.loads(self.last_raw_response)
                if response.get("done_reason") == "length":
                    raise IncompleteVisionResponse("model response exhausted its output budget")
                result = StructuredGameState.from_dict(
                    self._normalize_model_state(json.loads(response["message"]["content"])))
            self.last_error = None
            return result
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.last_error = type(error).__name__
            return self._fallback(
                f"Local vision unavailable or returned an invalid state ({self.last_error}); no state was guessed."
            )
        finally:
            self.last_latency_ms = round((time.perf_counter() - started) * 1000, 2)
