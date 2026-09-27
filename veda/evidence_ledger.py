"""Source-bound facts across inspections; no recognition, game rules or input.

``current`` means consistent with the caller's logical observation epoch, not
fresh live capture. The caller must report every input/context change. Verified
origins are declarations by an upstream reader/reviewer, never certifications
created by this module. Inventories are imported receipts from the existing
inventory ledger, not a second card/potion event-replay engine.

Observation packet::

    {"context": {"run_id": ..., "floor_id": ..., "combat_id": ..., "turn_id": ...},
     "epoch": 0, "frame_id": ..., "image_sha256": ..., "observed_at": aware_iso,
     "origin": {"kind": "reader"|"reviewer", "source": ..., "evidence_ref": ...,
                "verified": True},
     "sections": {"hand": {"data": {"cards": [...], "order": [...]},
                           "complete": True, "completeness_evidence": ...}}}

Cross-frame carry additionally requires ``continuity`` with previous_frame_id,
state_unchanged=True and evidence_ref. UI focus is always observed anew. This is
an explicit continuity assertion, not a conclusion from a matching turn ID.
``dump``/``from_dict`` persist and replay a bounded event journal; they neither
write SQLite nor upgrade imported partial reader output to complete state.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SECTIONS = ("player", "hand", "enemies", "statuses", "ui", "piles", "inventory")
_VOLATILE = SECTIONS[:-1]
_CONTEXT = ("run_id", "floor_id", "combat_id", "turn_id")
_MAX_EVENTS = 1024
_MAX_JSON = 2_000_000
_PLAYER = ("hp", "max_hp", "energy", "block", "ascension")
_STATUS = ("strength", "dexterity", "weak", "vulnerable", "frail", "no_block",
           "powers", "counters", "end_turn_damage", "unmodeled_effects")
_ENEMY_STATUS = ("strength", "weak", "vulnerable", "artifact", "powers")


def _bounded(value):
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 50_000 or depth > 20:
            raise ValueError("evidence size/depth bound exceeded")
        if isinstance(item, dict):
            if len(item) > 1000 or any(not isinstance(k, str) for k in item):
                raise ValueError("invalid evidence object")
            pending.extend((v, depth+1) for v in item.values())
        elif isinstance(item, list):
            if len(item) > 2000:
                raise ValueError("evidence list bound exceeded")
            pending.extend((v, depth+1) for v in item)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError("evidence must be JSON-compatible")
        elif type(item) in (int, float) and (not math.isfinite(item) or abs(item) > 10**12):
            raise ValueError("invalid evidence number")
    if len(json.dumps(value, allow_nan=False)) > _MAX_JSON:
        raise ValueError("evidence byte bound exceeded")


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _time(value):
    _require(_text(value), "observed_at must be a timezone-aware timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid observed_at") from exc
    _require(result.tzinfo is not None, "observed_at must be timezone-aware")
    _require(result <= datetime.now(timezone.utc), "observation time is in the future")
    return result


def _context(value):
    _require(isinstance(value, dict) and set(value) == set(_CONTEXT), "exact context binding required")
    _require(_text(value["run_id"]), "run_id is required")
    _require(all(v is None or _text(v) for v in value.values()), "invalid context identifier")
    _require(value["turn_id"] is None or value["combat_id"] is not None, "turn needs combat binding")
    return deepcopy(value)


def _origin(value, *, inventory=False):
    kinds = {"reader", "reviewer", "ledger"} if inventory else {"reader", "reviewer"}
    _require(isinstance(value, dict) and value.get("kind") in kinds
             and value.get("verified") is True and _text(value.get("source"))
             and _text(value.get("evidence_ref")), "named verified evidence origin required")
    return deepcopy(value)


def _frame(packet):
    _require(_text(packet.get("frame_id")), "frame_id is required")
    _require(isinstance(packet.get("image_sha256"), str)
             and re.fullmatch(r"[0-9a-f]{64}", packet["image_sha256"]), "image hash is required")
    _time(packet.get("observed_at"))
    return {k: packet[k] for k in ("frame_id", "image_sha256", "observed_at")}


def _numbers(data, nonnegative=(), signed=()):
    for key in (*nonnegative, *signed):
        if key in data and data[key] is not None:
            _require(type(data[key]) is int and (key in signed or data[key] >= 0), f"invalid {key}")


def _known(data, keys):
    _require(all(k in data and data[k] is not None for k in keys), "complete section contains unknown fields")


def _names(value):
    _require(isinstance(value, list) and len(value) <= 500 and all(_text(v) for v in value), "named list required")


def _objects(data, key, limit):
    rows = data.get(key)
    if rows is None:
        return []
    _require(isinstance(rows, list) and len(rows) <= limit, f"invalid {key}")
    _require(all(isinstance(v, dict) and _text(v.get("id")) for v in rows), "unique observed IDs required")
    _require(len({v["id"] for v in rows}) == len(rows), "duplicate observed ID")
    return rows


def _ordered(data, order_key, rows, complete):
    order = data.get(order_key)
    if order is not None:
        _names(order)
        _require(len(set(order)) == len(order) and set(order) == {r["id"] for r in rows}, "order must match observed IDs")
    if complete:
        _require(order is not None, "complete section requires explicit order")


def _validate_section(name, data, complete):
    _require(isinstance(data, dict) and type(complete) is bool, "section data/object and completeness required")
    if name == "player":
        _numbers(data, _PLAYER)
        if data.get("ascension") is not None:
            _require(data["ascension"] <= 20, "invalid Ascension")
        if complete:
            _known(data, _PLAYER)
    elif name == "hand":
        rows = _objects(data, "cards", 10)
        for card in rows:
            _numbers(card, ("cost",))
            for k in ("playable", "upgraded"):
                _require(card.get(k) is None or type(card[k]) is bool, f"invalid card {k}")
            _require(card.get("type") in (None, "Attack", "Skill", "Power", "Status", "Curse"), "invalid card type")
            if card.get("name") is not None:
                _require(_text(card["name"]), "invalid card name")
            if card.get("title_color") is not None:
                _require(card["title_color"] in ("white", "green", "teal"), "invalid title color")
            if complete:
                _known(card, ("name", "type", "playable", "upgraded", "title_color"))
                _require(card.get("cost") is not None or (card.get("playable") is False
                         and card.get("unplayable") is True and card.get("cost_is_absent") is True), "current card cost is unread")
                _require(card["name"].endswith("+") == card["upgraded"]
                         and (card["title_color"] != "white") == card["upgraded"], "card upgrade/color conflict")
        if complete:
            _known(data, ("cards",))
        _ordered(data, "order", rows, complete)
    elif name == "enemies":
        rows = _objects(data, "enemies", 10)
        for enemy in rows:
            _numbers(enemy, ("hp", "max_hp", "block"))
            for field in ("name", "intent"):
                _require(enemy.get(field) is None or _text(enemy[field]), f"invalid enemy {field}")
            hits = enemy.get("intent_hits")
            _require(hits is None or isinstance(hits, list) and len(hits) <= 100
                     and all(type(v) is int and v >= 0 for v in hits), "explicit per-hit values required")
            if complete:
                _known(enemy, ("name", "hp", "max_hp", "block", "intent", "intent_hits"))
            if enemy.get("hp") is not None and enemy.get("max_hp") is not None:
                _require(enemy["hp"] <= enemy["max_hp"], "enemy HP exceeds maximum")
        if complete:
            _known(data, ("enemies",))
        _ordered(data, "target_order", rows, complete)
    elif name == "statuses":
        for key in ("player", "enemies"):
            _require(data.get(key) is None or isinstance(data[key], dict), "status objects required")
        player = data.get("player") or {}
        _require(set(player) <= set(_STATUS), "status fields cannot override player resources or other sections")
        _numbers(player, ("weak", "vulnerable", "frail", "no_block", "end_turn_damage"), ("strength", "dexterity"))
        for key in ("powers", "counters"):
            _require(player.get(key) is None or isinstance(player[key], dict), "power/counter object required")
            for label, value in (player.get(key) or {}).items():
                _require(_text(label) and (value is None and not complete or
                         type(value) is int and value >= 0 or key == "powers" and type(value) is bool),
                         "unknown or invalid power/counter value")
        _require(player.get("unmodeled_effects") is None or isinstance(player["unmodeled_effects"], list), "effects list required")
        for ident, enemy in (data.get("enemies") or {}).items():
            _require(_text(ident) and isinstance(enemy, dict), "enemy status ID required")
            _require(set(enemy) <= set(_ENEMY_STATUS), "status fields cannot override enemy identity/resources/intent")
            _numbers(enemy, ("weak", "vulnerable", "artifact"), ("strength",))
            _require(enemy.get("powers") is None or isinstance(enemy["powers"], dict), "enemy powers object required")
            for label, value in (enemy.get("powers") or {}).items():
                _require(_text(label) and (value is None and not complete or type(value) is bool
                         or type(value) is int and value >= 0), "unknown or invalid enemy power")
            if complete:
                _known(enemy, _ENEMY_STATUS)
        if complete:
            _known(data, ("player", "enemies"))
            _known(player, _STATUS)
    elif name == "ui":
        _require(data.get("phase") is None or _text(data["phase"]), "invalid UI phase")
        for key in ("focused_card_id", "selected_card_id", "focused_target_id"):
            _require(data.get(key) is None or _text(data[key]), "invalid UI focus")
        if complete:
            _known(data, ("phase",))
            _require(all(k in data for k in ("focused_card_id", "selected_card_id", "focused_target_id")), "complete UI needs explicit focus fields")
    elif name == "piles":
        for zone in ("draw", "discard", "exhaust", "draw_order"):
            if data.get(zone) is not None:
                _names(data[zone])
        if data.get("draw_order") is not None:
            _require(data.get("draw") is not None and Counter(data["draw_order"]) == Counter(data["draw"]), "draw order does not match contents")
        coverage, proof = data.get("coverage", {}), data.get("zone_evidence", {})
        _require(isinstance(coverage, dict) and isinstance(proof, dict)
                 and set(coverage) <= {"draw", "discard", "exhaust"}
                 and set(proof) <= {"draw", "discard", "exhaust"}, "invalid pile coverage/proof")
        for zone, level in coverage.items():
            _require(level in ("unknown", "partial", "complete"), "invalid pile coverage")
            if level == "complete":
                _require(data.get(zone) is not None and _text(proof.get(zone)), "complete pile needs contents and zone evidence")
        if data.get("draw_order") is not None:
            _require(coverage.get("draw") == "complete", "draw order requires a complete draw pile")
        if complete:
            _known(data, ("draw", "discard", "exhaust"))
            _require(all(coverage.get(z) == "complete" for z in ("draw", "discard", "exhaust")), "complete piles need all zone coverage")
    if data.get("hp") is not None and data.get("max_hp") is not None:
        _require(data["hp"] <= data["max_hp"], "HP exceeds maximum")


class _Conflict(ValueError):
    pass


def _merge(old, new, *, path, old_complete, new_complete):
    if old is None:
        return deepcopy(new)
    if new is None:
        # Explicit null focus in a complete UI is an observed absence.
        if path.startswith("ui.") and new_complete:
            raise _Conflict(path)
        return deepcopy(old)
    if type(old) is not type(new):
        raise _Conflict(path)
    if isinstance(old, dict):
        if path == "piles.coverage":
            levels = ("unknown", "partial", "complete")
            return {k: max((old.get(k, "unknown"), new.get(k, "unknown")), key=levels.index) for k in set(old) | set(new)}
        if path == "piles.zone_evidence":
            return {**old, **new}  # Both original proofs remain in section evidence.
        if path.endswith((".powers", ".counters")) or path == "statuses.enemies":
            if old_complete and set(new)-set(old) or new_complete and set(old)-set(new):
                raise _Conflict(path)
        result = deepcopy(old)
        for k, v in new.items():
            if k in old:
                # A previous explicit absence cannot be replaced within the epoch.
                if path == "ui" and old_complete and old[k] is None and v is not None:
                    raise _Conflict(path+"."+k)
                old_full, new_full = old_complete, new_complete
                if path == "piles" and k in ("draw", "discard", "exhaust"):
                    old_full = old.get("coverage", {}).get(k) == "complete"
                    new_full = new.get("coverage", {}).get(k) == "complete"
                result[k] = _merge(old[k], v, path=path+"."+k, old_complete=old_full, new_complete=new_full)
            else:
                result[k] = deepcopy(v)
        return result
    if isinstance(old, list) and path in ("hand.cards", "enemies.enemies"):
        a, b = {v["id"]: v for v in old}, {v["id"]: v for v in new}
        if old_complete and set(b)-set(a) or new_complete and set(a)-set(b):
            raise _Conflict(path)
        ids = list(b) if new_complete else list(a)+[key for key in b if key not in a]
        return [_merge(a[k], b[k], path=path+"."+k, old_complete=old_complete, new_complete=new_complete)
                if k in a and k in b else deepcopy(a[k] if k in a else b[k]) for k in ids]
    if isinstance(old, list) and path in ("piles.draw", "piles.discard", "piles.exhaust"):
        a, b = Counter(old), Counter(new)
        if old_complete and b-a or new_complete and a-b:
            raise _Conflict(path)
        return list(new) if new_complete else list(old)+list((b-a).elements())
    if old != new:
        raise _Conflict(path)
    return deepcopy(old)


def _empty():
    return {"data": None, "status": "missing", "current": False, "complete": False,
            "evidence": [], "invalidated_by": []}


def inventory_digest(data):
    _bounded(data)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EvidenceLedger:
    def __init__(self, run_id, floor_id=None, combat_id=None, turn_id=None):
        self.context = _context(dict(run_id=run_id, floor_id=floor_id, combat_id=combat_id, turn_id=turn_id))
        self._initial_context = deepcopy(self.context)
        self.epoch = 0
        self.frame = None
        self._sections = {name: _empty() for name in SECTIONS}
        self._frames = {}
        self._inventory_events = {}
        self._events = []
        self._last_time = None

    def _transaction(self, operation, payload):
        _bounded(payload)
        _require(isinstance(payload, dict), "event payload must be an object")
        _require(len(self._events) < _MAX_EVENTS, "ledger event bound reached; archive and start a new ledger")
        candidate = deepcopy(self)
        getattr(candidate, "_"+operation)(deepcopy(payload))
        candidate._events.append({"operation": operation, "payload": deepcopy(payload)})
        _bounded(candidate.dump())
        self.__dict__.update(candidate.__dict__)
        return self.snapshot()

    def observe(self, packet):
        return self._transaction("observe", packet)

    def _binding(self, packet):
        _require(_context(packet.get("context")) == self.context, "observation context mismatch")
        _require(type(packet.get("epoch")) is int and packet["epoch"] == self.epoch, "observation epoch mismatch")

    def _invalidate_sections(self, names, reason):
        for name in names:
            section = self._sections[name]
            section.update(data=None, status="stale", current=False, complete=False)
            section["invalidated_by"] = [reason]
            section.pop("digest", None)

    def _observe(self, packet):
        self._binding(packet)
        frame, origin = _frame(packet), _origin(packet.get("origin"))
        observed = _time(frame["observed_at"])
        _require(self._last_time is None or observed >= self._last_time, "observation predates current evidence")
        key = frame["frame_id"]
        identity = (frame["image_sha256"], self.epoch, self.context)
        _require(key not in self._frames or self._frames[key] == identity, "frame identifier reused across source/epoch/context")
        _require(key not in self._frames or self.frame is not None and key == self.frame["frame_id"],
                 "superseded frame cannot be replayed")
        sections = packet.get("sections")
        _require(isinstance(sections, dict) and bool(sections) and set(sections) <= set(_VOLATILE), "known volatile sections required; inventory needs an event receipt")
        for name, section in sections.items():
            _require(isinstance(section, dict) and type(section.get("complete")) is bool, "section completeness required")
            _require(not section["complete"] or _text(section.get("completeness_evidence")), "complete section needs explicit evidence")
            if name == "piles" and isinstance(section.get("data"), dict):
                data = section["data"]
                if "coverage" not in data:
                    data["coverage"] = {z: "complete" if section["complete"] else
                                        "partial" if data.get(z) is not None else "unknown"
                                        for z in ("draw", "discard", "exhaust")}
                    if section["complete"]:
                        data["zone_evidence"] = {z: section["completeness_evidence"] for z in data["coverage"]}
            _validate_section(name, section.get("data"), section["complete"])
        continuity = packet.get("continuity")
        if self.frame is not None and key != self.frame["frame_id"]:
            if continuity is None:
                self._invalidate_sections(SECTIONS, "new_frame_without_continuity: inspect current state")
            else:
                _require(isinstance(continuity, dict) and continuity.get("previous_frame_id") == self.frame["frame_id"]
                         and continuity.get("state_unchanged") is True and _text(continuity.get("evidence_ref")), "invalid cross-frame continuity")
                self._invalidate_sections(("ui",), "ui_frame_changed: inspect current focus")
                for name in SECTIONS:
                    if self._sections[name]["current"]:
                        self._sections[name]["evidence"].append({"continuity": deepcopy(continuity), "frame": frame,
                            "origin": origin, "context": deepcopy(self.context), "epoch": self.epoch})
        else:
            _require(continuity is None, "continuity requires a different current frame")
            if self.frame is not None:
                _require(frame == self.frame, "same-frame metadata changed")
        self.frame, self._last_time = frame, observed
        self._frames[key] = deepcopy(identity)
        for name, offered in sections.items():
            section = self._sections[name]
            evidence = {"frame": frame, "origin": origin, "context": deepcopy(self.context), "epoch": self.epoch,
                        "complete": offered["complete"], "completeness_evidence": offered.get("completeness_evidence")}
            section["evidence"].append(deepcopy(evidence))
            if section["status"] == "conflict":
                continue
            try:
                data = (_merge(section["data"], offered["data"], path=name,
                               old_complete=section["complete"], new_complete=offered["complete"])
                        if section["current"] else deepcopy(offered["data"]))
                complete = section["complete"] or offered["complete"]
                _validate_section(name, data, complete)
                section.update(data=data, current=True, complete=complete,
                               status="current" if complete else "partial", invalidated_by=[])
            except ValueError as exc:
                self._conflict((name,), "contradictory same-state evidence: "+str(exc))
        self._cross_check()

    def _conflict(self, names, reason):
        for name in names:
            self._sections[name].update(data=None, complete=False, current=False, status="conflict", invalidated_by=[reason])
            self._sections[name].pop("digest", None)

    def _cross_check(self):
        hand, enemies, statuses, ui = (self._sections[k] for k in ("hand", "enemies", "statuses", "ui"))
        if enemies["current"] and enemies["complete"] and statuses["current"] and statuses["complete"]:
            if {e["id"] for e in enemies["data"]["enemies"]} != set(statuses["data"]["enemies"]):
                self._conflict(("enemies", "statuses"), "status coverage disagrees with complete enemy roster")
            else:
                for enemy in enemies["data"]["enemies"]:
                    status = statuses["data"]["enemies"][enemy["id"]]
                    if any(enemy.get(k) is not None and enemy[k] != status[k] for k in _ENEMY_STATUS):
                        self._conflict(("enemies", "statuses"), "enemy status facts contradict roster details")
                        break
        if ui["current"]:
            for key, section, collection in (("focused_card_id", hand, "cards"), ("selected_card_id", hand, "cards"),
                                              ("focused_target_id", enemies, "enemies")):
                if section["current"] and section["complete"] and ui["data"].get(key) is not None:
                    if ui["data"][key] not in {v["id"] for v in section["data"][collection]}:
                        self._conflict(("ui",), "focus is absent from the confirmed hand/roster")
                        break

    def invalidate(self, *, reason, kind="input"):
        return self._transaction("invalidate", {"reason": reason, "kind": kind})

    def _invalidate(self, payload):
        _require(payload.get("kind") in ("input", "unknown_input", "inventory_change") and _text(payload.get("reason")), "explicit invalidation kind/reason required")
        names = _VOLATILE if payload["kind"] == "input" else SECTIONS
        self._invalidate_sections(names, payload["kind"]+": "+payload["reason"])
        self.epoch += 1
        self.frame = None

    def change_context(self, *, run_id, floor_id=None, combat_id=None, turn_id=None, reason):
        return self._transaction("change_context", {"context": dict(run_id=run_id, floor_id=floor_id,
                               combat_id=combat_id, turn_id=turn_id), "reason": reason})

    def _change_context(self, payload):
        context = _context(payload.get("context"))
        _require(_text(payload.get("reason")) and context != self.context, "a changed context and reason are required")
        same_run = context["run_id"] == self.context["run_id"]
        self._invalidate_sections(_VOLATILE if same_run else SECTIONS, "context_change: "+payload["reason"])
        self.context, self.frame = context, None
        self.epoch += 1
        if not same_run:
            self._inventory_events.clear()
            self._last_time = None

    def record_inventory_event(self, receipt):
        """Import a verified baseline or existing-ledger transition's after-state.

        Receipt fields: event_id, kind=baseline|verified_transition, context,
        epoch, frame_id/image_sha256/observed_at, origin, verification_evidence,
        data={current,coverage,properties?}; transitions require prior_digest.
        No potion use, card upgrade, reward or inventory delta is inferred here.
        """
        return self._transaction("record_inventory_event", receipt)

    def _record_inventory_event(self, receipt):
        self._binding(receipt)
        frame = _frame(receipt)
        _require(self.frame is not None and frame == self.frame, "inventory receipt needs the current bound frame")
        origin = _origin(receipt.get("origin"), inventory=True)
        ident, kind = receipt.get("event_id"), receipt.get("kind")
        _require(_text(ident) and kind in ("baseline", "verified_transition")
                 and _text(receipt.get("verification_evidence")), "verified inventory event required")
        if ident in self._inventory_events:
            _require(receipt == self._inventory_events[ident], "inventory event ID reused with different evidence")
            return
        data = receipt.get("data")
        _require(isinstance(data, dict) and isinstance(data.get("current"), dict)
                 and isinstance(data.get("coverage"), dict), "inventory state/coverage required")
        _require(set(data["current"]) == set(data["coverage"]) == {"card", "relic", "potion"}, "all inventory coverage categories required")
        for key in ("card", "relic", "potion"):
            _names(data["current"][key])
            _require(data["coverage"][key] in ("unknown", "partial", "complete"), "invalid inventory coverage")
            _require(data["coverage"][key] != "unknown" or not data["current"][key], "known inventory items cannot have unknown coverage")
        _require(len(set(data["current"]["relic"])) == len(data["current"]["relic"]), "duplicate relic in inventory")
        _require(data.get("properties") is None or isinstance(data["properties"], dict), "inventory properties object required")
        section = self._sections["inventory"]
        _require(section["status"] != "conflict", "conflicted inventory needs a new observation epoch/frame")
        proof = {"event_id": ident, "kind": kind, "origin": origin, "frame": frame,
                 "context": deepcopy(self.context), "epoch": self.epoch,
                 "verification_evidence": receipt["verification_evidence"]}
        if kind == "verified_transition":
            _require(section["current"] and receipt.get("prior_digest") == section.get("digest"), "inventory transition has stale/missing prior state")
        elif section["current"]:
            _require(receipt.get("prior_digest") == section.get("digest"), "inventory replacement baseline needs prior digest")
            for key in ("card", "relic", "potion"):
                before, seen = Counter(section["data"]["current"][key]), Counter(data["current"][key])
                if (section["data"]["coverage"][key] == "complete" and seen-before
                        or data["coverage"][key] == "complete" and before-seen):
                    self._conflict(("inventory",), "inventory baseline contradicts current facts; verified transition required")
                    section["evidence"].append(proof)
                    self._inventory_events[ident] = deepcopy(receipt)
                    return
            # As in TelemetryDatabase.inventory_ledger, a partial look is not
            # evidence that hidden previously confirmed items disappeared.
            data = deepcopy(data)
            for key in ("card", "relic", "potion"):
                if data["coverage"][key] != "complete":
                    before, seen = section["data"]["current"][key], data["current"][key]
                    data["current"][key] = list(before)+list((Counter(seen)-Counter(before)).elements())
                    if section["data"]["coverage"][key] == "complete":
                        data["coverage"][key] = "complete"
                    elif data["current"][key]:
                        data["coverage"][key] = "partial"
        complete = all(v == "complete" for v in data["coverage"].values())
        section.update(data=deepcopy(data), status="current" if complete else "partial", current=True,
                       complete=complete, invalidated_by=[], digest=inventory_digest(data))
        section["evidence"].append(proof)
        self._inventory_events[ident] = deepcopy(receipt)
        if kind == "verified_transition":
            self._invalidate_sections(_VOLATILE, "inventory_change: verified inventory event "+ident)
            self.epoch += 1
            self.frame = None

    def inspection_requirements(self, required=None):
        names = SECTIONS if required is None else required
        _require(isinstance(names, (list, tuple)) and all(n in SECTIONS for n in names), "unknown inspection section")
        return [{"section": name, "status": self._sections[name]["status"],
                 "reason": "; ".join(self._sections[name]["invalidated_by"]) or "complete verified evidence is missing",
                 "action": "inspect", "controller_authorized": False}
                for name in names if not (self._sections[name]["current"] and self._sections[name]["complete"])]

    def snapshot(self):
        return deepcopy({"schema": "veda.evidence-ledger.v1", "context": self.context, "epoch": self.epoch,
                         "frame": self.frame, "sections": self._sections,
                         "inspection_requirements": self.inspection_requirements(),
                         "freshness_basis": "logical epoch only; caller must report unlogged input and verify capture age",
                         "runtime_authorized": False, "controller_authorized": False})

    def dump(self):
        return deepcopy({"schema": "veda.evidence-ledger-journal.v1", "initial_context": self._initial_context,
                         "events": self._events, "runtime_authorized": False, "controller_authorized": False})

    @classmethod
    def from_dict(cls, document):
        _bounded(document)
        _require(isinstance(document, dict) and document.get("schema") == "veda.evidence-ledger-journal.v1"
                 and document.get("runtime_authorized") is False and document.get("controller_authorized") is False,
                 "invalid ledger journal")
        context = _context(document.get("initial_context"))
        events = document.get("events")
        _require(isinstance(events, list) and len(events) <= _MAX_EVENTS, "bounded journal events required")
        result = cls(**context)
        for event in events:
            _require(isinstance(event, dict) and event.get("operation") in
                     ("observe", "invalidate", "change_context", "record_inventory_event"), "invalid journal operation")
            result._transaction(event["operation"], event.get("payload"))
        return result
