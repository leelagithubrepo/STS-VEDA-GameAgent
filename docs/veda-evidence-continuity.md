# Evidence during a settled turn

Thinking time does not change a settled Slay the Spire screen. Bind combat,
menu and inspection action drafts with `--session SESSION_DIRECTORY/state.json`.
This attaches the adapter's current input epoch automatically; never write or
alter that binding, source time or image hash yourself.

Inspect a frame after each input has finished resolving. That inspected frame
can support the next decision for as long as the owned session and state remain
unchanged. Validate the draft before or after thinking; do not recapture merely
because 30 seconds passed. The adapter checks the session identity, input
sequence, source ordering, game context and original image bytes at preparation
and again at dispatch. It also checks the owned bridge's current readiness.

An input attempt advances the epoch before contacting the controller, even if
delivery is uncertain. Verify the exact pending input from its actual after
image. Never replay it. After verification, the same inspected result frame may
support the next action in the new epoch, if it depicts the settled state and
no further input or state change intervened. A transition animation is not a
settled screen.

Restart, disarm and re-arm invalidate bindings from the previous session.
Arming still needs a recent identity/feed review (30 seconds); standalone
legacy actions without `--session` keep their age check. These are startup and
compatibility rules, not a timer on an active settled turn.

If the player moves a controller, the screen changes outside the pending
input, the game/feed target changes, or continuity is otherwise lost, send:

```json
{"operation":"invalidate_evidence","reason":"Describe the actual observed change"}
```

This sends no controller input and leaves pending actions intact. Cancel a
prepared, unsent action with `{"operation":"cancel_prepared"}` if needed,
inspect the current screen and bind again.
An attempted action must be reconciled. Actual feed/transport loss follows the
existing stop/recovery instructions. This mechanism cannot automatically see
an unreported manual controller input or prove that a capture feed is live;
Orchestrator still observes those conditions. An age cutoff could not prove
those facts either.

## Delayed outcome recording

A result is evidence of what happened after a particular input, not permission
to send another input now. Combat, menu and inspection result helpers accept
an older inspected after-image when it belongs to the exact pending action,
follows dispatch and its bytes/receipt remain intact. SQLite keeps the original
capture time and records the later review time. Historical result acceptance
never re-arms a stopped session. Reopening the adapter requires normal current
identity/feed review before any new gameplay input.

## Submit exactly the helper's output

Both of these equivalent file envelopes work:

```json
{"request_file":"/absolute/request.json"}
```

```json
{"operation":"request_file","path":"/absolute/request.json"}
```

The file contains the actual `execute` or `verify` packet. Extra/conflicting
fields, duplicate keys, oversized files and nested file pointers are rejected.
Submitting a verification packet does not send controller input. Never replace
its operation with `execute` to recover from a submission error.
