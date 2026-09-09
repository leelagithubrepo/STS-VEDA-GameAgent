# Human-Guided Operation

This is VEDA's transition mode before autonomous control hardware exists.

```text
QuickTime frame → VEDA observation → preflight → human controller attempt
      ↑                                                      ↓
      └──── expected transition → fresh-screen verification + experience ────┘
```

The human remains the only controller. VEDA may only issue one approved,
explicit recommendation at a time. The recommendation must name the target,
show the energy-legal prediction, and be verified against a later observation.
An input is never treated as success merely because the human pressed a button:
VEDA records the intended action, compares only its explicit predicted fields
against the next screen, and labels the result `confirmed`,
`unexpected_transition`, or `needs_fresh_observation`. Focus changes, modal
screens, and relic carousels therefore force a re-read instead of a guessed
next input.

If the screen is stale, the intent unknown, a target ambiguous, the boss name
is unreadable, or an action sequence fails arithmetic validation, VEDA pauses
and asks for another frame. For any proposed End Turn, VEDA must either have a
complete survival forecast (HP, total incoming damage, and visible end-of-turn
status damage) or explicitly label survival as unverified. A projected lethal
turn is never an approved recommendation.
An unexecuted or superseded recommendation is abandoned, never recorded as an
executed decision.

This mode creates real experience data while retaining human supervision. It
does not require a PS5 bridge, an API key, Pico hardware, or hidden control.

## Evidence-first decision packet

Before a recommendation, VEDA creates a decision brief containing only facts
from the current frame: player state, visible hand, visible enemy state,
immediate threat, confirmed ledger context, and a separate list of unknowns.
Missing values are never inherited from a prior screenshot. When a safer
alternative is provided, it must independently pass the same preflight; an
invalid fallback is rejected rather than presented as safety. The brief is
stored with the verified experience record. See
[the decision-support protocol](decision-support-protocol.md).

## Potion choices

Potion art and screen position are not sufficient evidence. Before replacing a
full inventory, VEDA requires a named, confirmed potion ledger, the capacity,
the exact potion to discard, and the potion being gained. Duplicate potions are
kept as distinct slots. If any of those facts are missing, VEDA asks for a
tooltip/screenshot rather than telling the player to discard “the blue one.”

## Prediction telemetry (v1)

Each verified decision retains only fields explicitly predicted: energy (weight
1), Block (1), player HP (2), and named enemy HP map (1). The score is
`100 × matched field weight / checked field weight`. Coverage is the share of
decision records with at least one checkable prediction. Unknown values are not
scored, and the score does not update strategy automatically; it is calibration
telemetry for later review.
