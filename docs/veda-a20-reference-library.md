# Slay the Spire reference library through Ascension 20

VEDA now has a local reference library for the original game across Ironclad,
Silent, Defect and Watcher. It helps answer what an item does and how an enemy
changes with Ascension. The current run still needs fresh screen evidence for
the actual hand, piles, costs, potions, relic counters and enemy intent.

You can ask: “Use the A20 reference library to check this hand, our relics and
potions, and the next enemy; tell me any uncertainty.”

## What is included

| Area | Reference coverage |
| --- | --- |
| Cards | 370 canonical card identities, including generated cards, choices, curses, statuses and character-specific Strike/Defend; base and first-upgrade references |
| Relics | 180 identities, including special and Endless-mode relics |
| Potions | 42 real potions; consumption and automatic-use differences are explicit |
| Enemies | 65 identities/variants from a pinned community manifest, including summons and Act 4; HP/attack candidates and behavior notes |
| Ascension | A0 baseline and every cumulative modifier through A20 |
| Baalorlord | 14 original-game videos, four A20 character playlists, two written guides and six principles from inspected written sections |

These counts describe the reference manifests. They do not establish that every
interaction, console version, enemy probability or card effect is simulated or
recognized from pixels. Disputed facts stay marked as conflicts; missing values
stay unknown. A complete list of names is a different milestone from reliable
autonomous play.

The card total includes four character-specific Strike/Defend pairs and generated
choices. It is not a count of cards obtainable from ordinary reward screens.
The enemy total includes distinct variants and summons, not 65 encounter types.

## Sources and uncertainty

The catalogs record exact source links and pinned revisions where available.
They combine the community-maintained
[Spire Archive](https://github.com/nkhoit/spire-archive), the MIT-licensed
[sts_lightspeed implementation](https://github.com/gamerpuppy/sts_lightspeed),
selected community references and
[Baalorlord's original-game materials](https://www.youtube.com/@Baalorlord).
Community code is evidence of its own implementation, not official Mega Crit
code. Cross-checking found stale rows, incomplete upgrades and simulator bugs.
Conflicts are retained so an apparently precise number cannot silently become
a trusted game rule.

Video titles, authors, dates and descriptions were inspected; the videos were
not watched or transcribed. The two written guides were read in the relevant
sections. Their advice is conditional strategy, not a fixed card ranking or
action order. Their revision dates are unknown. Current creator shortcuts that
point to Slay the Spire 2 are excluded. Mutable playlists need their individual
entries checked before new claims are adopted.

No source here establishes the installed PlayStation patch. For a disputed
interaction, the visible game and a separately reviewed rule must resolve the
question before VEDA relies on it for survival or lethal damage.

## How this helps during a run

Load the current Ascension rules once when opening a play session. Retrieve
references for the observed hand, relevant relics, available potions and current
enemy together. Keep those references in the session and fetch only new or
changed names. There is no need to search the internet or load every card on
each turn.

At each decision, compare the actual draw/discard/exhaust piles, energy,
incoming damage and setup needs. Evaluate potion use before accepting HP loss.
With Runic Pyramid and Corruption, account for the remaining Skills and whether
Barricade setup is timely. These are conditional checks against the current
state, not an unconditional instruction to play a particular card first.

At A17–A19, look up the tougher behavior for the relevant enemy class. A20 also
requires preparing for a second, different Act 3 boss. The Ascension query
returns every lower modifier as well; A20 is not just one extra boss rule.

The existing checked combat tools still determine which interactions they can
validate. Importing this library does not expand their recognition or numeric
forecast coverage. The automated-play readiness checklist remains separate in
[the automation readiness guide](veda-automation-readiness.md).

## Local storage and lookup

Reviewed catalogs live in `data/spire_reference/`. Their reproducible SQLite
cache is `artifacts/veda-reference.sqlite3`. Current-run telemetry stays in
`artifacts/veda-memory.sqlite3`; this importer refuses to replace a non-reference
database. Lookup operations open the reference cache read-only.

Build after a reviewed catalog update, outside live combat:

```sh
python3 scripts/veda_reference.py build
python3 scripts/veda_reference.py coverage
```

Use the exact card upgrade and character when relevant:

```sh
python3 scripts/veda_reference.py lookup 'True Grit+' --kind card --character ironclad
python3 scripts/veda_reference.py lookup 'Runic Pyramid' --kind relic
python3 scripts/veda_reference.py lookup 'Energy Potion' --kind potion
python3 scripts/veda_reference.py lookup 'Time Eater' --kind enemy
python3 scripts/veda_reference.py ascension 20
python3 scripts/veda_reference.py search baalorlord
```

`Strike` without a character stays ambiguous. `True Grit+` selects the upgraded
reference; it does not fall back to base True Grit. Searing Blow beyond its first
upgrade and other run-dependent values require observation and a reviewed rule.
Printed reference costs are not the current cost after combat modifiers.

For a batch, save a request such as this to a local JSON file and pass its path
to `python3 scripts/veda_reference.py context REQUEST.json`:

```json
{
  "ascension": 20,
  "references": [
    {"name": "True Grit+", "kind": "card", "character": "ironclad"},
    {"name": "Runic Pyramid", "kind": "relic"},
    {"name": "Energy Potion", "kind": "potion"},
    {"name": "Time Eater", "kind": "enemy"}
  ]
}
```

Batch output deduplicates entries and sources, omits duplicate extraction fields,
and preserves conflicts and unknowns. Exact lookup retains the full audit record.
Oversized requests fail explicitly instead of silently losing facts. Source
revision hashes let a session notice a library update.

## Remaining work toward reliable A20 play

Resolve disputed high-impact mechanics with independent evidence, confirm the
console version, and add focused checked rules and visual examples for the next
encounters. Validate each supported interaction before extending automatic play.
The library makes relevant research easier to retrieve; it does not by itself
prove shorter floors, correct perception, or an A20 win rate.
