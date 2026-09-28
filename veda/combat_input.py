"""Pure combat UI planning and outcome checks shared by both play paths.

No capture, model, calibration, controller or database calls occur here. The
caller must establish current inspected facts and its own execution authority.
"""
from __future__ import annotations
import copy
import json
import hashlib

FOCUS_DOMAINS = frozenset({'hand', 'player_status', 'relic', 'potion', 'enemy', 'none', 'unknown'})
TOOLTIP_KINDS = frozenset({'none', 'card_keyword', 'player_status', 'relic', 'potion', 'enemy', 'unknown'})
FOCUS_FIELDS = frozenset({'focus_domain', 'tooltip_kind', 'focused_subject_id', 'focus_evidence_note'})
FOCUS_CONTROL_PROFILE = 'ps5-default-cross-confirm-v1'


class RuntimeStop(RuntimeError):
    pass


def validate_combat_focus(ui, hand_ids, *, require_explicit=False):
    """Validate the visible focus domain, not whether any help box is open.

    Legacy plain hand/target observations remain readable in strict replay.
    A legacy generic tooltip is never inferred to be a focused hand card.
    """
    if not isinstance(ui, dict):
        raise RuntimeStop('combat UI must be an object')
    present = FOCUS_FIELDS & set(ui)
    if not present:
        if require_explicit:
            raise RuntimeStop('review focus_domain, tooltip_kind, focused_subject_id and focus_evidence_note from the current image')
        if ui.get('phase') == 'tooltip':
            return {'domain': 'unknown', 'tooltip_kind': 'unknown', 'subject_id': None,
                    'evidence_note': 'Legacy tooltip domain was not recorded.', 'focused_card_id': None,
                    'selected_card_id': ui.get('selected_card_id')}
        domain = ('enemy' if ui.get('phase') == 'targeting' else
                  'hand' if ui.get('focused_card_id') or ui.get('selected_card_id') else 'none')
        return {'domain': domain, 'tooltip_kind': 'none', 'subject_id': None,
                'evidence_note': 'Legacy plain combat focus declaration.',
                'focused_card_id': ui.get('focused_card_id'), 'selected_card_id': ui.get('selected_card_id')}
    if present != FOCUS_FIELDS:
        raise RuntimeStop('all four explicit combat focus fields are required together')
    domain, tooltip, subject, note = (ui['focus_domain'], ui['tooltip_kind'],
                                      ui['focused_subject_id'], ui['focus_evidence_note'])
    if (domain not in FOCUS_DOMAINS or tooltip not in TOOLTIP_KINDS
            or not isinstance(note, str) or not note.strip() or len(note.encode()) > 2048
            or (subject is not None and (not isinstance(subject, str) or not subject.strip() or len(subject.encode()) > 256))):
        raise RuntimeStop('combat focus needs a supported domain/tooltip and a bounded visible evidence note')
    phase = ui.get('phase'); focused = ui.get('focused_card_id'); selected = ui.get('selected_card_id')
    if focused is not None and focused not in hand_ids or selected is not None and selected not in hand_ids:
        raise RuntimeStop('focused/selected card must identify a current hand card')
    if phase == 'targeting':
        valid = domain == 'enemy' and tooltip == 'none' and selected is not None and ui.get('focused_target_id') is not None
    elif domain == 'hand':
        valid = (phase in {'hand', 'card_selected'} and tooltip in {'none', 'card_keyword'}
                 and subject is None and (focused is not None or selected is not None)
                 and ui.get('focused_target_id') is None)
    elif domain == 'none':
        valid = (phase == 'hand' and tooltip == 'none' and subject is None
                 and focused is None and selected is None and ui.get('focused_target_id') is None)
    else:
        valid = (phase in {'inspect', 'tooltip'} and focused is None and selected is None
                 and ui.get('focused_target_id') is None
                 and (tooltip == domain or tooltip == 'none' or domain == 'unknown' and tooltip == 'unknown')
                 and (subject is not None or domain == 'unknown'))
    if not valid:
        raise RuntimeStop('combat phase, focus domain, tooltip and selected subject contradict each other')
    return {'domain': domain, 'tooltip_kind': tooltip, 'subject_id': subject, 'evidence_note': note,
            'focused_card_id': focused, 'selected_card_id': selected}


def focus_transition(before_ui, after_ui, hand_ids):
    """Record actual navigation; away-domain observations never claim hand return."""
    before = validate_combat_focus(before_ui, hand_ids)
    after = validate_combat_focus(after_ui, hand_ids, require_explicit=True)
    if after_ui.get('phase') in {'card_selected', 'targeting'} or after['selected_card_id'] is not None:
        raise RuntimeStop('focus navigation cannot select a card or enter targeting')
    keys = ('domain', 'tooltip_kind', 'subject_id', 'focused_card_id', 'selected_card_id')
    prior, actual = ({key: focus[key] for key in keys} for focus in (before, after))
    return {'before': prior, 'after': actual,
            'effect': ('focus_observed' if not FOCUS_FIELDS <= set(before_ui)
                       else 'unchanged' if prior == actual else 'focus_changed'),
            'returned_to_hand': after['domain'] == 'hand' and after['focused_card_id'] is not None}


def record_focus_recovery_attempt(progress, context, focus, action_id, direction='down'):
    """Bound exploratory Down taps across fresh captures and session restarts."""
    scope = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    progress = copy.deepcopy(progress) if isinstance(progress, dict) and progress.get('scope') == scope else {
        'schema': 'veda.combat-focus-progress.v1', 'scope': scope, 'attempts': []}
    attempts = progress.get('attempts')
    if not isinstance(attempts, list) or len(attempts) > 8:
        raise RuntimeStop('invalid focus recovery progress')
    identity = {key: focus[key] for key in ('domain', 'subject_id')}
    if any(row.get('action_id') == action_id for row in attempts):
        return progress
    matching = [row for row in attempts if row.get('focus') == identity]
    if direction not in {'down', 'circle'} or len(attempts) >= 8 or any(row.get('button') == direction for row in matching):
        raise RuntimeStop('bounded focus exploration exhausted for this direction; inspect the actual navigation result')
    if direction == 'circle' and not any(row.get('button') == 'down'
            and row.get('result', {}).get('effect') == 'unchanged' for row in matching):
        raise RuntimeStop('Circle exploration needs a verified unchanged Down result from this exact focus')
    attempts.append({'action_id': action_id, 'focus': identity, 'button': direction})
    return progress


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
        if ui.get("screen_type") != "combat" or ui.get("phase") not in {"hand", "card_selected", "targeting", "tooltip", "inspect"}:
            raise RuntimeStop("UI phase is unconfirmed or is animating")
        state = reading.context["state"]
        focus = validate_combat_focus(ui, [card['id'] for card in state['hand']],
                                      require_explicit=getattr(self, 'decision_policy', 'strict') == 'learning')
        observation = {"screen_type": "combat", "focus_domain": focus['domain'], 'tooltip_kind': focus['tooltip_kind'],
                       "selected_item": None, 'recovery_direction': ui.get('recovery_direction', 'down')}
        if ui['phase'] in {'inspect', 'tooltip'} or focus['domain'] == 'none' and action['kind'] == 'card':
            if ui.get('control_profile') != FOCUS_CONTROL_PROFILE or not FOCUS_FIELDS <= set(ui):
                raise RuntimeStop('non-hand focus recovery needs explicit focus evidence and the selected default PS5 control profile')
            step = self.machine.plan_focus_recovery(observation)
            return step, {'kind': 'focus_probe', 'before_focus': focus, 'direction': step['buttons'][0],
                          'goal': 'hand', 'control_profile': FOCUS_CONTROL_PROFILE}
        if action["kind"] == "end_turn":
            observation["selected_item"] = ui.get("focused_card_id") or ui.get("selected_card_id")
            step = self.machine.plan_end_turn(observation)
            return step, {"kind": "end_turn"}
        cards = state["hand"]
        ids = [c["id"] for c in cards]
        card = next((c for c in cards if c["id"] == action["card_id"]), None)
        if card is None:
            raise RuntimeStop("planned card disappeared")
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
        validate_combat_focus(ui, [card['id'] for card in after.context['state']['hand']])
        unchanged = _state_key(before) == _state_key(after)
        if (after.floor_id, after.turn_id) != (before.floor_id, before.turn_id) and kind not in {"advance", "end_turn"}:
            raise RuntimeStop("floor or turn changed during navigation")
        if kind == 'focus_probe':
            if not unchanged:
                raise RuntimeStop('focus navigation changed observed gameplay state')
            focus_transition(before.ui, after.ui, [card['id'] for card in after.context['state']['hand']])
            return False
        if kind in {"card_focus", "target_focus", "clear"}:
            field = "focused_card_id" if kind == "card_focus" else "focused_target_id"
            matched = ui.get(field) == expected.get("expected")
            if kind == "clear":
                focus = validate_combat_focus(ui, [card['id'] for card in after.context['state']['hand']], require_explicit=True)
                matched = (ui != before.ui and focus['domain'] in {'hand', 'none'} and ui.get("phase") == "hand"
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
