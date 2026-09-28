# Continuous learning play

Use the hot path in `docs/veda-play-hot-path.md`. The reviewed adapter runs
with `--decision-policy learning`; compact combat/menu drafts carry
`decision_policy:"learning"`. Strict advisory checks remain available for
offline analysis. Do not switch back to strict merely because a warning appears.

## Strategy under uncertainty

Use visible HP, energy, cards, enemies and known inventory. Keep unread effects
and hidden draws explicit. Compare plausible damage and future setup without
inventing probabilities. When no exact forecast exists, choose using the
available information and observe the outcome. Consider a potion when useful;
a missing potion review does not prevent playing or ending a turn.

Inspect an unread pile when it would change the decision. Prefer a cheaper
inspection or another playable move if repeated inspection reveals nothing.
Barricade, Corruption, Runic Pyramid, zero-cost cards and enemy turn counters
are strategic considerations, not universal gates. A game can be lost while
the autonomous loop is working correctly; record the result and its limits.

Use `python3 scripts/veda_play_lessons.py --help` for a compact read-only query
of previous verified decisions matching the current encounter, card or action.
Retrieve at encounter start or after a new mechanic/prediction miss, then reuse
the result until context materially changes. Never substitute a past hand or
old HP value for a current observation. One case suggests an investigation;
it does not prove the cause of an outcome or improve model weights by itself.

## Drafts and results

Prepare source-free drafts before the fresh image. Use combat/menu/inspection
helpers to build frame hashes, review declarations and pending-action links.
Inspect every image bound to a request; a saved path alone is not observation.
Submit the short request-file pointer immediately after binding. `--execute`
prepares and sends exactly one input. Navigation and actual play may require
several separately observed inputs.

After dispatch, describe the actual resulting state. Learning mode accepts
supported observed action results even when numerical predictions were wrong.
The result review still needs to show that the action happened. An unchanged
selected card is not a successful play, and a random draw must not be invented
to make a forecast match. Preserve uncertain card destinations as unknown.

Use returned `next_context` after a turn/floor transition. For combat ending,
declare the observed combat outcome; reward screens continue the run. Only a
confirmed run-result screen establishes overall victory or defeat. A result
waiting for SQLite completion remains `verified_pending_log`: retry finalize
with the same durable evidence, never resend the controller action.

## Recovery without abandoning the run

| Response or observation | Next work |
| --- | --- |
| Strategic warning or unknown forecast | Weigh it, keep uncertainty in the log, choose one move. |
| Malformed or stale unsent request | Repair/recapture and retry; retain healthy arming. |
| Card unavailable or unaffordable | Choose a playable option or End Turn. |
| Unexpected action result | Review the actual state and record the forecast difference. |
| Pending input with unclear result | Inspect and reconcile; do not replay or discard it. |
| Verified result awaiting log | Finish its durable record before the next decision. |
| Unfamiliar map/event | Read visible options, use actual connected nodes and known controls, decide. |
| Repeated inspection failure | Change approach or select another available action; record the gap. |
| Time target exceeded | Report the cause briefly and continue recovery/planning. |
| Actual device/network/feed failure | Stop controls, preserve pending evidence, report the connection failure. |
| User Stop, defeat or full-run completion | End the session and verify bridge cleanup. |

Do not describe a recoverable review as a stopped session or request repeated
authorization. The adapter cannot solve an unread screen by itself; continue
the model's observation/replanning work. If every implemented action remains
unavailable, report that specific ongoing recovery honestly rather than
inventing progress. Keep product edits in Builder, separate from controller
ownership and live telemetry.

## Timings and recorded learning

The clock includes strategy, captures, draft repair, verification and logging.
A focus tap is not a completed card. A restarted adapter preserves the move's
start; technical recovery is not player waiting. Report measurements as partial
when the entry/start was not observed. Offline fixtures prove software behavior,
not PS5 recognition speed, autonomous floor completion or win rate.

After each observed result, SQLite retains the reason, policy warnings,
prediction limits and actual state. Review relevant mismatches on the next
encounter. A new durable game rule still needs corroborating evidence and
tests; that review happens after play, without stopping the current run merely
because a rule is missing.
