# Human-Guided Operation

This is VEDA's transition mode before autonomous control hardware exists.

```text
QuickTime frame → VEDA observation → preflight → human controller input
      ↑                                                   ↓
      └──────────── verification + experience record ────┘
```

The human remains the only controller. VEDA may only issue one approved,
explicit recommendation at a time. The recommendation must name the target,
show the energy-legal prediction, and be verified against a later observation.

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
