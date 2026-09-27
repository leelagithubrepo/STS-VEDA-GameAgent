"""Single-input choice planning from explicitly reviewed, source-bound UI facts.

No controller, capture, database or recognizer is used here. A caller must keep
one durable pending action, validate source bytes and obtain per-run authority.
Reviewed declarations are not independently recognized pixels or permission.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import math
import re
from uuid import uuid4

SCHEMA = "veda.choice-observation.v1"
PLAN_SCHEMA = "veda.choice-step.v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_BUTTONS = {"cross", "circle", "square", "triangle"}
_DIRECTIONS = {"up", "down", "left", "right"}
_SCREENS = {
    "selection": {"selection"}, "potion": {"potion_slots", "potion_menu", "potion_target"},
    "map": {"map"}, "reward": {"reward", "card_reward"}, "rest": {"rest"},
    "event": {"event"}, "shop": {"shop"}, "continue_run": {"title"},
}
_ALL_SCREENS = set().union(*_SCREENS.values()) | {"combat", "result"}
_CONTEXT = {"run_id", "floor_id", "combat_id", "turn_id"}


class ChoiceError(ValueError):
    pass


def _checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
            raise ChoiceError("malformed choice contract") from error
    return wrapped


def _require(condition, message):
    if not condition:
        raise ChoiceError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _integer(value):
    return type(value) is int and 0 <= value <= 1_000_000_000


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _json(value):
    try:
        raw = json.dumps(value, allow_nan=False)
        _require(len(raw.encode()) <= 256_000, "choice evidence exceeds its byte bound")
        return json.loads(raw)
    except (TypeError, ValueError, RecursionError) as error:
        raise ChoiceError("choice evidence must be bounded finite JSON") from error


def _context(value):
    _require(isinstance(value, dict) and set(value) == _CONTEXT, "exact run/floor/combat/turn context required")
    _require(_text(value["run_id"]) and _text(value["floor_id"]), "named run and floor required")
    _require(all(value[k] is None or _text(value[k]) for k in ("combat_id", "turn_id")), "invalid combat/turn identity")
    _require(value["combat_id"] is not None or value["turn_id"] is None,
             "turn identity requires a combat identity")


def _time(value):
    try:
        result = datetime.fromisoformat(value)
        _require(result.tzinfo is not None, "observation timestamp needs a timezone")
        return result
    except (TypeError, ValueError) as error:
        raise ChoiceError("invalid observation timestamp") from error


def _review(review, frame, kind):
    _require(isinstance(review, dict) and review.get("kind") == kind
             and _text(review.get("reviewer")) and review.get("complete") is True,
             "named complete review required")
    _require(review.get("frame_id") == frame["frame_id"]
             and review.get("image_sha256") == frame["image_sha256"], "review references different source")


def _proof(binding, observation, meaning, *, navigation=False):
    _require(isinstance(binding, dict), "reviewed button binding required")
    button = binding.get("button")
    _require(button in (_DIRECTIONS if navigation else _BUTTONS), "unreviewed or non-atomic button")
    proof = binding.get("evidence")
    _require(isinstance(proof, dict) and _text(proof.get("reviewer"))
             and proof.get("meaning") == meaning
             and proof.get("layout_id") == observation["ui"]["layout_id"], "button meaning/layout is unreviewed")
    if proof.get("kind") == "visible_hint":
        frame = observation["frame"]
        _require(proof.get("frame_id") == frame["frame_id"]
                 and proof.get("image_sha256") == frame["image_sha256"]
                 and _text(proof.get("hint_text")), "button hint references a different frame")
    elif proof.get("kind") == "reviewed_transition":
        _require(_text(proof.get("reference_id")) and _SHA.fullmatch(str(proof.get("before_sha256", "")))
                 and _SHA.fullmatch(str(proof.get("after_sha256", "")))
                 and proof["before_sha256"] != proof["after_sha256"], "reviewed transition needs distinct source identities")
    else:
        raise ChoiceError("button binding needs a visible hint or reviewed transition")
    return button


def _observation(value, now, age_limit):
    obs = _json(value)
    _require(isinstance(obs, dict) and obs.get("schema") == SCHEMA, "choice observation schema required")
    frame = obs.get("frame")
    _require(isinstance(frame, dict) and _text(frame.get("frame_id"))
             and _SHA.fullmatch(str(frame.get("image_sha256", ""))), "frame identity required")
    observed = _time(frame.get("observed_at"))
    if age_limit is not None:
        _require(isinstance(now, datetime) and now.tzinfo is not None, "aware assessment clock required")
        _require(type(age_limit) in (int, float) and math.isfinite(age_limit) and 0 < age_limit <= 30,
                 "freshness limit must be finite and at most 30 seconds")
        _require(0 <= (now - observed).total_seconds() <= age_limit, "choice source is stale or future dated")
    _review(obs.get("review"), frame, "reviewed_choice_ui")
    _context(obs.get("context"))
    _require(_SHA.fullmatch(str(obs.get("inventory_digest", ""))), "current inventory digest required")
    _require(isinstance(obs.get("resources"), dict) and len(obs["resources"]) <= 32
             and all(_text(k) and _integer(v) for k, v in obs["resources"].items()), "complete numeric resources required")
    _require(isinstance(obs.get("facts"), dict) and len(obs["facts"]) <= 64, "reviewed gameplay facts required")
    ui = obs.get("ui")
    _require(isinstance(ui, dict) and ui.get("screen") in _ALL_SCREENS
             and ui.get("phase") in {"choose", "confirm", "result"}, "unsupported or uncertain choice UI")
    _require(_text(ui.get("choice_id")) and _text(ui.get("layout_id")), "stable choice/layout identities required")
    options = ui.get("options")
    _require(isinstance(options, list) and len(options) <= 128, "bounded complete visible options required")
    for option in options:
        _require(isinstance(option, dict) and _text(option.get("id")) and _text(option.get("label"))
                 and type(option.get("enabled")) is bool, "each option needs stable identity, label and enabled state")
        _require(isinstance(option.get("costs"), dict) and len(option["costs"]) <= 32
                 and all(k in obs["resources"] and _integer(v) for k, v in option["costs"].items()),
                 "each option needs explicit known costs in observed resources")
    ids = [o["id"] for o in options]
    _require(len(ids) == len(set(ids)) and ui.get("order") == ids, "duplicate or incomplete option order")
    _require(ui.get("focused_id") is None or ui["focused_id"] in ids, "focus is outside visible options")
    _require(ui.get("selection_mode") in {"immediate", "toggle"}, "selection behavior is unreviewed")
    _require(_integer(ui.get("required_count")) and ui["required_count"] <= len(ids), "exact selection quota required")
    _require(ui["phase"] == "result" or ui["required_count"] >= 1,
             "active choice screens need a nonzero selection quota")
    for key in ("selected_ids", "pending_ids"):
        values = ui.get(key)
        _require(isinstance(values, list) and len(values) == len(set(values))
                 and all(v in ids for v in values), "invalid selected/pending IDs")
    _require(len(ui["selected_ids"]) <= ui["required_count"], "selection exceeds quota")
    _require(isinstance(ui.get("navigation"), list) and len(ui["navigation"]) <= 512,
             "reviewed navigation edges required")
    edge_keys = set()
    for edge in ui["navigation"]:
        _require(isinstance(edge, dict) and edge.get("from") in ids and edge.get("to") in ids
                 and edge["from"] != edge["to"], "invalid navigation edge")
        button = _proof(edge, obs, f"focus:{edge['from']}->{edge['to']}", navigation=True)
        key = (edge["from"], button)
        _require(key not in edge_keys, "ambiguous navigation edge")
        edge_keys.add(key)
    return obs


def _postconditions(choice, before):
    post = choice.get("postconditions")
    _require(isinstance(post, dict) and set(post) == {
        "screen", "phase", "context", "resources", "inventory_digest", "facts", "allow_changed_facts"},
        "explicit outcome conditions and allowed changes required")
    _require(post["screen"] in _ALL_SCREENS and post["phase"] in {"choose", "confirm", "result"}, "unknown outcome UI")
    _context(post["context"])
    _require(post["context"]["run_id"] == before["context"]["run_id"], "choices cannot start or switch runs")
    _require(isinstance(post["resources"], dict) and set(post["resources"]) == set(before["resources"]),
             "outcome must constrain every observed resource")
    for constraint in post["resources"].values():
        _require(_integer(constraint) or isinstance(constraint, dict) and set(constraint) == {"min", "max"}
                 and _integer(constraint["min"]) and _integer(constraint["max"])
                 and constraint["min"] <= constraint["max"], "invalid resource constraint")
    _require(post["inventory_digest"] == "unchanged" or _SHA.fullmatch(str(post["inventory_digest"])),
             "inventory outcome must be unchanged or an exact reviewed digest")
    allowed = post["allow_changed_facts"]
    _require(isinstance(post["facts"], dict) and isinstance(allowed, list) and len(allowed) <= 64
             and all(_text(k) for k in allowed) and len(allowed) == len(set(allowed))
             and not set(allowed).intersection(post["facts"]), "ambiguous outcome fact constraints")
    return post


def _choice(value, obs):
    choice = _json(value)
    _require(isinstance(choice, dict) and _text(choice.get("choice_id"))
             and choice["choice_id"] == obs["ui"]["choice_id"], "planned choice belongs to a different UI")
    _require(choice.get("kind") in _SCREENS and obs["ui"]["screen"] in _SCREENS[choice["kind"]], "choice kind/screen mismatch")
    _review(choice.get("review"), obs["frame"], "reviewed_choice")
    wanted = choice.get("option_ids")
    _require(isinstance(wanted, list) and wanted and all(_text(k) for k in wanted)
             and len(wanted) == len(set(wanted)) and len(wanted) == obs["ui"]["required_count"],
             "planned selection must equal exact visible quota")
    options = {o["id"]: o for o in obs["ui"]["options"]}
    _require(all(k in options and options[k]["enabled"] is True for k in wanted), "chosen option absent or disabled")
    _require(obs["ui"]["selected_ids"] == wanted[:len(obs["ui"]["selected_ids"])],
             "unexpected selected option/order; do not toggle it blindly")
    if choice["kind"] == "continue_run":
        _require(len(wanted) == 1 and options[wanted[0]]["label"] == "Continue"
                 and options[wanted[0]].get("role") == "continue_run", "only the visible current-run Continue is supported")
    post = _postconditions(choice, obs)
    for key, available in obs["resources"].items():
        cost = sum(options[k]["costs"].get(key, 0) for k in wanted)
        _require(cost <= available, "choice exceeds observed " + key)
        if cost:
            _require(post["resources"][key] == available - cost, "paid resource needs its exact reviewed debit")
    return choice


def _first_edge(ui, target):
    queue, seen = deque([(ui["focused_id"], None)]), {ui["focused_id"]}
    while queue:
        current, first = queue.popleft()
        for edge in ui["navigation"]:
            if edge["from"] == current and edge["to"] not in seen:
                first_step = first or edge
                if edge["to"] == target:
                    return first_step
                seen.add(edge["to"])
                queue.append((edge["to"], first_step))
    raise ChoiceError("no reviewed path to planned option; no wrap or mapping is assumed")


@_checked
def plan_choice_step(observation, choice, *, now=None, max_age_seconds=5, action_id=None):
    """Plan exactly one tap; this function never sends it or grants authority."""
    # None is an internal verification-only sentinel, never a public freshness
    # bypass for planning or the immediate before-send recheck.
    _require(type(max_age_seconds) in (int, float) and math.isfinite(max_age_seconds)
             and 0 < max_age_seconds <= 30, "freshness limit must be finite and at most 30 seconds")
    now = now or datetime.now(timezone.utc)
    obs = _observation(observation, now, max_age_seconds)
    choice = _choice(choice, obs)
    ui, wanted = obs["ui"], choice["option_ids"]
    _require(ui["phase"] != "result", "result screens need a newly reviewed choice")
    expectation = {}
    if ui["phase"] == "confirm":
        _require(ui["selected_ids"] == wanted and ui["pending_ids"] == wanted,
                 "confirmation does not name exactly the planned selection")
        button = _proof(ui.get("confirm"), obs, "confirm:" + choice["choice_id"])
        kind = "commit"
    else:
        _require(not ui["pending_ids"], "unexpected pending confirmation")
        remaining = [k for k in wanted if k not in ui["selected_ids"]]
        _require(remaining, "selected quota reached without a reviewed confirmation UI")
        target = remaining[0]
        option = next(o for o in ui["options"] if o["id"] == target)
        shortcut = option.get("shortcut")
        if shortcut is not None:
            _require(ui["selection_mode"] == "immediate" and len(wanted) == 1, "shortcuts require one immediate choice")
            button = _proof(shortcut, obs, "activate:" + target)
            kind = "commit"
        elif ui["focused_id"] != target:
            _require(ui["focused_id"] is not None, "choice focus is unread")
            edge = _first_edge(ui, target)
            button, kind, expectation = edge["button"], "focus", {"focused_id": edge["to"]}
        else:
            button = _proof(option.get("activate"), obs, "activate:" + target)
            if ui["selection_mode"] == "immediate":
                _require(len(wanted) == 1 and not ui["selected_ids"], "immediate choices cannot imply multiple selections")
                kind = "commit"
            else:
                kind, expectation = "select", {"selected_ids": ui["selected_ids"] + [target]}
    action_id = action_id or uuid4().hex
    _require(_text(action_id), "action identity required")
    proposal = {"schema": PLAN_SCHEMA, "action_id": action_id, "before_digest": _digest(obs),
        "before_frame": deepcopy(obs["frame"]), "choice": choice, "step_kind": kind,
        "command": {"action": "tap", "buttons": [button], "request_id": action_id},
        "expectation": expectation, "max_age_seconds": max_age_seconds,
        "runtime_authorized": False, "controller_authorized": False}
    proposal["proposal_digest"] = _digest(proposal)
    return proposal


@_checked
def validate_choice_proposal(proposal, before, *, now=None):
    """Recheck unchanged current source/choice immediately before a caller sends."""
    proposal = _json(proposal)
    _require(isinstance(proposal, dict), "choice proposal required")
    seal = proposal.pop("proposal_digest", None)
    _require(seal == _digest(proposal), "choice proposal was modified")
    expected = plan_choice_step(before, proposal.get("choice"), now=now,
        max_age_seconds=proposal.get("max_age_seconds"), action_id=proposal.get("action_id"))
    _require(expected["proposal_digest"] == seal, "proposal does not match current reviewed source")
    return expected


def _stable(obs):
    return {k: obs[k] for k in ("context", "resources", "inventory_digest", "facts")}


@_checked
def verify_choice_step(proposal, before, after, *, now=None):
    """Verify observed semantics, never just a bridge ack or a different image.

    Commits require an explicit reviewer outcome tied to both source frames.
    The caller retains unresolved actions on any exception and never retries.
    """
    # A legitimately completed step may outlive its before-frame freshness.
    before = _observation(before, None, None)
    proposal = validate_choice_proposal(proposal, before, now=_time(before["frame"]["observed_at"]))
    after = _observation(after, now or datetime.now(timezone.utc), proposal["max_age_seconds"])
    _require(after["frame"]["frame_id"] != before["frame"]["frame_id"]
             and after["frame"]["image_sha256"] != before["frame"]["image_sha256"]
             and _time(after["frame"]["observed_at"]) > _time(before["frame"]["observed_at"]),
             "verification needs a later distinct source")
    kind, ui = proposal["step_kind"], deepcopy(before["ui"])
    if kind in {"focus", "select"}:
        _require(_stable(after) == _stable(before), "game context/resources/inventory changed during choice navigation")
        if kind == "focus":
            ui["focused_id"] = proposal["expectation"]["focused_id"]
        else:
            ui["selected_ids"] = proposal["expectation"]["selected_ids"]
            if len(ui["selected_ids"]) == ui["required_count"]:
                ui["phase"], ui["pending_ids"] = "confirm", proposal["choice"]["option_ids"]
        # Current visible-hint proofs naturally rebind to the after-frame.
        def semantics(value):
            if isinstance(value, dict):
                return {k: semantics(v) for k, v in value.items() if k not in {"evidence", "confirm"}}
            if isinstance(value, list):
                return [semantics(v) for v in value]
            return value
        _require(semantics(after["ui"]) == semantics(ui), "choice focus/selection/UI transition differs")
    else:
        post = proposal["choice"]["postconditions"]
        _require(after["ui"]["screen"] == post["screen"] and after["ui"]["phase"] == post["phase"]
                 and after["context"] == post["context"], "unexpected committed choice UI/context")
        _require(set(after["resources"]) == set(post["resources"]), "resource fields changed")
        for key, expected in post["resources"].items():
            actual = after["resources"][key]
            _require(actual == expected if _integer(expected) else expected["min"] <= actual <= expected["max"],
                     "resource outcome differs: " + key)
        inventory = before["inventory_digest"] if post["inventory_digest"] == "unchanged" else post["inventory_digest"]
        _require(after["inventory_digest"] == inventory, "unexpected inventory outcome")
        allowed = set(post["allow_changed_facts"])
        for key in set(before["facts"]) | set(after["facts"]) | set(post["facts"]):
            if key in post["facts"]:
                _require(key in after["facts"] and after["facts"][key] == post["facts"][key], "outcome fact differs: " + key)
            elif key not in allowed:
                _require(key in before["facts"] and key in after["facts"] and after["facts"][key] == before["facts"][key],
                         "undeclared fact change: " + key)
        outcome = after["review"].get("outcome")
        _require(isinstance(outcome, dict) and outcome.get("action_id") == proposal["action_id"]
                 and outcome.get("before_frame_id") == before["frame"]["frame_id"]
                 and outcome.get("before_sha256") == before["frame"]["image_sha256"]
                 and outcome.get("choice_id") == proposal["choice"]["choice_id"]
                 and outcome.get("option_ids") == proposal["choice"]["option_ids"]
                 and _text(outcome.get("observed_result")), "source-bound named outcome review required")
        def option_facts(ui):
            return [{k: o.get(k) for k in ("id", "label", "enabled", "costs", "role")}
                    for o in ui["options"]]
        _require(_stable(after) != _stable(before) or
                 any(after["ui"][k] != before["ui"][k] for k in ("screen", "phase")) or
                 option_facts(after["ui"]) != option_facts(before["ui"]),
                 "no observed semantic choice result")
    return {"schema": "veda.choice-verification.v1", "action_id": proposal["action_id"],
        "step_verified": True, "choice_complete": kind == "commit", "before_frame": before["frame"],
        "after_frame": after["frame"], "after_digest": _digest(after),
        "evidence_kind": "explicit_reviewed_evidence_not_automated_recognition",
        "runtime_authorized": False, "controller_authorized": False}
