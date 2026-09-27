# Play with Codex Orchestrator

This mode keeps Codex in the loop to inspect the game, choose the next move,
send one controller input and verify its result. The player can say **stop** at
any time. It uses the existing saved run and does not start another run after
victory or defeat.

The new adapter connects reviewed combat moves, card selections, potion menus,
map/reward/rest/event/shop choices and SQLite recovery. These paths are tested
with recorded declarations and a fake controller. They still need their first
live end-to-end check. The local automatic screen reader is not certified for
standalone play; that separate runtime's calibration requirements remain intact.

One launch check remains: the archive does not yet establish which controller
button activates the title screen's Continue option. Establish that mapping
from an observed button-and-result transition before using it in the adapter.
Synthetic tests and a highlighted option do not establish the mapping.

## Resume the saved run

Use the project launcher for a fresh Luna High session:

```sh
./scripts/orchestrator
```

Add `--run-id CURRENT_ATTEMPT_UUID` when resuming an identified attempt.
`--print-command` shows the command without launching anything. The launcher
sets Luna High and `--approve-for-me`, retaining the workspace sandbox while
eligible shell/network/socket approval requests go through automatic review.
It does not change global settings or promise that every request is approved.
See [OpenAI automatic review](https://learn.chatgpt.com/docs/sandboxing/auto-review).
The launch prompt authorizes the current attempt, but the reviewed adapter
still needs fresh identity, screen and bridge preflight before any input.

Close the old stopped Codex session before using this launcher. A fresh task
loads `skills/orchestrator/SKILL.md` from this checkout; resuming an old task
may retain earlier instructions or permission settings. In an existing task,
`/permissions` can select **Approve for me**, but that alone does not reload
product changes or resolve an outstanding game action.

Keep the PS5 feed open in QuickTime's **Movie Recording** window and leave other
Remote Play clients disconnected. Recording the video is not required. Codex
can now capture that window even while its own window covers it.
The QuickTime window may be on a second monitor; capture selects the window
itself. Keep the preview open and confirm each captured frame is current.

For a manually started task that has not yet received current-run authorization,
use the following prompt. Launcher users do not need to repeat it:

> Use Orchestrator for the currently visible Slay the Spire attempt. ARM ORCHESTRATOR FOR THIS RUN. Check the live screen and SQLite, bind that attempt, and verify every move through the reviewed adapter. Re-plan conservatively under uncertain game state: favor survival and the lowest defensible damage risk among supported actions. Use reviewed bounds, inspections and protective alternatives. Preserve unresolved input and report exact technical blockers; do not replay input or bypass checks. Stop at victory or defeat.

Orchestrator requires explicit current-run arming; the launcher supplies it.
General development approval does not start the controller. Codex first checks
the actual game and bridge, starts one persistent bridge, then sends each
reviewed move through the adapter. A title screen does not reveal current HP,
cards, relics or potions: saved telemetry is only an expectation until the
resumed game is inspected.

If play stops, Codex reports the last verified move and the unresolved result.
It inspects and reconciles that result before another input. It does not repeat
a button because a reply or animation was slow. Reopening a session leaves it
disarmed. A bridge acknowledgement means delivery only, not that a card played.
Use the [action-evidence policy](veda-action-evidence.md) to distinguish
supported game uncertainty, a recoverable inspection need and a session stop.
It preserves every existing adapter requirement.

## Bounded startup for the operator

First resolve which attempt is visible. For Neow's opening `[Talk]`, follow
[Neow registration and Talk](veda-neow-start.md): register the authorized
already-open attempt before arming, preserve the finished attempt's history,
and use the new IDs. Do not search historical card/rule libraries for the Talk
label or bind the opening screen to a terminal run. A resume-only instruction
for a different run still needs its scope resolved; broad current-attempt
authorization already given by the player should not be requested again.

Finish setup before taking the frame used to arm. Keep the player's chosen
model; startup diagnostics and request packaging are local operations.

1. Run `python3 scripts/veda_play_context.py` once, adding `--run-id EXISTING_RUN_ID`
   when the current task already identifies the run. It reads the current run,
   context IDs, counted inventory and both possible session directories without
   dumping historical inventory events or guessing SQL columns. Its facts are
   **historical expectations**, not a fresh screen reading. Resolve reported
   ambiguity or pending input; never create a new session directory to hide it.
2. Load these instructions and prepare command arguments. Confirm current-run
   arming, the intended game feed and the exclusive controller client. Start
   one warm bridge and wait for `ready`. Start one reviewed adapter using the
   chosen session directory and the existing run ID below.
3. The adapter itself needs permission to connect to the local socket. Approval
   for `warm_bridge` or `bridge_command.py` does not grant the adapter that
   permission. In Codex's shell tool, launch the adapter through the command
   approval flow with `sandbox_permissions: "require_escalated"` when local
   socket access is restricted. Use a command-scoped approval for
   `python3 scripts/veda_reviewed_play.py`; do not assume a successful separate
   status probe proves this process can connect. This follows the official
   [Codex sandbox and command-approval boundary](https://learn.chatgpt.com/docs/sandboxing).
4. Before capturing the arm frame, send the adapter
   `{"operation":"bridge_preflight"}` **followed by a newline**. Require
   `bridge_access_ready`. This probes from that same process and closes only
   its temporary client socket. It neither arms nor closes the warm bridge.
   `ready_unarmed` at process startup only means the adapter is running.
5. Capture and inspect a new game-window image. Then use the helper below to
   package the inspected image's original metadata, and immediately submit its
   short `request_file` response followed by a newline. Do not type a large
   review object into the terminal or repeat schema searches at this point.
6. Require `armed_codex_reviewed`, then use the normal one-action checks. If an
   image expires, finish the remaining setup before capturing again. Do not
   loop through old arm files, replace their timestamps, or mark an unseen
   replacement capture as reviewed. The **30-second limit is unchanged** for
   arming, preparing, sending and reviewing outcomes.

After actually inspecting `CAPTURE.png`, use the real run ID and a new output
path in an existing directory:

```sh
python3 scripts/veda_play_request.py arm \
  --run-id EXISTING_RUN_ID --capture /absolute/path/to/CAPTURE.png \
  --screen combat --reviewer 'Codex Orchestrator' \
  --evidence-note 'Inspected this exact image: game, current screen and visible state.' \
  --phrase 'ARM ORCHESTRATOR FOR THIS RUN' \
  --reviewed --exclusive-client-confirmed \
  --output /absolute/path/to/new-arm-request.json
```

The helper records the operator's explicit review; it does not inspect pixels,
confirm exclusivity, grant user authorization, capture a frame or send anything.
It checks the capture receipt, source bytes and filename, and uses
`capture_requested_at` rather than completion time or the current time.
Its output is the short pointer accepted by the persistent adapter. An arming
review confirms game identity and screen; each card still needs its complete
fresh combat review. Every JSONL request needs a final newline. The adapter
also removes its own PTY's short line limit and echo while running and restores
the terminal on normal exit or interrupt; file pointers remain preferred.

| Probe reason | Meaning and next step |
| --- | --- |
| `connect_permission_denied` | This process was denied local socket access before sending a command. Relaunch the adapter through command approval, then probe again. |
| `connect_socket_missing` | The specified socket does not exist. Check the owned warm session and exact socket path before capturing another frame. |
| `connect_refused` | Nothing accepted the connection at that socket. Inspect the owned bridge's exit/cleanup and readiness. |
| `connect_timed_out` | The local connection timed out before a command was sent. Inspect the owned bridge and its socket. |
| `connect_failed` | Another local connection error occurred before sending. Preserve the stop evidence for investigation. |

These local errors are not responses from the PS5. If delivery of an actual
gameplay command is uncertain, preserve its pending action and reconcile the
observed result; a successful later status probe never authorizes replay.

## Operator interface

The following interface is for the Codex operator, not an unattended launcher.
Startup itself sends no controller input. Use the project's configured Python
environment and the existing run ID; no credentials or device pairing change.

```sh
python3 scripts/capture_observation.py --game-window
python3 scripts/veda_reviewed_play.py artifacts/reviewed-play/CURRENT_RUN \
  --run-id EXISTING_RUN_ID --database artifacts/veda-memory.sqlite3 --mode codex
```

Start `./scripts/warm_bridge --idle-timeout 0 --socket /tmp/veda-ps5-bridge.sock` only after current-run arming and
preflight. Wait for its ready event. Keep the reviewed-play process alive so it
reuses that socket. Its JSONL input accepts a request object or
`{"request_file":"/absolute/reviewed-request.json"}`. A malformed request
disarms the adapter; it cannot fall through into a raw controller command.

The normal cycle is **prepare → send → verify**. `prepare` accepts reviewed
evidence and strategy, computes one permitted button, retains the source, and
returns its action ID. `send` requires that ID, fresh unchanged evidence and an
armed session; it durably records the decision before attempting input.
`verify` requires a later image and actual matching effects. Every new input
needs a new review. The source freshness limit is 30 seconds at preparation,
dispatch and outcome review; recapture and inspect when it expires.

An `arm` request names the exact current run, arming phrase, source, frame ID,
complete named review, `game: "Slay the Spire"`, identifiable `screen`, and
`exclusive_client_confirmed: true`. It checks the existing warm bridge's live
status without sending a gameplay button. The field is the operator's explicit
client inspection, not an automatic scan of other controller software.

The startup `ready` event and the later `status` response now check the same
owned live transport and running refresh loop. They do not repeat the separate
console-discovery query after connecting. `on: null` means power state was not
probed; it does not mean the console is off. Arming requires the correlated
status response, ready transport and running refresh, with no reported health
fault. A startup event alone is insufficient. A failed check stores a sanitized
`last_bridge_preflight` reason in the session summary/state so the next stop
report can distinguish a timeout, connection failure and unhealthy transport.

Common preparation fields are `operation: "prepare"`, `kind` (`combat` or
`choice`), `context` (run/floor/combat/turn IDs), `source`, and `reasoning`.
`source` uses the exact path, SHA-256, capture timestamp, `origin: "reviewer"`
and evidence note from the [telemetry contract](veda-play-telemetry.md).

For combat, include the current `Reading` contract described in
[execution runtime](veda-execution-runtime.md), a complete named `review`
bound to its frame ID/hash, and a `plan` containing exactly one card or End Turn.
The adapter checks visual/context agreement and the shared Spire rules. It does
not accept caller-provided “allowed” flags. Full hand, current costs, enemy hits,
powers, and confirmed relic/potion inventory are required. Strategy remains
Codex's responsibility, including relevant draw/discard inspections and potion
review; local arithmetic does not guarantee the best move or a win.

A non-turn-ending card uses the shared checker's immediate-action scope. Its
conditional enemy-turn forecast may still show insufficient defense; that does
not imply the card itself causes those incoming hits. Verify the card and re-plan
the remaining defense from a fresh frame. Actual End Turn, forced turn endings,
immediate lethal HP costs and every source/inventory/control check retain their
guards. See [forecast horizons and defensive prefixes](veda-action-evidence.md#a-card-action-is-not-automatically-end-turn).

## Game uncertainty and unreadable evidence

The game's readable **Unknown (not attacking)** tooltip confirms that enemy's
nonattack category; its exact move stays unknown. It is not a failed screen
reading. Record `intent: "Unknown (not attacking)"` (or
`"unknown_not_attacking"`), `intent_hits: []`, and zero **displayed attack**
damage only when the tooltip is actually readable. Preserve the source image.
Zero displayed attack does not mean zero danger: summons, buffs, debuffs and
other enemies can still matter. Bare `Unknown`, `?`, Runic Dome, or unread
numbers do not prove a nonattack and must not be converted to zero.

For Collector at A2, the reviewed alternative contract is:

```json
{
  "move": "Unknown (not attacking)",
  "intent_effects": [{
    "kind": "one_of",
    "moves": ["Buff", "Mega Debuff", "Spawn", "Revive"]
  }]
}
```

This is a conservative set of alternatives, not a claim that all four share
the same icon or occur together. It retains the existing source-bound
`reviewed_reference` evidence with `observed_intent: true`, which here means
the **category** was observed. The complete current roster, known modifiers,
confirmed inventory and matching A2 manifest remain required. The checked
forecast takes the largest reviewed one-turn bound across every alternative;
the exact move remains null. It includes possible immediate Torch Head attacks
after a summon, without predicting that they actually happen. Source-based
opening-Spawn expectations never become an observed exact move.

Use these facts to submit the next legal card to the normal checked adapter.
Before End Turn, require its conservative survival check, review potions when
accepting damage and review unused playable zero-cost cards. Re-observe after
each action; never execute the alternative branches as a sequence. Do not stop
solely because a readable nonattack category hides the exact move, or tell the
player to wait for that label to change. Stop when an action depends on an
unbounded effect, missing evidence, or an actual failed check, and report that
specific reason. This capability does not cover arbitrary bosses or hidden
attacks. Offline tests validate these contracts, not live screen recognition.

## Other choices and verification

For unfamiliar layouts and readable random outcomes, use the
[general-decision workflow](veda-general-decisions.md). Its route brief reports
visible connections and uncertainty without selecting a path. A verified option
can remain usable when another reachable icon is unclear. Map/event/reward
choices can declare 2–8 complete outcome alternatives; verification must match
exactly one and persists its derived `choice_outcome_id`. Keep every branch's
run, costs, resources and inventory constrained. This does not authorize new
controller bindings or arbitrary random inventory additions.

For menus, include the [choice observation and planned choice](veda-choice-execution.md)
as `observation` and `choice`, plus the reviewed `inventory`. Its semantic digest
must match the observation. Controller bindings use a current visible hint,
a previously reviewed transition, or an applicable explicit
[default control profile](veda-menu-controls.md). A missing on-screen glyph
alone is not a blocker when that profile covers the reviewed menu. Only title
Continue may carry unknown relic or potion coverage, because it loads the same
saved run. Opening an upgrade picker may retain unknown card coverage; inspect
and record the actual cards before choosing the upgrade.

Verification supplies `action_id`, a new UUID `operation_id`, and `after` with
the new source and reviewed reading/choice observation. Optional `telemetry`
contains explicitly observed inventory, zone and lifecycle changes. These need
an `after.mutation_review` with the exact source/frame, named reviewer,
`complete: true`, and identical `changes`. Navigation cannot consume an item or
move a card. Inventory events must agree with the reviewed resulting inventory.
Omitted zone coverage stays uncertain; input intent cannot establish a draw,
discard or exhaust result.

When a card opens a selection, or combat ends on a reward/result screen, use an
after choice observation. Its outcome review binds the action ID, before-frame
ID/hash, exact combat action and named observed result. A selection names the
causing card ID and verifies HP/energy. Combat completion names the actual
win/loss and closes the turn/combat in telemetry. New observed context IDs may
be temporary labels; explicit lifecycle changes are required and the returned
`next_context` supplies SQLite's canonical IDs for the next request.

## Recovery

- `summary` reports the durable pending action and SQLite recovery state.
- `cancel_prepared` discards only a proposal that never entered dispatch.
- `recover_unsent` handles failure before dispatch. If SQLite already contains
  its decision, supply a fresh source/review to record the proven unsent result.
- `reconcile` records `unknown`, retaining the pending action and invalidating
  inventory confidence. `not_performed` requires the bridge's correlated
  `not_sent` receipt; an unchanged picture alone is insufficient.
- `finalize` retries only the exact verified SQLite outcome after a storage
  interruption. It never repeats controller input.
- `resume` records a source-reviewed checkpoint for a paused run. It grants no
  controller authority and refuses unresolved input.
- `stop` disarms and requests warm-bridge closure. Check the owned bridge's exit
  and cleanup result: a close acknowledgement alone does not prove hardware
  release. If the input connection is unusable, a separate local connection
  requests closure only; it never retries the gameplay input.

The durable session file and private SQLite ledger are both required. Do not
delete either to clear a pending action. A conflicting external writer or an
uncertain physical-controller input requires explicit inspection and recovery.
Captured evidence and reviewed declarations remain private artifacts; tests do
not certify screenshot interpretation or hardware performance.
