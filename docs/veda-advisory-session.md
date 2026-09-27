# Keep advisory play moving

Keep one checked session open instead of replaying the whole inspection history
for every card. Its compact response joins the current hand, enemies, inventory,
missing evidence and last action. The player operates the game. This tool does
not capture or recognize images, call a model, control a bridge or write SQLite.

Use this session during combat and the [pause checkpoint](veda-checkpoint.md)
at a pause or floor boundary. The [bundle checker](veda-combat-evidence.md)
remains useful for offline review. Do not run both full workflows per card.

## Start once per play block

The advisor prepares a context file with existing `run_id`, `floor_id`,
`combat_id` and `turn_id`; unknown combat/turn IDs are `null`. From the repository:

```sh
python3 scripts/veda_advisory_session.py artifacts/advisory/current \
  --context artifacts/advisory/context.json
```

Keep the process open and send one JSON request per line using persistent stdin.
It returns one response per line. This is an advisor interface, not a form for
the player. Initial `status:ready` means the session opened, not that a move is
approved. After conversation compaction, ask the same process for compact state:

```json
{"operation":"summary"}
```

Every other operation needs a new `request_id`. An identical retry returns a
historical receipt with `advisory_ready:false`; it never repeats advice. Reusing
an ID with different contents fails.

## One observed action at a time

1. `observe` supplies `source` (saved PNG path) and `observation` in the
   [evidence-ledger format](veda-combat-evidence.md). Record actual capture time,
   context, epoch, hash and named reader/reviewer provenance. Unknowns stay
   unknown. `inventory` supplies a source-bound `receipt` in that format;
   relic and potion coverage must be complete before checked advice.
2. `advise` supplies a `plan` with exactly one step. Existing freshness, tactics,
   pile, potion, zero-cost-card and survival checks apply. Read `advisory_ready`,
   `checked_plan`, `blockers` and `inspections` together. A legal bounded play
   with an unavailable forecast is not proven safe or lethal.
3. `action_reported` names the exact `action_id`, Boolean `performed` and an
   `evidence` note describing the player's report. A performed action invalidates
   volatile state; a potion also invalidates inventory. Reporting does not verify
   success. A skipped action clears advice without inventing a play.
4. Inspect a later frame. `observe` can include `outcome_review` with the exact
   `action_id`, Boolean `matches_expected`, named `reviewer`, `evidence` and
   `actual_action`. Compare the resources, cards, targets and statuses that could
   change. This records a reviewer declaration bound to before/after images,
   not an automatic proof of recognition or mechanics. No next recommendation
   is issued until the outcome is reviewed.
5. A successful End Turn review requires a distinct observed turn in the same
   combat. Use `change_context` with the exact new context and a `reason` before
   the new observation. A changed floor/combat, unreported input or mismatch
   needs `unlogged_input` with a concrete `reason`, then fresh state and inventory.
   Never relabel an old turn to clear pending advice.

Once an actual checked recommendation named `turn7-play1` has been performed:

```json
{"operation":"action_reported","request_id":"turn7-report1","action_id":"turn7-play1","performed":true,"evidence":"Player reports playing the recommended card; outcome still needs inspection."}
```

Observation and inventory schemas are shared with the evidence ledger. Card
candidates or incomplete enemies never become a complete observation. Headbutt,
Warcry and other selection/draw boundaries need another inspection. Keep
Shuriken's current-turn attack counter in inspected statuses; ownership alone
does not establish its value.

## Restart and uncertainty

Use the same directory with `--resume` after interruption. Only one process may
hold the directory. Resume checks every retained source hash and preserves
pending advice for inspection, but requires `unlogged_input` reconciliation and
fresh state/inventory. This covers game input while disconnected. Do not use
one-shot `--request` repeatedly during a floor: every restart needs reconciliation.

A storage failure stops further advice in that process. Close, resume and
reconcile the actual screen. Retrying the proposed card is not recovery.

Private source copies use content hashes. Current evidence is checked before
and after each recommendation; all sources are checked on restart. State writes
are atomic and file/directory updates are synchronized. Bounds are 256 requests,
8 MB of state and 512 MB of images. Start another session at a safe boundary
before reaching them; preserve the previous directory for review.

`--replay` is only for historical checks with an explicit `as_of` clock on advice.
A replay session cannot reopen in current-review mode. Every output denies
controller/runtime authorization. These checks do not validate a screen reader.

## Keep the live prompt small

At a safe boundary load relevant reviewed rules and confirmed inventory. During
combat use the fresh frame, compact summary and one decision. Give the next
card/target and a brief reason; mention a potion when it changes the immediate
decision. Acknowledge the observed outcome before the next recommendation.

Do not research already-reviewed mechanics, reread the archive, fan out reward
analysis or rebuild telemetry during combat. A missing critical rule remains a
stop condition: research it at a safe boundary and preserve the source. Dismiss
blocking reward popups before comparing choices. Save one bounded checkpoint
when pausing; retrospective reports and history repair happen after play.

This reduces repeated local work and context size. It does not remove image
inspection, model reasoning, network fallback or player time. Measure another
actual play block before claiming faster end-to-end floors.
