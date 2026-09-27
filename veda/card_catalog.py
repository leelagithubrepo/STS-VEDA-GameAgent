"""Grounded display titles for offline recognition, not gameplay support.

This deliberately small catalogue is independent of the effect calculator and
of any run's inventory. Entries record existing local references or inspected
archived pixels. A recognised display title grants no effect, cost, hand
membership, or runtime authority. Unlisted variants remain unknown.
"""
from __future__ import annotations


# Explicit variants only: do not manufacture '+' entries from base names.
_SOURCE_GROUPS = (
    ("veda/advisory.py:DIRECT", (
        "Strike", "Strike+", "Defend", "Defend+", "Iron Wave", "Iron Wave+",
        "Carnage", "Carnage+", "Body Slam", "Body Slam+",
    )),
    ("veda/advisory.py:REVIEWED_SPECIAL_CARDS", (
        "Anger", "Hemokinesis", "Hemokinesis+", "Bash", "Bash+", "Clothesline",
        "Headbutt", "Shrug It Off", "Shrug It Off+", "Slimed",
    )),
    ("veda/advisory.py:BOUNDARIES", (
        "Headbutt", "True Grit", "Dual Wield", "Armaments", "Offering",
        "Shrug It Off", "Bloodletting", "Pommel Strike", "Battle Trance",
        "Second Wind", "Reckless Charge", "Corruption", "Barricade",
        "Feel No Pain", "Inflame", "Shockwave", "Disarm", "Bash", "Clothesline",
        "Panic Button", "Burning Pact",
    )),
    ("data/spire_advisory_rules.json:rules/true-grit", ("True Grit", "True Grit+")),
    ("data/act1_reference_pack.json:claims", (
        "Pommel Strike", "Pommel Strike+", "Shrug It Off", "Shrug It Off+",
        "Combust", "Combust+",
    )),
    ("data/act1_current_deck_research.json:claims", (
        "Fiend Fire", "Fiend Fire+", "Perfected Strike", "Cleave",
    )),
    ("data/teaching_run_2026-09-02.json:card_reward", ("Warcry",)),
    ("artifacts/observations/ps5_observation_20260926T135731Z.png", (
        "Uppercut", "Metallicize",
    )),
    ("artifacts/observations/ps5_observation_20260926T142413Z.png", ("Wound",)),
)

TITLE_SOURCES: dict[str, tuple[str, ...]] = {}
for _source, _titles in _SOURCE_GROUPS:
    for _title in _titles:
        TITLE_SOURCES[_title] = (*TITLE_SOURCES.get(_title, ()), _source)

DISPLAY_TITLES = frozenset(TITLE_SOURCES)
_CASE_ONLY = {title.lower(): title for title in DISPLAY_TITLES}
if len(_CASE_ONLY) != len(DISPLAY_TITLES):
    raise ValueError("display catalogue contains ambiguous case variants")


def canonical_title(raw_text: str) -> str | None:
    """Match letters ignoring case only; retain spaces, punctuation and '+'."""
    if not isinstance(raw_text, str) or not raw_text.isascii():
        return None
    return _CASE_ONLY.get(raw_text.lower())
