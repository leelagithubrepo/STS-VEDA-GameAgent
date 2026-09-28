# Learning policy and action evidence

Autonomous Orchestrator uses `--decision-policy learning`. Its compact combat
and menu drafts explicitly carry `decision_policy:"learning"`; omitted fields
retain strict compatibility for existing callers. The draft and active session
must agree. Spire's advisory checker remains strict when used directly.

The player's policy is to keep playing through uncertainty, record decisions
and use the observed outcomes to improve later choices. Strategic preferences,
missing rules and incomplete forecasts are advice. A poor move or a lost run
is an acceptable learning result. Unknown damage is never recorded as zero.

## What changes in learning mode

Combat assessment retains the strict check's warnings and whatever forecast
is supported. It can permit a card or End Turn despite an unmodeled enemy move,
missing optional potion/zero-cost review, incomplete strategic coverage or a
survival forecast that cannot establish a win. It still rejects an unavailable
card, a known unaffordable cost, malformed state or a contradictory declaration;
the operator selects another action or repairs the declaration and continues.

Current screen and controller semantics still matter: a recommendation must
map to a real input in the current UI. There is no raw-button bypass. Exact
source, run ownership and pending-input correlation prevent accidental repeated
input or controlling the wrong game. These are execution requirements; they
are not strategic opinions or requirements to predict hidden outcomes.

After input, the observed result governs the ledger. Learning can verify a
resolved card or committed menu outcome that differs from predicted HP, energy,
Block, enemy state or reward effects. It stores the difference for review. An
unchanged card/option or a focus change alone does not prove an effect. Menu
outcome forecasts are predictions; actual inventory and lifecycle changes must
be explicitly observed and logged, never filled from a preferred branch.

## Keep recovering

| Situation | Next action |
| --- | --- |
| Missing rule, uncertain enemy move or unsupported numeric forecast | Keep it unknown, choose using available evidence and log the outcome. |
| Optional strategy review missing | Weigh the warning; do not ask the player for approval. |
| Incomplete future map or an unread neighboring icon | Inspect if useful, then choose an actual visible connected option. |
| New event or random outcome | Read the options, make a decision, observe the actual result. |
| Expired or malformed unsent request | Refresh/repair it; keep the healthy session active. |
| Candidate is unavailable or unaffordable | Choose another playable action or End Turn. |
| Forecast differs from a proven observed result | Log the mismatch and re-plan from actual state. |
| Input result unclear | Inspect and reconcile the pending input before the next move; never blindly repeat it. |
| Verified result waiting for durable logging | Finalize the saved outcome, without replaying the input. |
| Repeated unchanged inspection or timing overrun | Change approach; report the specific delay and continue recovery. |
| Hardware/network/bridge failure or missing game feed/target | Stop controls, preserve pending evidence and report the actual connection problem. |
| User Stop, confirmed defeat or completed run | End this session and verify owned bridge cleanup. |

`recoverable_review` is a response within the play loop, not a terminal answer.
It names the next recovery step and whether any input may have been sent. A
healthy adapter remains armed. Do not interpret every rejected request as a
reason to close the bridge or request the player's authorization again.

After two unsuccessful inspections of the same unchanged fact, try another
approach or an available conservative move. A new filename is not progress.
Do not spend repeated 30-second capture windows discovering request schemas;
prepare and validate drafts first. Keep recovery time on the play clock.

Unknown delivery is distinct from unknown strategy. Preserve the pending record
and observe what happened. A reconciliation marked `unknown` does not clear
that record or permit replay. Hardware failure during dispatch preserves the
record for recovery after reconnection. A durable-storage failure also needs
repair before another consequential input; do not silently lose the journal.

If no implemented action can express the current UI, keep pursuing observation
or another supported path and record the precise software gap. Report ongoing
recovery honestly. This policy does not claim the adapter can autonomously
recognize every screen or recover from every possible software failure.

## Decisions become retrievable cases

Before dispatch, SQLite records the chosen action, reason, policy warnings and
available forecast. After verified observation, it records actual state and
prediction mismatches. `veda_play_lessons.py` reads relevant confirmed cases
by encounter, card, action or screen. Query at encounter start or after a new
mechanic, then reuse the result while relevant; do not query on every focus tap.

Cases preserve uncertainty and source references. They are historical advice,
not current game state, causal proof or automatic model training. An unresolved
input cannot become a successful learning case. Reusable rule changes still
need corroboration and offline tests, without stopping play for missing rules.

## Preserve recovery evidence

Alongside the session journal, a concise recovery record may contain the run
and action IDs, actual last verified state, failed check, input status, attempted
inspection, alternatives considered and next recovery step. Reuse screenshot
paths and hashes rather than copying the entire history. Never label a pending
input cleared or hardware cleanup verified without the corresponding evidence.

The play task owns controller actions and live run telemetry. Builder changes
code and runs tests using temporary databases and fake controllers. A fresh
operator must load reviewed skill/code changes; an already-running process
may retain old behavior. Installing revised guidance is not proof of live
floor completion, recognition accuracy, speed or win rate.
