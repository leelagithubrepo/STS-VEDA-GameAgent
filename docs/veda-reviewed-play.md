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

Keep the PS5 feed open in QuickTime's **Movie Recording** window and leave other
Remote Play clients disconnected. Recording the video is not required. Codex
can now capture that window even while its own window covers it.
The QuickTime window may be on a second monitor; capture selects the window
itself. Keep the preview open and confirm each captured frame is current.

When ready to play, tell the active Codex task:

> Use Orchestrator for the current saved Slay the Spire run. ARM ORCHESTRATOR FOR THIS RUN. Check the live screen and SQLite, resume with Continue, and verify every move. Stop if a state or result is uncertain.

The installed Orchestrator skill requires that current-run arming statement.
General development approval does not start the controller. Codex first checks
the actual game and bridge, starts one persistent bridge, then sends each
reviewed move through the adapter. A title screen does not reveal current HP,
cards, relics or potions: saved telemetry is only an expectation until the
resumed game is inspected.

If play stops, Codex reports the last verified move and the unresolved result.
It inspects and reconciles that result before another input. It does not repeat
a button because a reply or animation was slow. Reopening a session leaves it
disarmed. A bridge acknowledgement means delivery only, not that a card played.

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

For menus, include the [choice observation and planned choice](veda-choice-execution.md)
as `observation` and `choice`, plus the reviewed `inventory`. Its semantic digest
must match the observation. Controller bindings require a current visible hint
or a previously reviewed transition; grid order alone is not enough. Only title
Continue may carry unknown inventory, because it loads the same saved run.

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
