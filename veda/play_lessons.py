"""Bounded read-only retrieval of verified historical decisions, not model training."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3

SCHEMA = "veda.play-lessons.v1"
DEFAULT_DATABASE = Path("artifacts/veda-memory.sqlite3")
MAX_CANDIDATES = 256
MAX_JSON_BYTES = 262_144
MAX_OUTPUT_BYTES = 24_000
CONTEXT_KEYS = ("run_id", "floor_id", "combat_id", "turn_id")


def _object(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_JSON_BYTES:
        raise ValueError("oversized_or_missing_json")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def finite(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("nonfinite_json")
        return value

    value = json.loads(raw, object_pairs_hook=unique, parse_float=finite,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
    if not isinstance(value, dict):
        raise ValueError("nonobject_json")
    return value


def _norm(value):
    return re.sub(r"[^\w+]+", " ", value.casefold()).strip() if isinstance(value, str) else ""


def _short(value, limit=200):
    return value[:limit] if isinstance(value, str) else None


def _strings(values, limit=6):
    return [_short(v) for v in values[:limit] if isinstance(v, str)] if isinstance(values, list) else []


def _source(value):
    if not isinstance(value, dict):
        return {}
    return {k: _short(value[k], 4096 if k == "path" else 128)
            for k in ("path", "sha256", "captured_at", "origin") if isinstance(value.get(k), str)}


def _moment(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("missing_timezone")
    return parsed


def _state_summary(value):
    resources = value.get("resources", value)
    if not isinstance(resources, dict):
        resources = {}
    result = {k: resources[k] for k in ("hp", "max_hp", "block", "energy", "gold")
              if isinstance(resources.get(k), (int, float)) and not isinstance(resources[k], bool)}
    enemies = value.get("enemies")
    if isinstance(enemies, list):
        result["enemies"] = [{k: (_short(enemy[k], 80) if isinstance(enemy[k], str) else enemy[k])
                              for k in ("id", "name", "hp", "block")
                              if isinstance(enemy.get(k), (str, int, float)) and not isinstance(enemy[k], bool)}
                             for enemy in enemies[:5] if isinstance(enemy, dict)]
    return result


def _action_summary(action, state):
    requested = action.get("requested_action", action)
    if not isinstance(requested, dict):
        requested = {}
    result = {k: _short(action[k], 100) for k in ("kind", "step_kind", "inspection")
              if isinstance(action.get(k), str)}
    result.update({k: _short(requested[k], 100) for k in ("card_id", "card_name", "name", "target")
                   if isinstance(requested.get(k), str)})
    hand = state.get("hand", [])
    if result.get("card_id") and isinstance(hand, list):
        matches = [card for card in hand if isinstance(card, dict) and card.get("id") == result["card_id"]]
        if len(matches) == 1 and isinstance(matches[0].get("name"), str):
            result["card_name"] = _short(matches[0]["name"], 100)
    choice = action.get("choice")
    if isinstance(choice, dict):
        result["option_ids"] = _strings(choice.get("option_ids"), 4)
    return result


def _comparison(prediction, actual, payload):
    learning = actual.get("learning")
    if not isinstance(learning, dict):
        learning = payload.get("durable_verified_evidence", {})
    if not isinstance(learning, dict):
        learning = {}
    assessment = learning.get("assessment", prediction.get("assessment", {}))
    if not isinstance(assessment, dict):
        assessment = {}
    raw = learning.get("observed_mismatches")
    deltas = []
    if isinstance(raw, list):
        for delta in raw[:16]:
            if not isinstance(delta, dict) or not isinstance(delta.get("field"), str):
                continue
            if all(delta.get(k) is None or type(delta.get(k)) in (int, float) for k in ("expected", "observed")):
                deltas.append({"field": _short(delta["field"], 100),
                               "expected": delta.get("expected"), "observed": delta.get("observed")})
            elif 'expected' in delta and 'observed' in delta:
                deltas.append({"field": _short(delta["field"], 100), "values_omitted": True,
                               "basis": "Structured/text mismatch retained in the linked outcome evidence."})
    return {"status": "mismatch" if deltas else "no_recorded_mismatch" if raw == [] else "unavailable",
            "mismatches": deltas, "mismatches_truncated": isinstance(raw, list) and len(raw) > 16,
            "forecast_available": isinstance(prediction.get("forecast"), dict),
            "forecast_status": _short(assessment.get("forecast_status"), 100),
            "decision_policy": _short(learning.get("decision_policy", prediction.get("decision_policy")), 50),
            "warnings": _strings(assessment.get("warnings"))}


def _confirmed_case(row):
    actual = _object(row["actual_outcome_json"])
    action = _object(row["chosen_action_json"])
    before = _object(row["state_json"])
    prediction = _object(row["prediction_json"])
    after = _object(row["outcome_state"])
    payload = _object(row["outcome_payload"])
    request, receipt = payload.get("request"), payload.get("receipt")
    if actual.get("status") != "verified" or not isinstance(request, dict) or not isinstance(receipt, dict):
        raise ValueError("outcome_not_verified")
    context = {k: row[k] for k in CONTEXT_KEYS}
    if (row["outcome_kind"] != "play_outcome" or actual.get("event_id") != row["outcome_id"]
            or any(row["outcome_" + key] != value for key, value in context.items())
            or request.get("context") != context or receipt.get("context") != context
            or request.get("decision_id") != row["id"] or receipt.get("decision_id") != row["id"]
            or receipt.get("event_id") != row["outcome_id"]
            or request.get("status") != "verified" or receipt.get("status") != "verified"
            or receipt.get("kind") != "outcome" or receipt.get("unresolved") is not False
            or actual.get("state") != after or request.get("state") != after
            or actual.get("source") != request.get("source") or receipt.get("source") != request.get("source")):
        raise ValueError("outcome_link_not_confirmed")
    source = actual.get("source", {})
    if (not isinstance(source, dict) or not isinstance(source.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", source["sha256"])
            or not isinstance(source.get("path"), str)
            or len(source["path"]) > 4096
            or row["outcome_screenshot"] != source["path"]
            or source.get("captured_at") != row["outcome_observed_at"]
            or _moment(row["outcome_observed_at"]) <= _moment(row["observed_at"])):
        raise ValueError("outcome_source_not_confirmed")
    summary = _action_summary(action, before)
    if not summary.get("kind"):
        raise ValueError("missing_chosen_action")
    ui = before.get("ui", {})
    screen = ui.get("screen", row["phase"]) if isinstance(ui, dict) else row["phase"]
    enemies = before.get("enemies", [])
    enemy_names = [row["encounter_name"]] if row["encounter_name"] else []
    if isinstance(enemies, list):
        enemy_names.extend(enemy.get("name") for enemy in enemies[:16] if isinstance(enemy, dict) and enemy.get("name"))
    comparison = _comparison(prediction, actual, payload)
    uncertainties = list(comparison["warnings"])
    if not comparison["forecast_available"]:
        uncertainties.append("No structured forecast was recorded for this action.")
    if comparison["status"] == "unavailable":
        uncertainties.append("No structured forecast comparison is available; legacy notes are not parsed as deltas.")
    return {"case_id": row["id"], "decision_event_id": row["event_id"], "outcome_event_id": row["outcome_id"],
            "context": {**context, "ascension": row["ascension"], "screen": _short(screen, 80),
                        "encounters": list(dict.fromkeys(_strings(enemy_names, 6)))},
            "action": summary, "reasoning": _short(row["reasoning"], 240),
            "before": _state_summary(before), "observed_after": _state_summary(after),
            "comparison": comparison, "uncertainties": uncertainties[:8],
            "evidence": {"decision_screenshot_path": row["screenshot_path"],
                         "outcome_source": _source(source), "observed_at": row["outcome_observed_at"],
                         "note": _short(actual.get("evidence_note"), 240),
                         "basis": "Resolved decision linked to a verified play_outcome receipt; source files not re-inspected."}}


def read_play_lessons(database=DEFAULT_DATABASE, *, run_id=None, screen=None, enemy=None,
                      card=None, action=None, limit=5):
    """Read at encounter/decision boundaries; run_id filters exactly, omitted searches all runs.

    Supplied context filters are conjunctive and match normalized complete names.
    At most 256 recent resolved candidates are examined; this is not exhaustive
    history, causal evidence, live state, or permission to repeat an old action.
    """
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("lesson limit must be between 1 and 10")
    filters = {"run_id": run_id, "screen": screen, "enemy": enemy, "card": card, "action": action}
    if any(value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 128)
           for value in filters.values()):
        raise ValueError("lesson filters must be nonempty text of at most 128 characters")
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError("lesson database does not exist")
    bounded = lambda column, alias: f"CASE WHEN length({column})<={MAX_JSON_BYTES} THEN {column} END AS {alias}"
    columns = [bounded("d." + key, key) for key in ("actual_outcome_json", "chosen_action_json", "prediction_json")]
    columns += [bounded("e.state_json", "state_json"), bounded("oe.state_json", "outcome_state"),
                bounded("oe.payload_json", "outcome_payload")]
    sql = """SELECT d.id,d.event_id,substr(d.reasoning,1,240) AS reasoning,e.run_id,e.floor_id,e.combat_id,e.turn_id,
        e.phase,e.observed_at,CASE WHEN length(e.screenshot_path)<=4096 THEN e.screenshot_path END AS screenshot_path,
        r.ascension,c.encounter_name,
        oe.id AS outcome_id,oe.kind AS outcome_kind,oe.run_id AS outcome_run_id,oe.floor_id AS outcome_floor_id,
        oe.combat_id AS outcome_combat_id,oe.turn_id AS outcome_turn_id,oe.observed_at AS outcome_observed_at,
        oe.screenshot_path AS outcome_screenshot,""" + ",".join(columns) + """
        FROM decisions d JOIN evidence_events e ON e.id=d.event_id JOIN runs r ON r.id=e.run_id
        LEFT JOIN combats c ON c.id=e.combat_id
        LEFT JOIN evidence_events oe ON oe.id=CASE WHEN length(d.actual_outcome_json)<=262144
            AND json_valid(d.actual_outcome_json) THEN json_extract(d.actual_outcome_json,'$.event_id') END
        WHERE d.status='resolved'""" + (" AND e.run_id=?" if run_id else "") + " ORDER BY e.observed_at DESC,d.id DESC LIMIT ?"
    params = ([run_id] if run_id else []) + [MAX_CANDIDATES + 1]
    excluded = Counter(); cases = []; examined = 0; truncated = False
    db = None
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        budget = 0

        def bounded_work():
            nonlocal budget
            budget += 1
            return budget > 4000

        db.set_progress_handler(bounded_work, 1000)
        for row in db.execute(sql, params):
            if examined == MAX_CANDIDATES:
                truncated = True
                break
            examined += 1
            try:
                case = _confirmed_case(row)
            except (ValueError, TypeError, KeyError, RecursionError):
                excluded["unconfirmed_or_malformed"] += 1
                continue
            if case["action"]["kind"] == "navigation" and _norm(action) != "navigation":
                excluded["navigation_only"] += 1
                continue
            names = {"screen": [case["context"]["screen"]], "enemy": case["context"]["encounters"],
                     "card": [case["action"].get(k) for k in ("card_name", "card_id", "name")]
                              if case["action"]["kind"] == "card" else [],
                     "action": [case["action"].get(k) for k in ("kind", "step_kind", "inspection")]}
            if any(value is not None and _norm(value) not in {_norm(item) for item in names[key]}
                   for key, value in filters.items() if key != "run_id"):
                excluded["not_relevant"] += 1
                continue
            case["relevance"] = [key for key, value in filters.items() if value is not None] or ["recent_verified_outcome"]
            cases.append(case)
    except sqlite3.Error as error:
        raise ValueError("lesson retrieval unavailable: database unreadable, incompatible, busy, or query budget exceeded") from error
    finally:
        if db is not None:
            db.close()
    # Stable sorting preserves recency within each comparison category.
    cases.sort(key=lambda case: case["comparison"]["status"] != "mismatch")
    result = {"schema": SCHEMA, "status": "ok", "historical_only": True, "live": False,
              "runtime_authorized": False, "controller_authorized": False, "model_weights_updated": False,
              "filters": filters, "cases": cases[:limit],
              "search": {"candidates_examined": examined, "candidate_limit": MAX_CANDIDATES,
                         "candidate_window_truncated": truncated, "relevant_cases_in_window": len(cases),
                         "excluded": dict(excluded), "output_truncated": len(cases) > limit,
                         "ranking": "Relevant recorded forecast mismatches first, then most recent evidence."},
              "limitations": ["Historical retrieval only; one observed result does not prove an action caused it.",
                              "Recheck current cards, relics, potions, enemy state and ascension before applying a case.",
                              "No recorded mismatch does not establish a complete or accurate forecast."]}
    while len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_OUTPUT_BYTES and result["cases"]:
        result["cases"].pop()
        result["search"]["output_truncated"] = True
    return result
