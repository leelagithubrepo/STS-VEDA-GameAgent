"""Partial, offline image reading. Deliberately not an execution interpreter.

The caller supplies an inspected game viewport. HUD, combat and card evidence share
one immutable saved-image identity; neither a complete hand nor a playable
runtime state can be constructed by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import struct
import time
from typing import Any

from .native_ocr import NativeTextReader


SCHEMA = "veda.partial-saved-frame.v1"
_UNKNOWN_FIELDS = [
    "complete_hand", "unique_hand_count", "hand_order", "enemy_intents",
    "enemy_statuses", "player_block_and_statuses", "potions", "relics",
    "draw_discard_exhaust_piles", "screen_phase", "controller_focus", "target_order",
]


def _identity(path: Path) -> tuple[str, list[int]]:
    with path.open("rb") as stream:
        data = stream.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("image_too_large")
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("image_not_png")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height or width * height > 100_000_000:
        raise ValueError("image_dimensions_invalid")
    return hashlib.sha256(data).hexdigest(), [width, height]


def _viewport_valid(viewport: Any, dimensions: list[int]) -> bool:
    return (isinstance(viewport, (list, tuple)) and len(viewport) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in viewport)
            and 0 <= viewport[0] < viewport[2] <= dimensions[0]
            and 0 <= viewport[1] < viewport[3] <= dimensions[1])


def read_saved_frame(image_path: Path, *, viewport: list[float],
                     reader: NativeTextReader, expected_sha256: str | None = None,
                     refine_cards: bool = True, refine_energy: bool = True,
                     read_combat: bool = True, refine_combat: bool = True) -> dict[str, Any]:
    """Read an explicit archived PNG; unknown or conflicting fields stay null.

    ``ok`` only means the offline processing completed, not that a frame is
    complete, accurate enough to act on, or current. A failure anywhere in the
    immutable-source check clears all derived readings.
    """
    began = time.monotonic()
    path = Path(image_path).expanduser().resolve()
    result: dict[str, Any] = {
        "schema": SCHEMA, "ok": False, "partial": True,
        "runtime_authorized": False, "controller_authorized": False,
        "runtime_authorization_eligible": False,
        "processed_at": datetime.now(timezone.utc).isoformat(), "captured_at": None,
        "frame_id": path.stem, "image_path": str(path), "image_sha256": None,
        "source_dimensions": None, "viewport": None,
        "viewport_provenance": "caller supplied; not automatically verified",
        "hud": {"hp": None, "max_hp": None, "energy": None, "energy_max": None},
        "cards": {"card_candidates": [], "hand_complete": None},
        "unavailable_fields": list(_UNKNOWN_FIELDS), "issues": [], "timing_ms": {},
    }
    try:
        digest, dimensions = _identity(path)
        result.update(image_sha256=digest, source_dimensions=dimensions)
        if not _viewport_valid(viewport, dimensions):
            raise ValueError("invalid_explicit_viewport")
        result["viewport"] = list(viewport)
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                raise ValueError("invalid_expected_sha256")
            if expected_sha256 != digest:
                raise ValueError("unexpected_image_sha256")
        native = reader.observe(path, frame_id=path.stem, regions={"viewport": list(viewport)})
        result["timing_ms"]["native"] = native.get("timing_ms", {})
        if native.get("ok") is not True:
            raise ValueError("native_reader_failed:" + str(native.get("error", "unknown")))
        if (native.get("image_sha256") != digest or native.get("source_dimensions") != dimensions
                or native.get("image_path") != str(path) or native.get("frame_id") != path.stem):
            raise ValueError("native_source_mismatch")
        hud = native["hud"]
        energy = None
        if refine_energy:
            from .energy_refinement import refine_energy_reading
            energy = refine_energy_reading(path, viewport=list(viewport), native=native,
                                           reader=reader, frame_id=path.stem)
            hud = energy["hud"]
            result["timing_ms"]["energy_refinement"] = energy["refinement"]["timing_ms"]
        cards_began = time.monotonic()
        cards_available = True
        try:
            from .card_regions import detect_card_regions
            cards = detect_card_regions(path, viewport=list(viewport), observations=native["observations"])
        except ImportError:
            cards_available = False
            cards = {"card_candidates": [], "hand_complete": None,
                     "image_sha256": digest, "source_dimensions": dimensions,
                     "issues": ["card_analysis_dependency_unavailable"]}
            result["issues"].append("card_analysis_dependency_unavailable")
        result["timing_ms"]["card_analysis"] = (time.monotonic() - cards_began) * 1000
        if cards.get("image_sha256") != digest or cards.get("source_dimensions") != dimensions:
            raise ValueError("card_source_mismatch")
        if refine_cards and cards_available:
            from .card_refinement import refine_card_regions
            cards = refine_card_regions(path, viewport=list(viewport), cards=cards,
                                        reader=reader, frame_id=path.stem, discover_hand_titles=True)
            result["timing_ms"]["card_refinement"] = cards["refinement"]["timing_ms"]
        if cards.get("hand_complete") is not None and cards.get("hand_complete") is not False:
            raise ValueError("unsupported_hand_completeness_claim")
        combat = None
        if read_combat:
            combat_began = time.monotonic()
            try:
                from .combat_evidence import extract_combat_evidence
                if refine_combat:
                    from .combat_refinement import refine_combat_reading
                    combat_result = refine_combat_reading(path, viewport=list(viewport),
                        native=native, reader=reader, frame_id=path.stem)
                    combat = combat_result["combat_evidence"]
                    combat["refinement"] = combat_result["refinement"]
                else:
                    combat = extract_combat_evidence(path, viewport=list(viewport),
                                                     observations=native["observations"])
                if (combat.get("image_sha256") != digest or combat.get("source_dimensions") != dimensions
                        or combat.get("viewport") != list(viewport)):
                    raise ValueError("combat_source_mismatch")
                if _identity(path) != (digest, dimensions):
                    raise ValueError("image_changed_during_processing")
                from .intent_evidence import extract_intent_evidence, merge_intent_evidence
                intents = extract_intent_evidence(path, viewport=list(viewport),
                                                   observations=native["observations"],
                                                   ocr_preprocessing="original")
                if refine_combat:
                    for region in combat_result["refinement"]["intent_regions"]:
                        if region["ok"]:
                            focused_intents = extract_intent_evidence(path, viewport=list(viewport),
                                                                      observations=region["observations"],
                                                                      ocr_preprocessing=region["preprocessing"])
                            intents = merge_intent_evidence(intents, focused_intents)
                if (intents.get("image_sha256") != digest or intents.get("source_dimensions") != dimensions
                        or intents.get("viewport") != list(viewport)):
                    raise ValueError("intent_source_mismatch")
                combat["intent_evidence"] = intents
            except ImportError:
                result["issues"].append("combat_analysis_dependency_unavailable")
            if combat is not None and (combat.get("image_sha256") != digest
                                       or combat.get("source_dimensions") != dimensions
                                       or combat.get("viewport") != list(viewport)):
                raise ValueError("combat_source_mismatch")
            result["timing_ms"]["combat_analysis"] = (time.monotonic() - combat_began) * 1000
        from .saved_frame_coverage import describe_coverage
        coverage = describe_coverage(hud=hud, cards=cards, combat=combat)
        if _identity(path) != (digest, dimensions):
            raise ValueError("image_changed_during_processing")
        result.update(ok=True, hud=hud, cards=cards, coverage=coverage)
        if combat is not None:
            result["combat_evidence"] = combat
        result["combat_reading_mode"] = ("focused_combat" if refine_combat else "whole_image_only") if read_combat else "disabled"
        result["card_reading_mode"] = "focused_second_pass" if refine_cards else "single_pass"
        result["energy_reading_mode"] = "focused_energy" if refine_energy else "whole_image_only"
        if energy is not None:
            result["energy_refinement"] = energy["refinement"]
        result["native_evidence"] = {
            "engine": native.get("engine"), "recognition": native.get("recognition"),
            "observations": native["observations"],
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # No partially accepted state is exposed after a processing/source failure.
        result["issues"].append(str(exc) if isinstance(exc, ValueError) else "saved_image_processing_failed")
    result["timing_ms"]["total"] = (time.monotonic() - began) * 1000
    return result
