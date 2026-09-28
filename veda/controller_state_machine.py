"""Fail-closed controller sequencing for Slay the Spire card turns.

The PS5 UI commonly treats the first Cross as card selection and the next
Cross as play/target confirmation.  This module keeps that distinction
explicit so callers must re-observe between inputs.  It plans inputs only; it
never talks to the controller bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Phase = Literal["idle", "card_selected", "targeting", "tooltip", "end_turn_ready"]


@dataclass
class ControllerStateMachine:
    phase: Phase = "idle"
    card_name: str | None = None

    def reset(self) -> None:
        self.phase = "idle"
        self.card_name = None

    def plan_card_step(self, observation: dict[str, Any], *, card_name: str, focus_button: str | None = None) -> dict[str, Any]:
        """Return one atomic input, requiring a fresh observation each time."""
        if observation.get("screen_type") != "combat":
            raise ValueError("card input requires a confirmed combat screen")
        if observation.get("tooltip") or observation.get('focus_domain', 'hand') not in {'hand', 'none', 'enemy'}:
            raise ValueError('review the actual focus domain; a tooltip is not a request to press Up')
        selected = observation.get("selected_item")
        if selected != card_name:
            if not focus_button:
                raise ValueError("card is not focused; supply one verified navigation button")
            self.phase = "idle"
            return {"buttons": [focus_button], "reason": f"focus {card_name}"}
        if observation.get("target_prompt"):
            self.phase = "targeting"
            return {"buttons": ["cross"], "reason": f"confirm target for {card_name}"}
        if self.phase == "card_selected" and self.card_name == card_name:
            self.phase = "targeting"
            return {"buttons": ["cross"], "reason": f"play selected {card_name}"}
        self.phase = "card_selected"
        self.card_name = card_name
        return {"buttons": ["cross"], "reason": f"select {card_name}; re-observe before play"}

    def plan_end_turn(self, observation: dict[str, Any]) -> dict[str, Any]:
        """End Turn directly from reviewed hand focus, including keyword help."""
        if observation.get("screen_type") != "combat":
            raise ValueError("end turn requires a confirmed combat screen")
        if observation.get('tooltip') or observation.get('focus_domain', 'hand') not in {'hand', 'none'}:
            raise ValueError('End Turn requires a reviewed hand focus domain')
        self.phase = "end_turn_ready"
        return {"buttons": ["triangle"], "reason": "commit verified End Turn"}

    def plan_focus_recovery(self, observation: dict[str, Any]) -> dict[str, Any]:
        """One exploratory default-profile direction; its result is not assumed."""
        if (observation.get('screen_type') != 'combat'
                or observation.get('focus_domain') not in {'player_status', 'relic', 'potion', 'enemy', 'none', 'unknown'}
                or observation.get('recovery_direction', 'down') not in {'down', 'circle'}):
            raise ValueError('focus recovery requires an explicit non-hand combat focus')
        self.reset()
        direction = observation.get('recovery_direction', 'down')
        return {'buttons': [direction], 'reason': f'explore one {direction} toward the hand; review the actual focus after this tap'}
