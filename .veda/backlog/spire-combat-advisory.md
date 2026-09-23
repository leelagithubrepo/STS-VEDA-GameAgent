# Backlog: safer, evidence-grounded Spire combat advice

**Status:** Implemented and regression tested for the 2026-09-22 release
**Scope:** Slay the Spire / Spire advisor only
**Owner:** Builder; Product Owner approved the pre-run release on 2026-09-22

## Requirements

1. **Keep potion inventory current.** Whenever a potion is consumed, immediately record the potion use in the run ledger and update the confirmed remaining potion slots. The use event must link to the active run, floor, and combat/turn when known. Do not infer an effect that was not observed.

2. **Check discard-pile eligibility before card advice.** Before recommending a card whose effect depends on the discard pile—especially Headbutt—inspect or retrieve the confirmed discard-pile state. Recommend only an eligible card that is actually available there. If the pile is unknown or cannot be inspected, say so and do not guess. Record the inspected state with the decision when available.

3. **Review every playable zero-cost card before ending a turn.** Before recommending End Turn, check each playable 0-cost card. In particular, use Body Slam when its damage from the current Block is a safe, legal improvement to the turn. Do not play it when doing so would violate a higher-priority survival or legality constraint; explain the exception.

4. **Calculate energy by card type and power status.** Apply Corruption’s free-cost effect to Skills only. Powers—including Barricade—still require their listed energy unless another confirmed effect changes the cost. Recalculate remaining energy after each advised card and never treat a Power as free merely because Corruption is active.

5. **Re-observe after draws and major state changes.** Do not advise a long sequence that crosses a draw, generated card, potion effect, enemy action, status change, or other material state change without rechecking the screen/state. With Runic Pyramid, verify the retained hand, card order, and available hand space after each such step before continuing the line.

## Acceptance checks

- A potion-use action leaves the ledger’s current potion inventory consistent with the observed game state and links the use to its context when known.
- A Headbutt recommendation is blocked or qualified if discard-pile state is unknown; when known, its target is among confirmed eligible discard cards.
- End-turn advice demonstrates a check for playable 0-cost cards, including Body Slam’s damage based on current Block and a safety/legality check.
- Energy calculations distinguish Skills made free by Corruption from Powers that retain their costs.
- Advice pauses for a fresh observation after a draw or major state change; Runic Pyramid lines additionally verify retained-hand order and hand capacity before continuing.

## Implementation notes

- Reuse the existing SQLite combat-zone, inventory-event, decision, and evidence records where possible; do not create a parallel telemetry store.
- Preserve Spire’s watch-only/advisory role. This backlog does not authorize controller input, live-play interruption, automatic LLM review, or strategy promotion without evidence and tests.
- Builder should add focused regression tests for each acceptance check and document any state that remains unavailable rather than fabricating it.

## Added by leela
 I should have checked the draw pile and planned around
  Runic Pyramid before recommending Corruption in this long
  fight. I’ll check the stored Barricade evidence and the
  current screen before recommending another play.
Implementation and limits: [checked advisory workflow](../workflows/spire-advisory.md).
