"""Names of supported play helpers, for preflight validation only."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_HELPERS = frozenset({
    "veda_arm.py", "veda_combat.py", "veda_campfire.py", "veda_loot.py",
    "veda_map_step.py", "veda_menu.py", "veda_shop.py", "veda_submit.py",
    "veda_recovery_watch.py", "veda_play_clock.py",
})


def helper_path(name: str) -> Path:
    """Resolve a supported helper and reject nonexistent ad-hoc scripts."""
    basename = Path(str(name)).name
    if basename not in SUPPORTED_HELPERS:
        raise ValueError(f"unsupported VEDA helper: {basename}")
    path = ROOT / "scripts" / basename
    if not path.is_file():
        raise ValueError(f"supported VEDA helper is missing: {basename}")
    return path
