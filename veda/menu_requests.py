"""Build source-bound menu requests from a compact, separately validated draft.

Draft validation has no capture, clock or controller dependency. Its internal
synthetic source exists only to exercise the ordinary contract validators and
never leaves this module. A runnable prepare request is created only after the
caller explicitly declares review of an exact fresh capture and its draft facts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .choice_execution import SCHEMA as OBSERVATION_SCHEMA, _checked, _require, _same_json, plan_choice_step
from .menu_controls import bind_reviewed_menu_controls
from .play_requests import reviewed_capture_source
from .reviewed_play import MAX_BYTES, inventory_digest

SCHEMA = "veda.menu-draft.v1"
MAX_DRAFT_BYTES = 256_000
_OFFLINE_TIME = datetime(2000, 1, 1, tzinfo=timezone.utc)
_KINDS = {"card", "relic", "potion"}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key in menu draft")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("menu draft must contain finite JSON")


def _json(value):
    if isinstance(value, (str, bytes)):
        raw = value.encode() if isinstance(value, str) else value
    else:
        try:
            raw = json.dumps(value, allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError) as error:
            raise ValueError("menu draft must be finite JSON") from error
    _require(len(raw) <= MAX_DRAFT_BYTES, "menu draft exceeds byte bound")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid menu draft JSON") from error


def read_menu_draft(path):
    """Read bounded, duplicate-free, finite JSON without interpreting a source."""
    with Path(path).open("rb") as stream:
        return _json(stream.read(MAX_DRAFT_BYTES + 1))


def _text(value, limit=4096):
    return isinstance(value, str) and bool(value.strip()) and len(value.encode()) <= limit and "\0" not in value


def _inventory(value, policy='strict'):
    _require(policy in {'strict', 'learning'}, 'decision_policy must be strict or learning')
    _require(isinstance(value, dict) and isinstance(value.get("current"), dict)
             and isinstance(value.get("coverage"), dict)
             and set(value["coverage"]) == _KINDS
             and all(value["coverage"][k] in {"unknown", "partial", "complete"} for k in _KINDS)
             and all(isinstance(value["current"].get(k), list)
                     and all(_text(name, 256) for name in value["current"][k]) for k in _KINDS)
             and isinstance(value.get("properties", {}), dict), "explicit reviewed inventory and coverage required")
    _require(policy == 'learning' or all(value["coverage"][k] == "complete" for k in ("relic", "potion")),
             "menu preparation requires complete reviewed relic and potion coverage")
    inventory_digest(value)  # Exercise the same property representation as the adapter.
    return deepcopy(value)


def _postconditions(value, context, policy='strict'):
    _require(isinstance(value, dict), "explicit menu outcome required")
    if "alternatives" in value:
        _require(set(value) == {"alternatives"} and isinstance(value["alternatives"], list),
                 "complete named alternatives required")
        return {"alternatives": [
            _branch(branch, context, policy) for branch in value["alternatives"]]}
    _require(set(value) in ({"screen", "phase", "resources", "inventory", "facts", "allow_changed_facts"},
                            {"screen", "phase", "context", "resources", "inventory", "facts", "allow_changed_facts"}),
             "compact outcome needs screen, phase, resources, inventory, facts and allowed fact changes")
    post = deepcopy(value)
    inventory = post.pop("inventory")
    post["inventory_digest"] = "unchanged" if inventory == "unchanged" else inventory_digest(_inventory(inventory, policy))
    post.setdefault("context", deepcopy(context))
    return post


def _branch(branch, context, policy='strict'):
    _require(isinstance(branch, dict) and set(branch) == {"id", "postconditions"},
             "each menu outcome alternative needs a name and complete conditions")
    _require("alternatives" not in branch["postconditions"], "nested menu alternatives are unsupported")
    return {"id": branch["id"], "postconditions": _postconditions(branch["postconditions"], context, policy)}


def _ui(value):
    _require(isinstance(value, dict), "compact reviewed menu UI required")
    allowed = {"menu_family", "choice_id", "layout_id", "phase", "focused_id", "options", "grid",
               "selected_ids", "pending_ids", "upgrade_preview", "confirm_hint", "screen", "order",
               "control_layout", "selection_mode", "required_count", "navigation", "selection_purpose",
               "map_siblings", "map_inspection", "shop_positions", "shop_navigation"}
    _require(not set(value) - allowed, "source fields and control proofs do not belong in a menu draft")
    ui = deepcopy(value)
    family = ui.get("menu_family")
    # The game labels the post-combat reward screen "Spoils!". Vision and
    # hand-authored snapshots may surface that visible title instead of the
    # canonical internal family; normalize it before menu validation so it
    # reaches the deterministic loot helper.
    if isinstance(family, str) and family.casefold().strip().rstrip('!') in {'spoils', 'loot', 'rewards'}:
        family = "loot_rewards"
        ui["menu_family"] = family
    _require(family in {"event_options", "event_leave", "treasure_options", "card_upgrade", "combat_card_selection", "map_nodes", "map_inspect", "loot_rewards", "loot_cards", "shop_entry", "shop_stock", "shop_exit", "shop_remove", "campfire_options", "campfire_exit"},
             "unsupported draft menu family")
    _require(isinstance(ui.get("options"), list), "complete visible draft options required")
    for option in ui["options"]:
        _require(isinstance(option, dict) and not set(option) - {"id", "label", "enabled", "costs", "card", "role", "node", "reward", "activate_hint", "shortcut_hint", "offer"},
                 "draft options need visible semantics, not prebound controls")
    derived = {"screen": "rest" if family.startswith("campfire_") else "selection" if family in {"card_upgrade", "combat_card_selection", "shop_remove"} else "shop" if family.startswith('shop_') else "map" if family.startswith("map_") else "reward" if family == "loot_rewards" else "card_reward" if family == "loot_cards" else "treasure" if family == "treasure_options" else "event",
        "order": [option["id"] for option in ui["options"]], "control_layout": "ps5_default",
        "selection_mode": "toggle" if family == "card_upgrade" else "immediate",
        "required_count": 1, "navigation": []}
    if family == "card_upgrade":
        derived["selection_purpose"] = "upgrade"
    for key, expected in derived.items():
        _require(key not in ui or _same_json(ui[key], expected), "draft UI conflicts with its declared menu family: " + key)
        ui[key] = expected
    ui.setdefault("layout_id", "ps5-" + family.replace("_", "-") + "-reviewed-v1")
    ui.setdefault("phase", "choose")
    ui.setdefault("selected_ids", [])
    ui.setdefault("pending_ids", [])
    return ui


def _draft(value):
    draft = _json(value)
    _require(isinstance(draft, dict) and set(draft) - {'decision_policy'} == {
        "schema", "context", "inventory", "resources", "facts", "ui", "choice", "reasoning"}
        and draft["schema"] == SCHEMA, "source-free veda.menu-draft.v1 schema required")
    _require(_text(draft["reasoning"]), "menu reasoning required")
    _require(isinstance(draft["choice"], dict) and set(draft["choice"]) == {"kind", "option_ids", "postconditions"},
             "draft choice needs kind, option IDs and explicit outcome only")
    policy = draft.get('decision_policy', 'strict')
    _inventory(draft["inventory"], policy)
    _ui(draft["ui"])
    _postconditions(draft["choice"]["postconditions"], draft["context"], policy)
    return draft


def _request(draft, checked, control_profile, clock, max_age_seconds=30):
    policy = draft.get('decision_policy', 'strict')
    frame = {"frame_id": checked["frame_id"], "image_sha256": checked["source"]["sha256"],
             "observed_at": checked["source"]["captured_at"]}
    review = dict(checked["review"], kind="reviewed_choice_ui")
    ui = _ui(draft["ui"])
    hint = ui.pop("confirm_hint", None)
    if hint is not None:
        _require(ui["phase"] == "confirm" and isinstance(hint, dict) and set(hint) == {"button", "hint_text"},
                 "a visible confirmation hint belongs only to the reviewed preview")
        ui["confirm"] = {"button": hint["button"], "evidence": {
            "kind": "visible_hint", "reviewer": review["reviewer"], "meaning": "confirm:" + ui["choice_id"],
            "layout_id": ui["layout_id"], "frame_id": frame["frame_id"], "image_sha256": frame["image_sha256"],
            "hint_text": hint["hint_text"]}}
    for option in ui['options']:
        shortcut_hint = option.pop('shortcut_hint', None)
        if shortcut_hint is not None:
            _require(ui['menu_family'] in {'shop_entry', 'shop_stock', 'shop_exit', 'loot_rewards'}
                     and option.get('role') in {'open', 'skip', 'leave', 'proceed'}
                     and isinstance(shortcut_hint, dict) and set(shortcut_hint) == {'button', 'hint_text'},
                     'merchant boundary shortcut needs the actual visible hint')
            option['shortcut'] = {'button': shortcut_hint['button'], 'evidence': {
                'kind': 'visible_hint', 'reviewer': review['reviewer'], 'meaning': 'activate:' + option['id'],
                'layout_id': ui['layout_id'], 'frame_id': frame['frame_id'], 'image_sha256': frame['image_sha256'],
                'hint_text': shortcut_hint['hint_text']}}
        activate_hint = option.pop('activate_hint', None)
        if activate_hint is not None:
            _require(ui['menu_family'] in {'loot_rewards', 'loot_cards', 'campfire_options', 'campfire_exit', 'treasure_options'}
                     and isinstance(activate_hint, dict) and set(activate_hint) == {'button', 'hint_text'},
                     'activation hint needs its actual visible button and text')
            option['activate'] = {'button': activate_hint['button'], 'evidence': {
                'kind': 'visible_hint', 'reviewer': review['reviewer'], 'meaning': 'activate:' + option['id'],
                'layout_id': ui['layout_id'], 'frame_id': frame['frame_id'],
                'image_sha256': frame['image_sha256'], 'hint_text': activate_hint['hint_text']}}
    observation = {"schema": OBSERVATION_SCHEMA, "frame": frame, "review": review,
        "context": deepcopy(draft["context"]), "inventory_digest": inventory_digest(draft["inventory"]),
        "resources": deepcopy(draft["resources"]), "facts": deepcopy(draft["facts"]), "ui": ui}
    observation = bind_reviewed_menu_controls(observation, control_profile=control_profile, now=clock, max_age_seconds=max_age_seconds)
    choice = {"kind": draft["choice"]["kind"], "option_ids": deepcopy(draft["choice"]["option_ids"]),
        "choice_id": ui["choice_id"], "review": dict(review, kind="reviewed_choice"),
        "postconditions": _postconditions(draft["choice"]["postconditions"], draft["context"], policy)}
    post = choice["postconditions"]
    branches = post.get("alternatives", [{"postconditions": post}])
    for branch in branches:
        outcome = branch["postconditions"]
        if outcome.get("context") == "next_room":
            _require(ui["menu_family"] == "map_nodes" and len(choice["option_ids"]) == 1,
                     "next_room context belongs only to one reviewed map node")
            outcome["context"] = map_arrival_context(draft["context"], choice["option_ids"][0], outcome["screen"])
    plan_choice_step(observation, choice, now=clock, max_age_seconds=max_age_seconds)
    if ui["menu_family"] == "card_upgrade":
        target = next(option for option in ui["options"] if option["id"] == choice["option_ids"][0])
        projected = deepcopy(draft["inventory"])
        cards = projected["current"]["card"]
        _require(target["card"]["name"] in cards, "selected upgrade card is absent from reviewed inventory")
        cards.remove(target["card"]["name"])
        cards.append(target["card"]["upgrade_name"])
        _require(inventory_digest(projected) == choice["postconditions"]["inventory_digest"],
                 "upgrade inventory must replace exactly one selected card and preserve all other inventory")
    return {"operation": "prepare", "kind": "choice", "decision_policy": policy, "source": deepcopy(checked["source"]),
        "context": deepcopy(draft["context"]), "review": deepcopy(review), "observation": observation,
        "inventory": deepcopy(draft["inventory"]), "choice": choice, "reasoning": draft["reasoning"]}


def map_arrival_context(context, node_id, screen):
    """Name provisional arrival scopes; no ledger records or combat facts exist yet.

    The same selected node has one floor identity across outcome alternatives.
    Only a combat-screen branch receives provisional combat/turn identities.
    Actual observation and the ordinary lifecycle receipt establish real rows.
    """
    from .choice_execution import _context
    _context(context)
    _require(_text(node_id, 256) and screen in {"combat", "rest", "shop", "event", "treasure", "reward"},
             "map arrival requires a named node and supported room screen")
    _require(context["combat_id"] is None and context["turn_id"] is None,
             "map arrival starts from a noncombat context")
    basis = json.dumps(["veda.map-arrival.v1", context["run_id"], context["floor_id"], node_id], separators=(",", ":"))
    identity = str(uuid5(NAMESPACE_URL, basis))
    return {"run_id": context["run_id"], "floor_id": "map-floor-" + identity,
            "combat_id": "map-combat-" + identity if screen == "combat" else None,
            "turn_id": "map-turn-" + identity if screen == "combat" else None}


def _digest(draft):
    return hashlib.sha256(json.dumps(draft, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate(draft, control_profile):
    # This source is deliberately synthetic and private. Return neither it nor
    # any runnable observation/proposal, and never write it to an adapter file.
    frame_id = "validation-only-no-capture"
    checked = {"frame_id": frame_id,
        "source": {"sha256": "0" * 64, "captured_at": _OFFLINE_TIME.isoformat()},
        "review": {"complete": True, "reviewer": "offline schema validation only", "frame_id": frame_id,
                   "image_sha256": "0" * 64}}
    _request(draft, checked, control_profile, _OFFLINE_TIME)
    return {"schema": "veda.menu-draft-validation.v1", "draft_digest": _digest(draft), "draft_valid": True,
        "decision_policy": draft.get('decision_policy', 'strict'),
        "validation_only": True, "source_bound": False, "dispatchable": False,
        "controller_input_sent": False, "requires_exact_fresh_capture_review": True}


@_checked
def validate_menu_draft(draft, control_profile):
    """Check strategy and schema before capture; return no executable request."""
    return _validate(_draft(draft), control_profile)


@_checked
def write_menu_request(draft, *, capture, reviewer, evidence_note, reviewed,
                       control_profile, output, now=None, execute=False, session=None):
    """Exclusively publish a prepare request after explicit exact-image review.

    ``reviewed=True`` declares that this exact fresh image was inspected and all
    draft facts, focus, visible options and any hint match it. It is not inferred
    from an earlier capture, a successful draft check or identical file pixels.
    The adapter independently rechecks source, age and pending state at dispatch.
    """
    draft = _draft(draft)
    _validate(draft, control_profile)  # Diagnose structure before source handling.
    checked = reviewed_capture_source(capture=capture, reviewer=reviewer,
        evidence_note=evidence_note, reviewed=reviewed, now=now, max_age_seconds=None if session is not None else 30)
    clock = now if now is not None else datetime.now(timezone.utc)
    request = _request(draft, checked, control_profile, clock, max_age_seconds=None if session is not None else 30)
    if session is not None:
        from .evidence_continuity import bind_session
        request['evidence_binding'] = bind_session(session, draft['context'], checked['source'])
    _require(type(execute) is bool, "execute must be an explicit boolean")
    if execute:
        request["operation"] = "execute"
    data = (json.dumps(request, sort_keys=True, allow_nan=False, indent=2) + "\n").encode()
    _require(len(data) <= MAX_BYTES, "menu request exceeds adapter byte bound")
    _require(isinstance(output, (str, Path)) and _text(str(output)), "output path required")
    declared = Path(output).expanduser()
    destination = declared.parent.resolve() / declared.name
    image_path = Path(checked["source"]["path"])
    _require(destination.resolve() not in {image_path, image_path.with_suffix(".capture.json")},
             "output cannot overwrite capture or receipt")
    # Recheck the complete original receipt and image after serialization. No
    # caller-supplied timestamp or hash is ever spliced into this request.
    _require(reviewed_capture_source(capture=capture, reviewer=reviewer,
        evidence_note=evidence_note, reviewed=reviewed, now=now, max_age_seconds=None if session is not None else 30) == checked,
        "capture changed during menu packaging")
    created = False
    try:
        with destination.open("xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise ValueError("menu output already exists") from None
    except OSError:
        if created:
            destination.unlink(missing_ok=True)
        raise ValueError("menu output write failed") from None
    return {"request_file": str(destination)}
