"""Join source-checked inspections to Spire's existing advisory checks.

This is an offline/review interface, not an execution interpreter. Completeness
assertions retain their reader or reviewer origin. Neither a recent timestamp
nor an explicit assertion grants live calibration or controller permission.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import json
from pathlib import Path
import time

from .advisory import boss_manifest, check_plan, relevant_rules, validate_snapshot
from .evidence_ledger import EvidenceLedger, _bounded
from .saved_frame_reader import _identity


def load_evidence_bundle(path):
    path = Path(path).expanduser().resolve()
    with path.open('rb') as stream:
        data = stream.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("input JSON exceeds the 4 MB bound")
    bundle = json.loads(data)
    if (not isinstance(bundle, dict) or not isinstance(bundle.get("journal"), dict)
            or not isinstance(bundle.get("source_files"), dict)
            or any(not isinstance(value, str) or not value.strip() for value in bundle["source_files"].values())):
        raise ValueError("bundle needs a journal object and source_files mapping hashes to PNG paths")
    bundle["source_files"] = {digest: str((path.parent / value).resolve())
                              for digest, value in bundle["source_files"].items()}
    return bundle


def partial_observation(reading, *, context, epoch, observed_at):
    """Import only identified HUD facts; candidates do not become a full hand.

    ``observed_at`` is supplied by the capture/review record. A saved reader's
    processing time is deliberately never substituted for capture time.
    """
    if (not isinstance(reading, dict) or reading.get("schema") != "veda.partial-saved-frame.v1"
            or reading.get("ok") is not True or reading.get("partial") is not True
            or any(reading.get(k) is not False for k in
                   ("runtime_authorized", "controller_authorized", "runtime_authorization_eligible"))
            or reading.get("cards", {}).get("hand_complete") not in (None, False)):
        raise ValueError("a successful partial saved-frame reading is required")
    path = Path(reading["image_path"]).expanduser().resolve()
    digest, dimensions = _identity(path)
    if digest != reading.get("image_sha256") or dimensions != reading.get("source_dimensions"):
        raise ValueError("partial reading source changed")
    hud = reading.get("hud", {})
    data = {key: hud[key] for key in ("hp", "max_hp", "energy") if hud.get(key) is not None}
    block = reading.get("combat_evidence", {}).get("player_block")
    if block is not None:
        data["block"] = block
    return {"context": deepcopy(context), "epoch": epoch,
            "frame_id": reading["frame_id"], "image_sha256": digest, "observed_at": observed_at,
            "origin": {"kind": "reader", "source": "saved-frame partial reader",
                       "evidence_ref": str(path), "verified": True},
            "sections": {"player": {"data": data, "complete": False}}}


def verify_sources(journal, source_files):
    """Verify every observed frame against its explicit saved PNG, once/hash."""
    _bounded(journal)
    if (not isinstance(journal, dict) or not isinstance(journal.get("events"), list)
            or len(journal["events"]) > 1024
            or any(not isinstance(event, dict) or not isinstance(event.get("payload"), dict)
                   for event in journal["events"])):
        raise ValueError("bounded observation journal required")
    if not isinstance(source_files, dict) or len(source_files) > 1024:
        raise ValueError("bounded source_files hash-to-PNG mapping required")
    verified = {}
    for event in journal.get("events", []):
        if event.get("operation") not in ("observe", "record_inventory_event"):
            continue
        packet = event.get("payload", {})
        digest = packet.get("image_sha256")
        if not isinstance(digest, str) or digest not in source_files:
            raise ValueError("every observation needs a source PNG in source_files")
        if digest not in verified:
            if not isinstance(source_files[digest], str) or not source_files[digest].strip():
                raise ValueError("source PNG paths must be nonempty text")
            path = Path(source_files[digest]).expanduser().resolve()
            actual, dimensions = _identity(path)
            if actual != digest:
                raise ValueError("source PNG hash mismatch")
            verified[digest] = {"path": str(path), "dimensions": dimensions}
    return verified


def advisory_context(snapshot, *, mode="review", as_of=None, max_age_seconds=180):
    """Require complete combat sections and preserve unavailable pile zones.

    Review uses wall-clock freshness. Replay must name a historical clock and
    reports only what could be checked at that point in the recorded sequence.
    """
    if mode not in ("review", "replay"):
        raise ValueError("mode must be review or replay")
    if type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds) or not 0 < max_age_seconds <= 180:
        raise ValueError("capture freshness limit must be positive and at most 180 seconds")
    if mode == "review" and as_of is not None:
        raise ValueError("review mode uses the current clock")
    if mode == "replay" and as_of is None:
        raise ValueError("replay requires an explicit historical as_of timestamp")
    now = datetime.now(timezone.utc) if as_of is None else datetime.fromisoformat(as_of)
    if now.tzinfo is None or now > datetime.now(timezone.utc):
        raise ValueError("as_of must be timezone-aware and not in the future")
    sections = snapshot["sections"]
    unknowns = []
    for name in ("player", "hand", "enemies", "statuses", "ui"):
        section = sections[name]
        if section.get("current") is not True or section.get("complete") is not True:
            unknowns.append(f"{name}: {section.get('status', 'missing')} evidence needs inspection")
    frame = snapshot.get("frame")
    age = None if not frame else (now-datetime.fromisoformat(frame["observed_at"])).total_seconds()
    fresh = age is not None and 0 <= age <= max_age_seconds
    if not fresh:
        unknowns.append("capture is missing, stale, or later than the replay clock")
    inv_section = sections["inventory"]
    inventory = deepcopy(inv_section.get("data")) if inv_section.get("current") else None
    if inventory is None:
        unknowns.append("inventory: current verified baseline or transition is missing")
        inventory = {"current": {}, "coverage": {}}
    for kind in ("relic", "potion"):
        if inventory.get("coverage", {}).get(kind) != "complete":
            unknowns.append(f"complete {kind} inventory is unconfirmed")
    ui = sections["ui"].get("data") or {}
    if ui.get("phase") != "combat":
        unknowns.append("return to and verify the combat screen before checking a play")
    result = {"fresh": fresh, "unknowns": unknowns, "state": None, "inventory": inventory,
              "encounter_type": None, "boss_manifest": None, "rules": None,
              "mode": mode, "capture_age_seconds": age,
              "runtime_authorized": False, "controller_authorized": False}
    # No partial roster/hand/status object can become a checked combat snapshot.
    if unknowns:
        return result
    player = deepcopy(sections["player"]["data"])
    statuses = sections["statuses"]["data"]
    hand_data = sections["hand"]["data"]
    cards = {card["id"]: card for card in hand_data["cards"]}
    enemy_data = sections["enemies"]["data"]
    enemies = {enemy["id"]: enemy for enemy in enemy_data["enemies"]}
    ordered_enemies = []
    for ident in enemy_data["target_order"]:
        enemy = deepcopy(enemies[ident])
        status = statuses["enemies"][ident]
        # Duplicate known facts cannot silently overwrite a contradictory value.
        if any(key in enemy and enemy[key] is not None and enemy[key] != value for key, value in status.items()):
            unknowns.append(f"enemy {ident} has contradictory status values")
        enemy.update(deepcopy(status))
        ordered_enemies.append(enemy)
    piles = {zone: None for zone in ("draw", "discard", "exhaust", "draw_order")}
    pile_section = sections["piles"]
    if pile_section.get("current"):
        data = pile_section["data"]
        for zone in ("draw", "discard", "exhaust"):
            if pile_section.get("complete") or data.get("coverage", {}).get(zone) == "complete":
                piles[zone] = deepcopy(data.get(zone))
        if piles["draw"] is not None:
            piles["draw_order"] = deepcopy(data.get("draw_order"))
    status_fields = ("strength", "dexterity", "weak", "vulnerable", "frail", "no_block",
                     "powers", "counters", "end_turn_damage", "unmodeled_effects")
    for key in ("hp", "max_hp", "energy", "block", "ascension"):
        if key in statuses["player"] and statuses["player"][key] is not None and statuses["player"][key] != player[key]:
            unknowns.append(f"player {key} has contradictory status values")
    state = {**player, **{key: deepcopy(statuses["player"][key]) for key in status_fields}, "schema": "spire.advisory.v1",
             "observed_at": frame["observed_at"], "hand_complete": True, "powers_complete": True,
             "hand": [deepcopy(cards[ident]) for ident in hand_data["order"]],
             "enemies": ordered_enemies, "piles": piles}
    validate_snapshot(state)
    result["state"] = state
    result["rules"] = relevant_rules(state, inventory)
    # An unknown encounter classification is never silently called a hallway.
    encounter_type = enemy_data.get("encounter_type")
    if encounter_type not in ("enemy", "elite", "boss"):
        unknowns.append("encounter type is unconfirmed")
    result["encounter_type"] = encounter_type
    if encounter_type == "boss":
        result["boss_manifest"] = boss_manifest(enemy_data.get("encounter_name"), player["ascension"])
        if result["boss_manifest"] is None:
            unknowns.append("a reviewed manifest for this boss and Ascension is unavailable")
    result["incoming_displayed"] = sum(sum(e["intent_hits"]) for e in ordered_enemies if e["hp"] > 0)
    return result


def check_evidence(journal, *, source_files, plan=None, mode="review", as_of=None):
    """Replay the ledger, verify image identities, then invoke shared tactics."""
    from .inspection_plan import plan_inspections

    started = time.monotonic()
    sources = verify_sources(journal, source_files)
    verified_at = time.monotonic()
    ledger = EvidenceLedger.from_dict(journal)
    snapshot = ledger.snapshot()
    context = advisory_context(snapshot, mode=mode, as_of=as_of)
    assembled_at = time.monotonic()
    checked = check_plan(context, plan) if plan is not None else None
    action_names = []
    if context.get("state") and isinstance(plan, dict):
        by_id = {c["id"]:c["name"] for c in context["state"]["hand"]}
        action_names = [by_id[s["card_id"]] for s in plan.get("steps", [])
                        if isinstance(s, dict) and s.get("card_id") in by_id]
        context["rules"] = relevant_rules(context["state"], context["inventory"], action_names)
    decision = {"kind": "combat_action", "effects": []}
    if isinstance(plan, dict):
        steps = plan.get("steps", [])
        if isinstance(steps, list) and steps and isinstance(steps[0], dict):
            first = steps[0]
            decision["kind"] = {"end_turn": "end_turn", "potion": "play_potion"}.get(first.get("kind"), "play_card")
        hand = snapshot["sections"]["hand"]
        named = {c["id"]:c.get("name", "").rstrip("+")
                 for c in (hand.get("data") or {}).get("cards", [])} if hand["current"] else {}
        names = {named.get(step.get("card_id")) for step in steps if isinstance(step, dict)} if isinstance(steps, list) else set()
        if "Headbutt" in names:
            decision["effects"].append("discard")
        if "Corruption" in names and "Runic Pyramid" in context["inventory"].get("current", {}).get("relic", []):
            decision["effects"].append("draw")
            decision["long_fight"] = True
        if plan.get("long_fight") is True:
            decision["long_fight"] = True
    inspections = plan_inspections(snapshot, decision=decision)
    inspections["advisory_blockers"] = list(context["unknowns"])
    inspections["decision_blocked"] = inspections["decision_blocked"] or bool(context["unknowns"])
    # Catch source mutation during replay/checking instead of publishing mixed evidence.
    if verify_sources(journal, source_files) != sources:
        raise ValueError("source changed during advisory assembly")
    kinds = sorted({item["origin"]["kind"] for section in snapshot["sections"].values()
                    for item in section["evidence"] if "origin" in item})
    return {"schema": "veda.evidence-advisory.v1", "mode": mode,
            "advisory_ready": not context["unknowns"] and not inspections["decision_blocked"]
                              and (checked is None or checked["allowed"]),
            "runtime_authorized": False, "controller_authorized": False,
            "automatic_recognition_complete": False,
            "provenance_kinds": kinds, "sources": sources, "snapshot": snapshot,
            "context": context, "checked_plan": checked, "inspections": inspections,
            "timing_ms": {"source_verification": (verified_at-started)*1000,
                          "ledger_and_context": (assembled_at-verified_at)*1000,
                          "plan_and_final_source_verification": (time.monotonic()-assembled_at)*1000},
            "limitations": ["Source hashes verify file identity, not the truth of a reviewer's labels.",
                            "Unlogged physical input must be reported to invalidate current evidence.",
                            "A checked plan is advisory only; execution still requires separate live calibration and run arming."]}
