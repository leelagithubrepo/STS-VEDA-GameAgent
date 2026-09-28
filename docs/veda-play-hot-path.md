# Play loop and timing targets

Read this once at startup. Use the guide for the current screen only. Spire
interprets the current image and chooses the move; helpers validate and package
that declaration. They do not recognize pixels. After authorized arming, continue
playing in the same turn; an armed status is not task completion.

| Measured interval | Target |
| --- | ---: |
| Launch through first verified input | 90 seconds |
| Ordinary logical card/menu move, including focus and verification | 20 seconds |
| Noncombat floor | 90 seconds |
| Ordinary combat floor, including rewards and departure | 4 minutes |
| Elite floor | 6 minutes |
| Boss floor | 8 minutes |

These are initial engineering targets, not established live performance or
hard gameplay cutoffs. A long fight can exceed them. An overrun reports the
phase and elapsed time; it never authorizes skipping inspection, playing an
unchecked card, dropping a pending action, or racing to End Turn.

## Startup

The project launcher starts `timing.json` before launching Codex when a recorded
run can be identified. This timing identity is not live game confirmation.
If switching an existing advisory session, start the clock immediately:

```sh
python3 scripts/veda_play_clock.py start --session SESSION_DIRECTORY --run-id RUN_ID
```

1. Read `veda_play_context.py --run-id RUN_ID` once for IDs, inventory and pending
   recovery. Treat it as historical. Do not query every previous request.
2. Check exclusive process ownership and `./scripts/bridge status`. Use
   command-scoped approval if the sandbox prevents process/socket checks.
   Inspect a planning screenshot to establish the actual game, attempt and HUD
   floor. A floor-1 fight cannot use a floor-0 Neow record. Reconcile mismatches
   with the observed lifecycle before committing gameplay telemetry.
3. Start one `./scripts/warm_bridge --idle-timeout 0 --socket /tmp/veda-ps5-bridge.sock` and one adapter:
   `python3 scripts/veda_reviewed_play.py SESSION_DIRECTORY --run-id RUN_ID --mode codex --decision-policy learning`.
   Send `{"operation":"bridge_preflight"}` followed by a newline. Require ready.
   The bridge's ready event must report `command_channel:"unix_socket"` and the same
   endpoint. `connect_socket_missing` means a missing path, not denied permission.
4. Use [staged arming](veda-arm-startup.md): validate the arm draft before capture,
   then capture/stage/display in one tool call. After inspecting the exact image,
   send the short explicit confirmation and immediately submit its ordinary
   `request_file` pointer. Route planning is separate from this identity review.
5. Require `armed_codex_reviewed` and immediately continue below. Do not return
   a final response or wait for another "continue" unless asked to arm only.

## One decision

Start floor timing before planning its first move (the adapter continues it
across subsequent inputs). For example, send to the adapter:

```json
{"operation":"timing","event":{"operation":"begin_floor","floor_id":"ACTUAL_FLOOR_ID","kind":"combat"}}
```

Kinds are `noncombat`, `combat`, `elite`, `boss`. The clock follows actual
floor arrivals and completes a floor when the next room is verified. A focus
tap counts as an input, never a completed card. After each completed move,
the next decision clock starts immediately, including thinking time.
If play resumes mid-floor, its timer measures that observed segment only. Only
a floor whose entry was observed can establish the full-floor timing target.

Use a planning image and confirmed ledger facts to fill one source-free draft
with `decision_policy:"learning"` for combat/menu actions. Legacy drafts default
to strict; regenerate an unsent draft explicitly for the learning session.
Finish strategy, rule retrieval and draft validation **before** the fresh action
image. The action image starts the unchanged 30-second freshness window.

- Combat: use [compact combat](veda-combat-play.md).
- Combat focus outside the hand: use [focus recovery](veda-combat-play.md#combat-focus),
  then inspect the actual destination. A raised card's keyword help is hand focus.
- Menus: use [menu controls](veda-menu-controls.md).
- Map: use [map survey and route planning](veda-map-play.md).

Once the source-free draft validates: capture → inspect that exact image → bind
with the helper and `--execute` → submit its short `request_file` pointer. The
armed adapter prepares and sends **one** input in that call. Do not send another
`send` command. It still performs the ordinary checks and durable writes.

After the input, inspect its result, fill the compact result draft and validate
it. If the inspected image is still fresh and the result schema was already
prepared, bind it immediately; do not take a redundant second image. If repair
or schema work consumed the window, finish it before taking/inspecting a fresh
result image. Submit its pointer; there is no `--execute` or `send` for verification.
Use the canonical `next_context` returned, then continue with the next decision.

Never hand-build hashes, capture times, Reading objects or mutation-review
objects. Never repair expired source fields. Keep the draft if its game facts
still match, and let the helper bind a new exact inspected image.

## Overruns and recovery

Adapter responses include timing; while idle it polls every five seconds and
emits each newly crossed deadline once. This reports model silence as well as
tool work. The standalone clock can show the full record:

```sh
python3 scripts/veda_play_clock.py status --session SESSION_DIRECTORY --run-id RUN_ID
```

Use `operation:timing` with `event:{operation:"phase",name:...}` when work
changes phase: `preflight`, `capture`, `inspection`, `planning`, `draft`,
`prepare`, `dispatch`, `verification`, `telemetry`, `recovery`, or `idle`.
The adapter automatically records input-bound phases. Reading, research,
drafting, validation and recovery all count as active time.

At an overrun, state the actual delay and next recovery step in one short
progress update. If an input is pending, resolve its outcome first. If no input
was sent, diagnose the exact helper rejection and revise the draft. After two
stale captures for the same move, complete that repair before taking another
capture. A timestamp change does not reset the stale counter. The arm, combat,
inspection and menu helpers automatically count failures and stale captures
against an existing matching play clock, even before adapter submission. They
never create a clock for an unknown run; an unavailable timer is reported in
the error response. The same exact capture path is counted once.

Only genuine player waits or stopped play are excluded with a named `pause`
event (`category:"user_wait"` or `"paused"`, plus `reason`), followed by `resume`.
Never pause the clock for model thinking or technical recovery. Closing the
adapter records a stop; reopening for play resumes measurement. Restarting does
not erase an incomplete move. Missing startup coverage or clock rollback is
reported as incomplete measurement, never as a fast result.

If closing an adapter is part of technical recovery, immediately resume its
paused clock with `veda_play_clock.py resume` and set phase `recovery`; do not
exclude time while preparing its replacement. A manual clock start is marked
partial because earlier handoff work was not observed. If a past interval was
missed or misclassified, use `measurement_gap --reason REASON_CODE` to label the
measurement incomplete without inventing elapsed time or clearing its history.

A `recoverable_review` keeps the task active: refresh, repair an unsent draft,
inspect a result, or re-plan. Missing forecasts and strategy reviews are warnings
in learning mode. If a helper cannot express the situation, record the exact
gap and pursue another supported action/inspection. Never replay pending input.
Actual hardware/network/feed failure stops controls; user Stop, defeat or
full-run completion ends play. Ordinary combat victories continue. See the
[action-evidence policy](veda-action-evidence.md) for the distinction.

Retrieve compact historical cases with `veda_play_lessons.py` at encounter
start or after a new mechanic/forecast miss. Cache those cases while the context
remains relevant; a database lookup on every focus tap adds no useful learning.

## What is verified

Offline fixtures exercise compact card actions, results, boundaries, timing and
tooltip inspection with temporary SQLite and a fake controller. They establish
contract behavior, not live PS5 speed, recognition accuracy or floor completion.
The next live run must measure these targets and report actual completions.
