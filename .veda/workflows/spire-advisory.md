# Checked Spire advice before the next run

This workflow remains watch-only. The player operates the game. It does not
start a controller session or an automatic LLM service.

For multiple inspections, use the source-bound evidence ledger and
`scripts/check_combat_evidence.py` described in `docs/veda-combat-evidence.md`.
This shares the tactical checker below, preserves reader/reviewer provenance,
and requests missing views. It does not promote partial OCR to a complete hand.
Before committing HP or Time Eater's twelfth card, checked plans must include a
`potion_review` reason for every available manual potion. Fairy is automatic.

## Observe, retrieve, check, verify

1. Inspect a fresh screen. Record the active run, floor, combat and numbered
   turn. Confirm relic and potion inventory with `inventory-baseline`; record
   later acquisitions/removals as events. Partial baselines preserve confirmed
   items in categories they do not cover. Use a complete empty potion category
   to confirm that no bottles remain.
2. Use `combat-observe --state @snapshot.json --capture` (or `--screenshot`) with
   the context IDs and named evidence source. Follow
   `docs/spire-advisory-example.json`. The observation time is the actual frame
   time, never the time an old image was entered. Give every hand card and
   same-name enemy a distinct ID. Confirm title color, upgrade, current cost,
   playability, active powers, counters and complete-hand visibility. A known
   empty hand is valid; an unread field is null.
3. Retrieve `combat-context --combat-id ...`. This combines current inventory,
   state and matching rules from `data/spire_advisory_rules.json`. Draw/discard/
   exhaust contents remain null until inspected or continuously tracked against
   that frame. A sorted pile is a multiset, not a known draw order. Do not copy
   an old zone ledger into a fresh snapshot after any unlogged input.
4. Use `advice-check --combat-id ... --plan @plan.json`. Then persist the same
   plan with `advice-decide --snapshot-id ... --reasoning ...`. It records the
   rule IDs/version, boss manifest, energy after each play, targets, forecast
   availability and abort condition. This replaces `decision --phase combat`.
   Give the player only the next checked step or bounded sequence.
5. Record what the player actually did with `resolve`. Record card movements
   with `combat-zone-event`. A draw, generated card, upgrade, potion, enemy
   action, turn change or unsupported interaction ends the checked sequence.
   Inspect again before continuing. A mismatched prediction is a reason to
   observe, not to repeat an action. Use `resolve --skipped` for superseded
   advice; never invent the action to clear a pending decision.

`combat-context` refuses snapshots older than three minutes, snapshots from a
closed combat/turn, or snapshots preceding a recorded change. Checked advice
uses a transaction so its inventory and revision cannot change during insertion.
A recorded recommendation consumes the snapshot for further recommendations.
These safeguards cannot detect unlogged physical input: fresh observation is
still mandatory after the player changes anything.

## Live-play performance policy

Live play uses a two-tier loop. Every meaningful decision, draw, enemy turn,
potion, status change, generated card, lethal calculation, and end-turn choice
still requires a fresh frame and verification. Routine low-risk card movements
are kept in the in-memory combat ledger and are summarized at the next
meaningful boundary; they do not create a SQLite decision, screenshot archive,
inbox check, or retrospective while combat is active. The bridge remains one
persistent warm session.

Use `python3 scripts/capture_observation.py --ephemeral` for a routine frame
that is needed only to verify the next action. Keep the default archived
capture for meaningful decisions, floor boundaries, elites, bosses, potions,
and other evidence that belongs in SQLite.

Codex reasoning is part of the wall-clock cost. Keep the prompt limited to the
fresh frame, confirmed combat ledger, and the next legal action. Do not run
reviews, website generation, broad file searches, or telemetry health reports
inside combat. Use `scripts/veda_live_metrics.py` after a floor or play block to
separate observation cadence from game time. A bridge command should normally
be measured in milliseconds; long gaps indicate observation or reasoning
overhead and should be addressed there rather than by reducing safety checks.

## Tactical checks

- **Potions:** review them before spending HP or accepting damage. Immediately
  record confirmed consumption with `potion-use`, including combat/turn when
  known and only the effects actually observed. It consumes one bottle and
  links the inventory event atomically. Fairy is automatic, not a drink action.
- **Headbutt:** inspect the current discard pile and supply `return_card` from
  that pile. Stop at the selection/draw-order change.
- **True Grit:** base is random; upgraded selects `exhaust_card_id` from the
  remaining hand. Shame is a possible target when actually present.
- **Corruption:** only Skills become free. Barricade remains a Power with its
  current cost. With Runic Pyramid inspect draw first; if Barricade is in hand
  or draw and inactive, record `setup_reason` comparing timing, incoming threat
  and the remaining Skill reserve. This is a conditional comparison, not an
  unconditional rule to play Barricade first.
- **Dual Wield:** identify an Attack/Power still in hand using `copy_card_id`
  and verify space for one or two copies. Observe afterward.
- **End Turn:** inspect every playable zero-cost card and record a concrete
  reason in `zero_cost_review` for each unused card ID. Body Slam uses Block
  plus attack modifiers. End Turn and forced Time Warp require a supported
  survival forecast; an unavailable forecast is not approval.
- **Bosses:** confirm inventory with `boss-preflight`. Current boss/Ascension
  must have a reviewed local manifest and visible intent. The initial pack
  covers Bronze Automaton and Time Eater at A0–20. Other bosses require research
  and an updated pack before checked advice. Community sources are attributed;
  they are not labeled official. The visible screen outranks an expected move.
- **Rest sites:** use `campfire-advice`. Compare Rest and Smith using current
  HP, entry-heal timing, deck size and direct next-boss status. Do not add
  Eternal Feather twice, future Burning Blood without a fight, or Pantograph
  to current HP before the boss. Unsupported healing/option relics stop the
  automatic comparison rather than fabricate a result.
- **Map:** elite/shop classifications need `classification_evidence` on each
  legal node (tooltip, clear map symbol, or explicit player confirmation).
  Unverified classifications remain unknown and cannot support a route choice.

## Limits and validation

The 2026-09-24 rules add inspected base Anger, Hemokinesis+, and Slimed.
These are single-action checks with an observation boundary, not full numeric
forecasts. Log Anger's played original and generated discard copy separately;
the copy does not increase the permanent deck. Hemokinesis+ requires more than
2 HP regardless of Block. Slimed is a Status, so Corruption does not make it
free. Other variants still require their own review.

Spike Slime (L)/(M) support is bounded to Ascension 2. A snapshot must supply
`ascension: 2`, the visible intent and displayed hits, and an enemy `move` with
matching `intent_effects`. Its `evidence` names the reviewed Spike Slime
reference with `kind: "reviewed_reference"` and `observed_intent: true`.
Keep reference-based move interpretation separate from observed outcomes.
The large slime also needs its inspected `split` descriptor and maximum HP.
Crossing half HP invalidates the previous incoming-damage forecast; inspect
the new intent. After splitting, record actual child identities, HP, and
intents before continuing. Unknown effects remain in `unmodeled_effects` and
still block End Turn; never remove them merely to obtain approval.

The checked tool validates evidence, ownership, card costs, targets, card/hand
limits and observation boundaries. Its numeric forecast covers a small set of
simple direct cards; unsupported powers/relic triggers cannot certify lethal
or survival. A single special-card step may pass these legality checks while
its forecast is explicitly unavailable. Read its effects and state the safety
reason before advising it. Do not describe that as a simulated safe sequence.

Regression tests cover displayed intent versus raw damage, Bash sequencing,
exhausted-card reuse, stale energy/HP, immediate versus end-turn predictions,
potion concurrency, pile eligibility, upgrades, campfire timing and boss
thresholds. These tests validate the tools, not autonomous gameplay performance.
No historical Time Eater turns were reconstructed for this release.
