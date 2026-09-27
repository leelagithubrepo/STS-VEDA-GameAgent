# Keeping inspected combat evidence consistent

VEDA can combine verified inspections and check a proposed play against them.
It records which facts came from the saved-image reader and which were supplied
by a reviewer. This closes a state-management gap; it does not make the partial
image reader a complete autonomous observer.

The record covers player resources, the complete hand and its order, every
enemy and intent, statuses, screen phase and focus, piles, and inventory. Each
section is current, partial, missing, stale, or conflicting. Unknown information
stays unknown. Only an empty list in a complete, verified field establishes
absence; an empty partial inventory or hand does not.

## Use the checked evidence path

`scripts/check_combat_evidence.py` accepts a JSON bundle containing `journal`
and `source_files`. The journal comes from `EvidenceLedger.dump()`; source_files
maps each screenshot SHA-256 to its saved PNG path. Relative image paths resolve
beside the bundle. Every observation and inventory receipt must reference one of
those images. The checker verifies the images before and after assembling advice.

```sh
python scripts/check_combat_evidence.py /absolute/path/evidence.json \
  --plan /absolute/path/next-play.json
```

Normal review requires evidence at most three minutes old. For an archived
sequence, explicitly supply its historical review time:

```sh
python scripts/check_combat_evidence.py /absolute/path/evidence.json \
  --plan /absolute/path/next-play.json \
  --replay-as-of 2026-09-22T19:00:00+00:00
```

Replay evaluates the recorded situation at that time. It does not establish
what is on screen now. Exit status 0 means the required context is available
and all required inspections are satisfied and the supplied plan passed the
shared advisory check (`advisory_ready: true`). A plan can pass a legality check
while still needing a requested setup inspection; that result is not ready.
Status 2 means evidence
or plan requirements remain unresolved. A malformed bundle/source also exits 2
with an error. Neither status permits controller input.

The result includes the context, relevant reviewed rules, checked plan, evidence
origins, and prioritized inspection requests. `incoming_displayed` is published
only after a complete current roster and explicit per-hit values are available.
The number sums every living enemy's displayed hits. It is not an end-turn
survival forecast; the existing tactical checker handles Block, counters and
supported effects separately.

## Continue through SQLite advice

When the inspection bundle is complete and current, import it into the existing
private memory workflow:

```sh
python scripts/veda_memory.py combat-evidence-import \
  --bundle /absolute/path/evidence.json --source 'Verified current inspections'
python scripts/veda_memory.py combat-context --combat-id actual-combat-id
```

The import checks screenshot identities, the current open combat/turn, Ascension,
encounter and inventory against SQLite. Record confirmed inventory changes in
SQLite first; the bundle cannot silently replace that history. It retains the
inspection journal, screenshot sources and reader/reviewer origins with the
snapshot. Continue with `advice-check`; supply the returned snapshot ID when
recording `advice-decide`. Any later logged inventory/action/turn change makes
that snapshot stale. Archived replay bundles cannot be imported as current play.

## Record observations without filling gaps

To create a first bundle from an actual saved-reader output, run the following
from the repository with Python. Replace the paths, context identifiers and
observation time with the matching evidence record; the placeholders are not
game facts. `reading.json` is the JSON produced by `read_saved_frame.py`.

```python
import json
from pathlib import Path
from veda.evidence_advisory import partial_observation
from veda.evidence_ledger import EvidenceLedger

reading = json.loads(Path('/absolute/path/reading.json').read_text())
ledger = EvidenceLedger('actual-run-id', 'actual-floor-id', 'actual-combat-id', 'actual-turn-id')
ledger.observe(partial_observation(
    reading, context=ledger.context, epoch=ledger.epoch,
    observed_at='2026-09-22T19:00:00+00:00'))  # replace with the actual observation time
bundle = {'journal': ledger.dump(),
          'source_files': {reading['image_sha256']: reading['image_path']}}
Path('/absolute/path/evidence.json').write_text(json.dumps(bundle, indent=2))
```

Checking this first partial bundle is expected to return exit status 2,
`advisory_ready: false`, no complete combat state, and a
`current_screen_and_focus` inspection request. Add verified observations using
`ledger.observe(packet)` before saving the next bundle. Reload an existing
journal with `EvidenceLedger.from_dict(bundle['journal'])`; do not discard its
prior evidence merely to replace a contradictory reading.

Use `EvidenceLedger(run_id, floor_id, combat_id, turn_id)` from
`veda.evidence_ledger`. Its observation packet has:

- The exact context, current epoch, frame ID, screenshot hash, and capture or
  verified observation time. A processing timestamp is not a capture timestamp.
- An origin with `kind` (`reader` or `reviewer`), named `source`, `evidence_ref`,
  and `verified: true`. This is the upstream source's declaration, not an
  automatic certification produced by the ledger.
- Named sections with `data`, `complete`, and a nonempty
  `completeness_evidence` explanation when complete.

`partial_observation()` in `veda.evidence_advisory` imports known HP, energy and
Block from a successful saved-reader result. It never turns card candidates
into hand members or partial attack numbers into all incoming damage. Complete
hand, enemy, status and focus evidence must come from additional verified
inspection. Screenshot hashes establish file identity; they cannot prove that
a human annotation is correct.

Hand cards need stable IDs, name, observed cost, upgrade/color, type, playability,
and an explicitly verified order. Enemies need stable IDs, name, HP, Block,
literal intent and per-hit damage, plus target order. Enemy data also records
`encounter_type` (`enemy`, `elite`, or `boss`) and `encounter_name`; boss checks
require the reviewed manifest at the observed Ascension. Status coverage must
match every enemy ID, including confirmed absence of effects.

A partial pile page does not establish a complete pile. Per-zone `coverage`
and `zone_evidence` can establish a complete draw or discard pile while other
zones remain unknown. The advisory adapter exposes only complete zones.
An unordered draw-pile browser never establishes draw order.

Inventory receipts import the existing inventory ledger's observed after-state,
with coverage for cards, relics and potions. Ordinary combat advice requires
complete relic and potion coverage; it does not demand a full permanent-deck
inspection for every play. A verified inventory transition includes the prior
inventory digest and event ID, invalidates volatile combat facts and requires a
new observation. Importing a receipt does not itself write a game event to SQLite.

## Invalidate after change

After a known card/turn action, call `invalidate(reason=..., kind="input")`.
After potion use or another inventory change use `inventory_change`, or import
the verified inventory transition. For any unlogged or uncertain physical
action use `unknown_input`, which invalidates inventory too. Context changes
invalidate combat facts; a different run invalidates inventory as well.

Combining two screenshots requires an explicit continuity record naming the
previous frame, `state_unchanged: true`, and its evidence reference. Matching
turn numbers alone are insufficient. A new frame always requires current UI
focus evidence. Conflicting known facts stay unavailable until a new observation
boundary; another assertion cannot silently overwrite them.

The ledger cannot detect physical controller activity that was never reported.
That limitation must be handled by the live observer before autonomous use.

## Tactical checks that use this record

The same checker used by SQLite advice now enforces potion review before a
damaging End Turn, an HP-cost card, or Time Eater's twelfth card. When usable
potions exist, provide a nonempty reason for each in `potion_review`, keyed by
potion name. For example:

```json
{
  "steps": [{"kind": "end_turn"}],
  "potion_review": {
    "Energy Potion": "No useful playable cards remain in the verified hand.",
    "Power Potion": "Retain for the boss; this checked hit is survivable."
  }
}
```

Those example reasons are not a recommendation for a particular fight. Drinking
a potion ends the checked sequence and requires another observation. Fairy in
a Bottle is automatic and cannot be recommended as a drink; it is not counted
in the current survival forecast.

Other checks retain their existing evidence dependencies: True Grit+ requires
an eligible remaining hand card, Headbutt requires the complete current discard
pile, Dual Wield requires a suitable target and hand space, and Corruption with
Runic Pyramid requires the draw pile and a Barricade setup comparison when
applicable. Playable zero-cost cards require review before End Turn. Current
energy, upgraded cost, Time Warp and Choker counters remain checked.

The deterministic planner still supports only its reviewed effects and encounters.
Unsupported interactions stop at an observation boundary; this work does not
certify autonomous boss strategy or permit running without calibration.

## Verification and timing

Ledger and advisory tests use explicitly synthetic observations to verify
invalidation, source mismatches, partial coverage and tactical dependencies.
They are not image-recognition accuracy tests. Saved-image recognition is
measured separately against archived screenshot labels.

The result reports source-file verification, ledger/context assembly, and
plan/final-verification times separately. These are not SQLite query time,
screen-capture latency, or full autonomous turn time.
