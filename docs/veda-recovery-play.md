# VEDA autonomous recovery

Autonomous play may repair recoverable packet and helper failures without
asking the player to mediate. The recovery decision is based on delivery state,
not on whether the strategy forecast is complete.

## Recovery order

1. Read the session summary and classify the failure as pre-input,
   post-input, or infrastructure.
2. Before input, repair the source-free packet and revalidate it. Reuse the
   same inspected capture when the visible state is unchanged.
3. After input, preserve the pending action. Inspect the exact after-image,
   adapt the result envelope if the helper rejected a compatible observation,
   and verify the same action. Use `finalize` only for a
   `verified_pending_log` that is already confirmed. Never resend the input.
4. If the helper cannot express a supported screen, stop the adapter and close
   the owned bridge, verify that no input is in flight, make the smallest
   compatible code change, run focused and affected tests, reload the adapter,
   and re-arm the same run.

## Hard stops

Stop only for an unresolved controller delivery, wrong run or game identity,
missing or unreadable game feed, bridge or network failure, hardware failure,
confirmed defeat, confirmed victory, or the player's Stop instruction. A new
event, unknown intent, incomplete forecast, stale draft, schema error, timing
overrun, or missing strategy rule is recoverable evidence and should lead to a
conservative supported move or a repair loop.

Repairs must preserve the run directory, pending action, evidence chain and
SQLite history. Do not fabricate observations, change save data, replay an
uncertain input, or claim progress before the next screen is inspected.
