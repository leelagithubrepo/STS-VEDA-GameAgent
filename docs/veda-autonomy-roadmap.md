# VEDA autonomy roadmap

## Current boundary

VEDA supports advisory play and explicitly armed Codex-reviewed play. In advisory
mode the player supplies controller input. In reviewed play the adapter can send
one source-reviewed input through the isolated PS5 bridge, then requires a fresh
observed result. See [reviewed play](veda-reviewed-play.md) for the operating
contract. The separate automatic screen reader is not certified for standalone
play; offline checks do not establish live recognition or controller reliability.

## What VEDA owns now

- source-backed facts and separate hypotheses;
- visible-graph route facts with uncertain options isolated from confirmed ones,
  and bounded alternative choice outcomes with exactly one observed match;
- an encounter-transition gate: selected map node must agree with the entered
  encounter type before Elite- or boss-specific reasoning;
- verified combat arithmetic, including energy, target validity, Block,
  Artifact, Weak, Vulnerable, Frail, multi-hit totals, visible end-turn damage,
  and immediate combat-end on lethal damage;
- a deterministic selector among supplied, verified candidate sequences;
- a bounded routine-combat planner for calibrated familiar states; it can own
  direct-card target/ordering decisions, but defers special cards and enemies;
- an experience record containing a structured prediction and observed-result
  comparison;
- Act 1 profiles that prevent unsupported claims, including the rule that Acid
  Slime (M) cannot Split.

## What remains LLM-assisted

The LLM still recognizes the live QuickTime image, proposes strategic candidate
lines, and handles unfamiliar screens. This is intentional until local vision
is calibrated on real frames and VEDA has enough verified trajectory data.
The [general-decision benchmark](veda-general-decisions.md) checks declared
graph facts, not the LLM's image reading or strategic choices. Recorded experience
does not retrain the model or automatically promote new rules.

## Handoff gates

1. Label and benchmark at least 12 real QuickTime frames per domain.
2. Require >=95% accuracy for every combat-critical or map-critical field.
3. Permit VEDA to select among candidate sequences only when the local state,
   map-to-encounter transition, and combat preflight are all verified.
4. Expand VEDA-owned planning from immediate lethal/survival decisions to
   repeatable enemy-profile decisions only after prediction outcomes are
   measured over multiple runs.

These gates govern expansion of standalone automation. Reviewed play retains
its own source, authorization and verification requirements. An unresolved
action stays pending until inspected and reconciled; it is never replayed merely
because verification failed. Ordinary game randomness can be handled with
reviewed bounds, while genuinely missing evidence remains explicit.
