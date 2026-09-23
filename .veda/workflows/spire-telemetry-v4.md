# Spire telemetry operating workflow (schema v6)

This is a private, advisory-only logging workflow. It never sends game input,
starts an LLM review, or invents an unseen game state.

## Evidence is mandatory for future decisions

Spire—not the player—must create the private evidence record at each safe
meaningful boundary. Use `--capture` on `decision`, `route-snapshot`,
`map-snapshot`, `inventory-baseline`, and `floor-finish`. It passively saves a
timestamped PNG of the Mac display and links it to the matching SQLite record;
it never presses a game button. A pre-existing verified still may be used with
`--screenshot` instead. The commands reject a meaningful decision or completed
floor without one of those two forms of evidence.

If passive capture fails, the command now records a **written fallback** rather
than losing the turn: no screenshot path, `capture_status: unavailable`, and
confidence capped at 0.60. Continue logging observed state, options, reasoning,
action, and outcome; attach a supplied still later when available. Treat these
as lower-confidence evidence, never screenshot-backed proof. A floor with only
written fallback remains not review-ready in `floor-completeness`, but its
telemetry is retained for a later, qualified retrospective.

After every completed floor, run `floor-completeness --floor-id "$FLOOR_ID"`.
Do not call a floor retrospective review-ready until it reports
`"review_ready": true`. The report fails closed on a missing screenshot,
unresolved decision, open combat/turn, absent floor outcome, or missing combat
zone base.

## Starting a newly observed run

Use `run-new`, never `run`, when the player has truly started a new run. It
archives any matching active ledger run but retains its records. If logging
begins after the physical run has already started, create the run with metadata
that says which floor observation began; do not backfill earlier floors.

```zsh
python3 scripts/veda_memory.py run-new --ascension 1 --metadata '{"capture_started_at_floor":11,"earlier_floors_unlogged":true}'
```

At each visible floor entry, open `floor-start`. At exit, use `floor-finish`.

## Persistent run facts

Record every confirmed card, relic, and potion acquisition/removal/consumption
with `inventory-event`. Use `property_confirmed` only after the tooltip or
other named evidence was read. Preserve unknown relic or potion effects as
unknown instead of filling in a familiar effect from memory.

If observation begins mid-run, record the **current confirmed inventory** once
with `inventory-baseline`. This is not an acquisition history: it says only
that named items were present at this capture boundary. Set `coverage` for
each category to `complete`, `partial`, or `unknown`; never call the deck,
relics, or potions complete unless the whole relevant inventory was visible.

```zsh
python3 scripts/veda_memory.py inventory-baseline \
  --run-id "$RUN_ID" --floor-id "$FLOOR_ID" \
  --items '{"items":[{"kind":"relic","item":"Burning Blood"},{"kind":"potion","item":"Blessing of the Forge"}]}' \
  --coverage '{"card":"unknown","relic":"partial","potion":"partial"}' \
  --source "visible safe-boundary inventory" --confidence 0.99 --capture
```

Use `inventory-ledger` before inventory-dependent advice. It shows the current
confirmed items, their provenance, and category coverage. It never fills in
unseen cards or claims when a baseline item was acquired.

When the full map is visibly legible, save it with `map-snapshot`. This stores
only displayed nodes and displayed connections. Use `route-snapshot` separately
for the immediate legal next nodes used in a route recommendation.

## Combat state

For new recommendations follow [the checked advisory workflow](spire-advisory.md).
`combat-observe`, `combat-context`, and `advice-decide` replace free-form combat
decision logging. The low-level event API remains available for evidence history.


At combat entry:

1. Open `combat-start` under the current floor.
2. Open turn 1 with `turn-start`.
3. When the deck and opening hand are confirmed, use `combat-zones-start`.

After a confirmed card movement, append `combat-zone-event`: draw, play,
discard, exhaust, return, generate, or shuffle. Attach the current turn ID.
If a state-changing effect was not observed, append one `unknown` event with a
reason. Do not reconstruct past movements from a later hand.

Before advice that depends on a card zone (for example Headbutt), retrieve
`combat-zone-state`:

- If `known` is true, use only its eligible cards.
- If `known` is false, inspect the relevant pile or give no zone-dependent
  recommendation.

For every meaningful combat recommendation, record a fresh `combat-observe`
snapshot with `--capture`, check and save `advice-decide`, then resolve the observed action.
Routine card movements still go in the zone ledger. Re-observe whenever the
checked plan reaches an observation boundary.

## Elite and boss discipline

For an Elite or boss, give **one executable sequence only**, then wait for the
result. Record it with `advice-decide` (which enforces one unresolved sequence), resolve it after the observed
result, or use `resolve --skipped` if the sequence was replaced. The next
high-stakes sequence is rejected while the previous one remains unresolved.
This prevents a new line of advice from silently contradicting an earlier one.

Before a boss, create one confirmed inventory snapshot with
`boss-preflight`. Enter only names actually visible: unread relics, unclear
potions, or uncertain card identities belong in `unknowns`. They must not be
used as a combat constraint. Checked boss advice automatically requires this preflight and the reviewed boss manifest.

```zsh
python3 scripts/veda_memory.py boss-preflight \
  --run-id "$RUN_ID" --floor-id "$FLOOR_ID" --boss "Bronze Automaton" \
  --relics '{"items":["Burning Blood","Kunai"]}' \
  --potions '{"items":["Blood Potion"]}' \
  --key-cards '{"items":["Corruption","Second Wind"]}' \
  --unknowns '{"items":["unread relic in fifth slot"]}' --capture
```

At a safe boundary, give the player a quiet Run Card rather than raw
telemetry-health messages:

```zsh
python3 scripts/veda_memory.py run-card --run-id "$RUN_ID" \
  --next-priority "Recover safely before the next Elite."
```

It contains only recent wins, current HP/resources, and the next priority.
Evidence confidence and other internal diagnostics stay in the SQLite record
unless they prevent safe advice.

## Reviewable boundary

Resolve a decision only after the observed player action. Close the turn,
combat, and floor after their outcomes are visible. Close each floor with
`floor-finish ... --capture`, then run `floor-completeness`. At a safe
post-floor or post-combat boundary, use `floor-retrospective` for an on-demand
review only after the completeness check passes.
