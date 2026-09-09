# VEDA Decision-Support Protocol

VEDA is the tactical analyst; the player remains the controller and final decision-maker.
Every combat recommendation follows this contract:

1. **Read the current frame.** The decision brief separates verified board facts from unknown values. Earlier screenshots and remembered deck state never fill a missing field.
2. **Name the threat.** VEDA records confirmed incoming damage, or explicitly states that survival is unverified.
3. **Propose a primary line.** The sequence must pass the existing card-in-hand, energy, target, arithmetic, and survival checks.
4. **Offer a safe alternative when it matters.** If supplied, the alternative must independently pass preflight. An invalid fallback blocks the recommendation instead of giving false reassurance.
5. **Execute only by human choice.** A controller instruction is an attempt, not a successful action.
6. **Verify and learn.** The next observation is compared only to the fields that were explicitly predicted. The decision brief, forecast, and outcome are stored as evidence—not automatically promoted to strategy.

## LLM / VEDA boundary

| Capability | Owner now |
| --- | --- |
| Read ambiguous screenshot text and propose tactical hypotheses | LLM, audited by the player |
| Preserve current-board facts and missing fields | VEDA |
| Check visible hand, energy, targets, direct combat math, and lethal end turns | VEDA |
| Maintain confirmed relic, potion, deck, and run ledgers | VEDA |
| Provide a primary line plus a preflight-checked safer line | VEDA validates; LLM may propose |
| Compare forecast with the following screen | VEDA |
| Choose controller inputs or override a recommendation | Player only |

The implementation lives in `veda.decision_protocol`, is attached to every completed `HumanGuidedSession` decision record, and remains conservative: an unreadable value stays unknown.
