# VEDA autonomy roadmap

## Current boundary

VEDA is a human-guided agent: the player supplies controller input; VEDA may
observe, retrieve knowledge, validate a proposed sequence, predict its direct
result, and compare the result after the action. No PS5 input is emitted by this
repository.

## What VEDA owns now

- source-backed facts and separate hypotheses;
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

## Handoff gates

1. Label and benchmark at least 12 real QuickTime frames per domain.
2. Require >=95% accuracy for every combat-critical or map-critical field.
3. Permit VEDA to select among candidate sequences only when the local state,
   map-to-encounter transition, and combat preflight are all verified.
4. Expand VEDA-owned planning from immediate lethal/survival decisions to
   repeatable enemy-profile decisions only after prediction outcomes are
   measured over multiple runs.

Failure at any gate returns control to human-guided, LLM-audited play; it never
silently turns an assumption into an automated action.
