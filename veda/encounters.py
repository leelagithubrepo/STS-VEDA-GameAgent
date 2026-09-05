"""Source-backed encounter facts used to constrain, never invent, tactics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EncounterProfile:
    name: str
    kind: str
    act: int
    split_at_half_hp: bool
    intent_notes: tuple[str, ...]
    source_url: str


@dataclass(frozen=True)
class EncounterCheck:
    known: bool
    profile: EncounterProfile | None
    cautions: tuple[str, ...]


SLAY_THE_SPIRE_WIKI = "https://slaythespire.wiki.gg/wiki/Slimes"

# Keep this deliberately small and inspectable.  Unknown names remain unknown;
# a profile is not inferred from sprite shape or HP alone.
ACT1_PROFILES = {
    "Acid Slime (M)": EncounterProfile(
        "Acid Slime (M)", "enemy", 1, False,
        ("Corrosive Spit: 7 damage and a Slimed.", "Tackle: 10 damage.", "Lick: 1 Weak."),
        SLAY_THE_SPIRE_WIKI,
    ),
    "Acid Slime (L)": EncounterProfile(
        "Acid Slime (L)", "enemy", 1, True,
        ("Splits into two Acid Slime (M) at or below half HP.",),
        SLAY_THE_SPIRE_WIKI,
    ),
    "Spike Slime (L)": EncounterProfile(
        "Spike Slime (L)", "enemy", 1, True,
        ("Splits at or below half HP.",),
        SLAY_THE_SPIRE_WIKI,
    ),
    "Cultist": EncounterProfile(
        "Cultist", "enemy", 1, False,
        ("Starts with Incantation, then uses Dark Strike.",),
        "https://slaythespire.wiki.gg/wiki/Cultist",
    ),
    "Gremlin Nob": EncounterProfile(
        "Gremlin Nob", "elite", 1, False,
        ("Skills trigger Enrage; confirm the live intent before choosing a line.",),
        "https://slaythespire.wiki.gg/wiki/Gremlin_Nob",
    ),
    "Lagavulin": EncounterProfile(
        "Lagavulin", "elite", 1, False,
        ("Starts asleep; its live state must be read before action.",),
        "https://slaythespire.wiki.gg/wiki/Lagavulin",
    ),
    "Sentry": EncounterProfile(
        "Sentry", "elite", 1, False,
        ("Has Artifact; outer and middle Sentries alternate different moves.",),
        "https://slaythespire.wiki.gg/wiki/Sentry",
    ),
}


def check_encounter(name: str | None, *, act: int | None = None) -> EncounterCheck:
    if not name:
        return EncounterCheck(False, None, ("enemy name is not confirmed",))
    profile = ACT1_PROFILES.get(name)
    if profile is None or (act is not None and profile.act != act):
        return EncounterCheck(False, None, (f"no validated profile exists for {name}",))
    cautions = () if profile.split_at_half_hp else ("Do not predict a split for this confirmed enemy.",)
    return EncounterCheck(True, profile, cautions)
