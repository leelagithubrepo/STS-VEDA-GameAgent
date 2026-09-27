"""Pure combat UI planning and outcome checks shared by both play paths.

No capture, model, calibration, controller or database calls occur here. The
caller must establish current inspected facts and its own execution authority.
"""
from __future__ import annotations
import copy
import json


class RuntimeStop(RuntimeError):
    pass


def _state_key(reading):
    """Semantic state equality for navigation, excluding recording metadata."""
    context = reading.context
    state = copy.deepcopy(context.get("state", {}))
    state.pop("observed_at", None)
    inventory = context.get("inventory", {})
    properties = {key: value.get("property") for key, value in inventory.get("properties", {}).items()}
    semantic = {
        "state": state,
        "inventory": {"current": inventory.get("current"),
                      "coverage": inventory.get("coverage"), "properties": properties},
        "encounter_type": context.get("encounter_type"),
        "boss_manifest": context.get("boss_manifest"),
        "rule_version": context.get("rules", {}).get("version"),
    }
    return json.dumps(semantic, sort_keys=True)



class CombatInputAdapter:
    """Requires a caller-owned ControllerStateMachine in ``machine``."""

    def _input(self, reading, action):
        ui = reading.ui
        if ui.get("screen_type") != "combat" or ui.get("phase") not in {"hand", "card_selected", "targeting", "tooltip"}:
            raise RuntimeStop("UI phase is unconfirmed or is animating")
        state = reading.context["state"]
        observation = {"screen_type": "combat", "tooltip": ui["phase"] == "tooltip",
                       "selected_item": None}
        if action["kind"] == "end_turn":
            observation["selected_item"] = ui.get("focused_card_id") or ui.get("selected_card_id")
            step = self.machine.plan_end_turn(observation)
            return step, {"kind": "clear" if step["buttons"] == ["up"] else "end_turn"}
        cards = state["hand"]
        ids = [c["id"] for c in cards]
        card = next((c for c in cards if c["id"] == action["card_id"]), None)
        if card is None:
            raise RuntimeStop("planned card disappeared")
        if ui["phase"] == "tooltip":
            return self.machine.plan_card_step(observation, card_name=card["name"]), {"kind": "clear"}
        if ui["phase"] in {"targeting", "card_selected"}:
            if ui.get("selected_card_id") != card["id"]:
                raise RuntimeStop("a different card is selected")
            if ui["phase"] == "targeting" and action.get("target") != ui.get("focused_target_id"):
                order = ui.get("target_order")
                alive = [e.get("id", e["name"]) for e in state["enemies"] if e["hp"] > 0]
                if (not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(alive)
                        or ui.get("focused_target_id") not in order or action.get("target") not in order):
                    raise RuntimeStop("target navigation is unverified")
                index = order.index(ui["focused_target_id"])
                delta = 1 if order.index(action["target"]) > index else -1
                return {"buttons": ["right" if delta > 0 else "left"]}, {
                    "kind": "target_focus", "expected": order[index + delta]}
            observation.update(selected_item=card["name"], target_prompt=ui["phase"] == "targeting")
            self.machine.phase, self.machine.card_name = "card_selected", card["name"]
            return self.machine.plan_card_step(observation, card_name=card["name"]), {"kind": "advance", "card": card}
        focused = ui.get("focused_card_id")
        if focused not in ids or ui.get("hand_order") != ids:
            raise RuntimeStop("hand focus/order is unread; cannot guess navigation")
        if focused != card["id"]:
            index = ids.index(focused)
            delta = 1 if ids.index(card["id"]) > index else -1
            observation["selected_item"] = cards[index]["name"]
            # Stable IDs distinguish two copies with the same displayed name.
            observation["selected_item"] = focused
            step = self.machine.plan_card_step(observation, card_name=card["id"],
                                               focus_button="right" if delta > 0 else "left")
            return step, {"kind": "card_focus", "expected": ids[index + delta]}
        observation["selected_item"] = card["name"]
        self.machine.reset()
        return self.machine.plan_card_step(observation, card_name=card["name"]), {"kind": "advance", "card": card}

    def _verify(self, before, after, action, expected, checked):
        kind, ui = expected["kind"], after.ui
        unchanged = _state_key(before) == _state_key(after)
        if (after.floor_id, after.turn_id) != (before.floor_id, before.turn_id) and kind not in {"advance", "end_turn"}:
            raise RuntimeStop("floor or turn changed during navigation")
        if kind in {"card_focus", "target_focus", "clear"}:
            field = "focused_card_id" if kind == "card_focus" else "focused_target_id"
            matched = ui.get(field) == expected.get("expected")
            if kind == "clear":
                matched = (ui != before.ui and ui.get("phase") == "hand"
                           and ui.get("selected_card_id") is None
                           and (action["kind"] != "end_turn" or ui.get("focused_card_id") is None))
            if not unchanged or not matched:
                raise RuntimeStop("navigation verification mismatch")
            return False
        if kind == "end_turn":
            if (after.turn_id == before.turn_id or after.floor_id != before.floor_id
                    or after.ui.get("screen_type") != "combat" or after.ui.get("phase") != "hand"
                    or not after.context.get("fresh")
                    or after.context.get("state", {}).get("hand_complete") is not True or unchanged):
                raise RuntimeStop("End Turn transition not verified")
            return True
        if (after.floor_id, after.turn_id) != (before.floor_id, before.turn_id):
            raise RuntimeStop("floor or turn changed during card input")
        card = expected["card"]
        if unchanged and ui.get("phase") in {"card_selected", "targeting"} and ui.get("selected_card_id") == card["id"]:
            if ui == before.ui:
                raise RuntimeStop("selection input produced no verified transition")
            return False
        state = after.context.get("state", {})
        if not after.context.get("fresh") or state.get("hand_complete") is not True:
            raise RuntimeStop("card resolution lacks a fresh complete hand")
        if any(c.get("id") == card["id"] for c in state.get("hand", [])):
            raise RuntimeStop("card play not verified; planned card remains in hand")
        if state.get("energy") != checked["steps"][0].get("energy_after"):
            raise RuntimeStop("post-action energy differs from checked result")
        hp_loss = checked["steps"][0].get("reviewed_effect", {}).get("hp_loss", 0)
        if state.get("hp") != before.context["state"]["hp"] - hp_loss:
            raise RuntimeStop("post-action HP differs from checked result")
        forecast = checked.get("forecast")
        if forecast:
            if state.get("block") != forecast.get("block", forecast.get("player_block")):
                raise RuntimeStop("post-action Block differs from checked result")
            for expected_enemy in forecast.get("enemies", []):
                current = next((e for e in state.get("enemies", []) if e.get("id") == expected_enemy.get("id")), None)
                if current is None or any(current.get(k) != expected_enemy.get(k) for k in ("hp", "block")):
                    raise RuntimeStop("post-action enemy state differs from checked result")
        return True  # Always invalidate plan after any actual card effect.
