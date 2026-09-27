"""Read-only implementation coverage for a historical strategy checkpoint.

This is not a recognizer, tactical checker, calibration evaluator or arming API.
It never follows the checkpoint's evidence/database paths or operates the game.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .advisory import BOUNDARIES, DIRECT, REVIEWED_SPECIAL_CARDS, boss_manifest, rule_pack
from .execution import RUNTIME_FIELDS
from .routine_combat import _PASSIVE_RELICS

SCHEMA = "veda.automation-readiness.v1"
MAX_CHECKPOINT_BYTES = 1_000_000


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("nonfinite JSON number")


def load_checkpoint(path):
    """Read/hash/parse the same bounded bytes; do not inspect linked artifacts."""
    path = Path(path).expanduser().resolve()
    with path.open("rb") as stream:
        raw = stream.read(MAX_CHECKPOINT_BYTES + 1)
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise ValueError("checkpoint exceeds the 1 MB byte limit")
    checkpoint = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    return checkpoint, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw), "binding": "checkpoint bytes only; referenced evidence was not opened"}


def _names(checkpoint, field, limit):
    values = checkpoint.get(field)
    if values is None:
        return None
    if (not isinstance(values, list) or len(values) > limit
            or any(not isinstance(v, str) or not v.strip() or len(v) > 128 for v in values)):
        raise ValueError(f"{field} must be a bounded list of exact recorded names or null")
    return values


def _card_support(name, count):
    base = name[:-1] if name.endswith("+") else name
    direct, special = name in DIRECT, name in REVIEWED_SPECIAL_CARDS
    guarded = base in BOUNDARIES and name in (base, base + "+")
    level = ("direct_numeric" if direct else "exact_boundary_effect" if special
             else "type_and_boundary_guard_only" if guarded else "unsupported")
    followups = []
    if base == "Headbutt":
        followups.append("discard return selection when the confirmed discard pile is nonempty")
    if name in ("True Grit+", "Burning Pact", "Burning Pact+"):
        followups.append("selected hand-card exhaustion")
    if base == "Dual Wield":
        followups.append("Attack/Power copy selection")
    if name == "Armaments":
        followups.append("hand-card upgrade selection")
    if base == "Warcry":
        followups.append("observe the draw, then select a hand card to return")
    conditions = ["Fresh complete hand, actual cost, statuses, inventory, target and UI evidence are still required."]
    if name == "Headbutt":
        conditions.append("The routine candidate supports only a confirmed empty discard; a nonempty return choice needs a separate reviewed selection.")
    if followups:
        conditions.append("The reviewed choice planner can navigate and verify a supplied complete selection contract; it does not choose the card, recognize the grid or add missing card mechanics.")
    if not (direct or special):
        conditions.append("The routine planner does not generate this card as a supported candidate.")
    if special or guarded:
        conditions.append("A new observation is required before continuing beyond the checked effect.")
    if special and REVIEWED_SPECIAL_CARDS[name].get("random_exhaust"):
        conditions.append("The exhausted card is random and remains unknown; no selected target, future hand or full-turn forecast is established.")
    return {"name": name, "copies": count, "checked_support": level,
            "exact_effect_registered": direct or special,
            "routine_candidate_registered": direct or special,
            "selection_followups": followups,
            "selection_execution_implemented": True if followups else None,
            "selection_execution_scope": "reviewed choice contract only" if followups else None,
            "controller_scope": "Codex-reviewed single-input navigation/verification; no current or hardware-validated execution claim",
            "executable_from_checkpoint": False, "conditions": conditions}


def _relic_support(name, count):
    special = {"Shuriken": "Observed 0–2 turn counter required; third Attack stops for Strength/counter inspection.",
               "Red Mask": "Use observed enemy Weak/Artifact; ownership does not apply an opening effect.",
               "Potion Belt": "Slot capacity does not establish current slot contents."}
    supported = name in _PASSIVE_RELICS or name in special
    return {"name": name, "copies_in_record": count,
            "routine_scope": "conditional_observed_state" if name in special else
                             "routine_allowlist_only" if supported else "unsupported_interaction",
            "limitation": special.get(name, "Allowlisting is not a complete relic-effect or interaction simulation."),
            "currently_verified": False}


def _implementation_inventory():
    """Describe bundled code paths, not a reader or hardware validation result."""
    return [
        {"id": "reviewed_choices", "status": "implemented", "modules": ["veda.choice_execution"],
         "scope": "Source-bound single-tap planning and semantic verification for supplied card-selection, potion, map, reward, rest, event, shop and same-run Continue contracts.",
         "limits": "The reviewer supplies strategy, complete options, current costs, button proofs and outcome conditions; this module sends no input."},
        {"id": "play_telemetry", "status": "implemented", "modules": ["veda.play_telemetry"],
         "scope": "Pending decision before input, atomic observed inventory/zone/lifecycle outcomes, idempotent recovery and explicit resume in existing SQLite tables.",
         "limits": "Caller facts are declarations; source checks do not recognize pixels or prove input delivery. Unknown outcomes remain pending."},
        {"id": "codex_reviewed_session", "status": "implemented", "modules": ["veda.reviewed_play", "veda.combat_input"],
         "scope": "A distinct Codex-reviewed session connects one reviewed input at a time to the bridge and telemetry, retaining before/after sources and unresolved attempts.",
         "limits": "Not a standalone recognizer or calibration bypass. Current review, per-run arming, bridge preflight and end-to-end validation remain separate gates."},
        {"id": "collector_a2_bound", "status": "implemented", "modules": ["veda.advisory", "veda.routine_combat"],
         "scope": "Conservative one-enemy-turn survival bound for source-verified A2 Fireball, Buff, Mega Debuff, Spawn/Revive and Torch Head Tackle.",
         "limits": "Requires complete observed roster, displayed hits, modifiers, typed current move and reviewed relic/effect coverage. No exact future roster/status, console action order or next rolled move is established."},
    ]


def assess_automation_readiness(checkpoint, *, now=None):
    """Inventory code coverage; every authority result remains false.

    Caller-supplied arming, freshness and calibration booleans are not evidence.
    A recent recorded timestamp is still only historical checkpoint metadata.
    """
    if not isinstance(checkpoint, dict):
        raise ValueError("checkpoint must be an object")
    try:
        if len(json.dumps(checkpoint, allow_nan=False).encode()) > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint exceeds the 1 MB byte limit")
    except (TypeError, RecursionError) as error:
        raise ValueError("checkpoint must be bounded finite JSON") from error
    state = checkpoint.get("current_state", {})
    if not isinstance(state, dict):
        raise ValueError("current_state must be an object")
    cards, relics = _names(checkpoint, "cards", 300), _names(checkpoint, "relics", 100)
    slots = state.get("potion_slots")
    if slots is not None and (not isinstance(slots, list) or len(slots) > 10
            or any(v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 128) for v in slots)):
        raise ValueError("potion_slots must be a bounded list of names/nulls or null")
    for field in ("hp", "max_hp", "energy", "floor", "act", "ascension", "deck_size"):
        value = state.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"recorded {field} must be a nonnegative integer or null")
    if state.get("ascension") is not None and state["ascension"] > 20:
        raise ValueError("recorded Ascension is outside 0–20")
    if state.get("hp") is not None and state.get("max_hp") is not None and state["hp"] > state["max_hp"]:
        raise ValueError("recorded HP exceeds recorded maximum")
    if checkpoint.get("run_id") is not None and (not isinstance(checkpoint["run_id"], str)
            or not checkpoint["run_id"].strip() or len(checkpoint["run_id"]) > 128):
        raise ValueError("recorded run_id must be bounded text or null")
    for field in ("screen", "next_boss"):
        if state.get(field) is not None and (not isinstance(state[field], str) or not state[field].strip() or len(state[field]) > 128):
            raise ValueError(f"recorded {field} must be bounded text or null")
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("assessment clock must be timezone-aware")
    observed = state.get("observed_at")
    age = None
    if observed is not None:
        if not isinstance(observed, str):
            raise ValueError("observed_at must be a timezone-aware timestamp or null")
        parsed = datetime.fromisoformat(observed)
        if parsed.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        age = (clock - parsed).total_seconds()
    card_rows = [_card_support(name, count) for name, count in Counter(cards or []).items()]
    relic_rows = [_relic_support(name, count) for name, count in Counter(relics or []).items()]
    potion_rows = []
    for name, count in Counter(v for v in slots or [] if v is not None).items():
        potion_rows.append({"name": name, "copies_in_record": count,
            "checked_scope": "immediate damage only; requires complete target/modifier/reaction evidence" if name == "Explosive Potion"
                else "automatic trigger only; not manually usable or included in survival forecasts" if name == "Fairy in a Bottle"
                else "presence/observation boundary only; no numeric effect forecast",
            "controller_execution_implemented": name != "Fairy in a Bottle",
            "controller_scope": "not manually usable" if name == "Fairy in a Bottle" else
                "generic Codex-reviewed potion choice path; requires observed slot/menu/target and outcome contract",
            "currently_verified": False, "hardware_execution_validated": False})
    boss_name, ascension = state.get("next_boss"), state.get("ascension")
    manifest = boss_manifest(boss_name, ascension)
    blockers = [
        {"id": "complete_runtime_reader", "kind": "implementation", "status": "missing",
         "applies_to": ["standalone_automatic"],
         "reason": "No bundled validated worker produces complete hand/order, enemy roster/statuses, inventories, piles and UI/focus Reading evidence."},
        {"id": "independent_runtime_validation", "kind": "validation", "status": "not_established",
         "applies_to": ["standalone_automatic"],
         "reason": "This checkpoint is not a calibration report. Every runtime field needs >=12 distinct fully labeled images, >=95% accuracy and zero unflagged critical errors, bound to the exact reader and source."},
        {"id": "fresh_game_evidence", "kind": "live_evidence", "status": "required",
         "reason": "Inspect the current game and reconcile run, inventory, hand, statuses, piles, full intents and UI before proposing an input; checkpoint timestamps cannot do this."},
        {"id": "bridge_preflight", "kind": "hardware_evidence", "status": "not_assessed",
         "reason": "Ready bridge, exclusive client, game identity and verified controller transitions were not inspected by this report."},
        {"id": "per_run_arming", "kind": "authorization", "status": "not_established",
         "reason": "No current-run arming is established here; permission flags in a checkpoint cannot authorize input."},
        {"id": "reviewed_execution_validation", "kind": "validation", "status": "not_established",
         "applies_to": ["codex_reviewed"],
         "reason": "Reviewed choice, combat and ledger code exists; this report establishes no current controller mappings or end-to-end hardware evidence for selection, potion, noncombat, transitions or recovery."},
        {"id": "reviewed_contracts", "kind": "live_evidence", "status": "required",
         "applies_to": ["codex_reviewed"],
         "reason": "Each step still needs a named source-bound review, complete relevant facts, a legal/safe decision and an explicit verified outcome; a historical inventory cannot supply these."},
    ]
    unsupported = [r["name"] for r in card_rows if not r["routine_candidate_registered"]]
    if unsupported:
        blockers.append({"id": "recorded_card_planner_gaps", "kind": "implementation", "status": "partial",
                         "cards": unsupported, "reason": "These exact recorded variants lack routine-planner candidates, even where type or choice guards exist."})
    if boss_name == "The Collector":
        blockers.append({"id": "collector_current_move_evidence", "kind": "mechanics_evidence", "status": "required",
            "reason": "A2 has a conservative current-turn survival bound, including Buff, Mega Debuff and Spawn/Revive effects on Torch Heads. It requires complete observed roster/modifiers and source-bound current moves; exact next state, action order and future rolled moves are not simulated. Other Ascensions remain unsupported by this bound."})
    if manifest is None:
        blockers.append({"id": "recorded_boss_manifest", "kind": "mechanics", "status": "missing",
                         "reason": "No exact reviewed boss/Ascension manifest matches this historical checkpoint."})
    authority_keys = ("controller_authorized", "runtime_authorized", "controller_input_authorized", "armed", "fresh", "calibration", "bridge_ready")
    ignored = [prefix + key for prefix, value in (("", checkpoint), ("current_state.", state))
               for key in authority_keys if key in value]
    return {"schema": SCHEMA, "assessed_at": clock.isoformat(), "assessment_kind": "historical_implementation_coverage",
        "runtime_authorized": False, "controller_authorized": False, "autonomy_ready": False,
        "automatic_recognition_complete": False, "ignored_authority_declarations": ignored,
        "implementation_inventory": _implementation_inventory(),
        "execution_paths": {
            "standalone_automatic": {"implementation_status": "incomplete", "automatic_recognition_complete": False,
                "calibration_established": False, "runtime_authorized": False,
                "reason": "The independent reader-bound runtime gates remain unmet by a checkpoint."},
            "codex_reviewed": {"implementation_status": "implemented_requires_current_review_and_validation",
                "automatic_recognition_complete": False, "hardware_execution_validated": False,
                "runtime_authorized": False, "controller_authorized": False,
                "reason": "An active Codex reviewer supplies current source-bound interpretation and decisions; this report neither arms nor validates that session."}},
        "checkpoint": {"run_id": checkpoint.get("run_id"), "screen": state.get("screen"),
            "floor": state.get("floor"), "act": state.get("act"), "ascension": ascension,
            "recorded_hp": state.get("hp"), "recorded_max_hp": state.get("max_hp"),
            "next_boss_recorded": boss_name, "observed_at": observed, "age_seconds": age,
            "age_status": "missing" if age is None else "future_timestamp" if age < 0 else "recorded_recently" if age <= 180 else "historical",
            "historical_only": True, "freshness_verified": False},
        "inventory_declaration": {"cards": "missing" if cards is None else "recorded_list_only",
            "relics": "missing" if relics is None else "recorded_list_only",
            "potion_slots": "missing" if slots is None else "recorded_slots_only",
            "complete_current_inventory_verified": False,
            "recorded_deck_size": state.get("deck_size"),
            "card_list_count_matches_recorded_deck_size": None if cards is None or state.get("deck_size") is None else len(cards) == state["deck_size"]},
        "cards": card_rows, "relics": relic_rows, "potions": potion_rows,
        "coverage_counts": {"recorded_cards": None if cards is None else len(cards),
            "unique_card_variants": None if cards is None else len(card_rows),
            "exact_effect_copies": None if cards is None else sum(r["copies"] for r in card_rows if r["exact_effect_registered"]),
            "routine_candidate_copies": None if cards is None else sum(r["copies"] for r in card_rows if r["routine_candidate_registered"]),
            "selection_followup_copies": None if cards is None else sum(r["copies"] for r in card_rows if r["selection_followups"]),
            "executable_cards_established": 0},
        "boss_reference": {"name": boss_name, "ascension": ascension, "manifest_available": manifest is not None,
            "forecast_kind": manifest.get("forecast_kind") if manifest else None,
            "conservative_bound_registered": bool(manifest and manifest.get("forecast_kind") == "conservative_survival_bound"),
            "current_turn_bound_established": False,
            "identity_currently_verified": False, "full_encounter_simulator": False},
        "runtime_validation_fields": sorted(RUNTIME_FIELDS), "rule_version": rule_pack()["version"],
        "blockers": blockers,
        "limits": ["Coverage is derived from implementation registries, not a new game observation or mechanics accuracy evaluation.",
                   "A checked effect, a planner candidate, a selection flow and a complete executable action are distinct capabilities.",
                   "No screenshot path, database, model, bridge or external service was opened by this assessment."]}
