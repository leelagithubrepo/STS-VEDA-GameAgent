# VEDA PS5 bridge

`scripts/bridge` owns a separate Remote Play client identity for VEDA. Its
configuration is stored only in `.ps5rmtctl/`, while its copied dependency and
virtual environment are stored in `.bridge-source/` and `.bridge-venv/`.
None of these are tracked or shared with other PS5 projects.

## Setup

```zsh
./scripts/setup_bridge.sh
./scripts/bridge setup
./scripts/bridge status
```

Run `setup` only when ready to pair VEDA with the console/account. Follow the
local pairing prompts; do not put a host, account identifier, token, or PSN
credential in source files.

## Operating policy

The bridge starts as observation/configuration infrastructure. VEDA must not
send controller input until her visual observer can identify the current UI and
her adapter can enumerate legal actions. A live action session must be
explicitly armed by the runtime that uses this bridge. Do not run another
Remote Play client simultaneously.

The wrapper binds its API locally and uses the VEDA-specific configuration
directory, preventing accidental reuse of another project's session state.

For the active Codex Orchestrator, use the
[reviewed play adapter](veda-reviewed-play.md) as the gameplay dispatcher. It
checks one reviewed move, records the pending decision before input, and binds
the observed result to SQLite. Keep one adapter and one warm bridge alive;
do not reconstruct the full history or restart the bridge for each card.
Capture the actual feed with `scripts/capture_observation.py --game-window`,
then inspect it. The window may be covered by Codex without changing the
captured source. This reviewed path is separate from the standalone runtime's
automatic-reader calibration requirements. The examples below describe the
transport; they do not replace per-run arming and reviewed input checks.

A readable game tooltip such as **Unknown (not attacking)** is deliberate
game uncertainty, not a bridge fault or unreadable frame. Follow the
[reviewed intent contract](veda-reviewed-play.md#game-uncertainty-and-unreadable-evidence)
to check supported alternatives. Keep exact hidden moves unknown and report
an actual evidence or planning blocker before stopping for intent uncertainty.

## Warm controller session

For autonomous play, do not invoke `scripts/bridge tap` once per input. Each
invocation establishes a new Remote Play session, which adds several seconds of
latency and makes directional input harder to verify. Start a single VEDA-owned
session instead:

```zsh
./scripts/warm_bridge --idle-timeout 0
```

It emits JSON Lines and accepts JSON Lines on standard input. The first response
is a `ready` event; all later responses include `latency_ms`.

```json
{"action":"tap","buttons":["right"],"delay":0.15}
{"action":"tap","buttons":["cross"],"delay":0.20}
{"action":"status"}
{"action":"close"}
```

The process retains the encrypted session only while it is running, serializes
all input, and attempts to release every button/stick when it exits; check its
cleanup result. VEDA captures and verifies the screen after every atomic input,
including focus movement, before sending another. Persistent connections and
compact state reduce overhead without removing that verification boundary.

The tracked `scripts/bridge_health.py` adapter retires the dependency's controller
thread and sends feedback on one event loop. While the armed session is open, it
refreshes the **current stick state every 200 ms**, including unchanged centered
sticks. It does not invent movement, replay button events, or reconnect after a
fault. Both feedback channels use a wrapping 16-bit wire sequence. This follows
the periodic state approach in
[Chiaki-ng's feedback sender](https://github.com/streetpea/chiaki-ng/blob/main/lib/src/feedbacksender.c).
The repair still needs a live idle-and-resume check before claiming that it fixes
this console's observed idle failure.

`ready` and `status` responses include a sanitized `health` object: refresh/send
counts, transport readiness, send/receive/heartbeat ages in milliseconds, the
last requested input's age, and a fixed error code if a fault was detected.
Unobserved ages are `null`. Server AFK values are reported as `afk_raw`, with
`afk_units: "unknown"`; no duration is inferred from those values. A local send
or cached `session_ready` flag does **not** verify delivery to the game, so
`input_delivery_verified` remains false and screen verification is still required.
Startup and status use the same `owned_live_transport` readiness contract:
ready transport, an active refresh loop and no health fault. Status does not
launch a second UDP discovery query; `on` is null and `power_state_probed` is
false. Arming uses this current transport response, not a discovery power flag.

Known transport closure or errors interrupt both idle waits and active commands,
emit a sanitized `controller_fault`, and close the session after best-effort input
release. The adapter does not infer a connection failure from an unverified
heartbeat cadence. A fresh session requires an explicit new launch after the
failure has been inspected. `--idle-timeout 0` disables the wrapper's command
inactivity timeout; a positive value closes the session after that many seconds
without a JSON command. This option does not change a physical DualSense's power
settings.

When the launcher cannot provide a persistent stdin/PTY, use the Unix-socket command channel instead:

```zsh
./scripts/warm_bridge --idle-timeout 0 --socket /tmp/veda-ps5-bridge.sock
python3 scripts/bridge_command.py /tmp/veda-ps5-bridge.sock '{"action":"status"}'
```

The socket stays available across client process or shell stdin closure. Transport faults still close the session and release inputs.

Offline verification (no Remote Play SDK or console required):

```zsh
python3 -B -m unittest tests.test_bridge_health -v
```

## Standalone watcher (no controller)

For VEDA outside VS Code, use `scripts/veda_watch`. It never imports this
bridge and never opens a Remote Play session. Its only output is passive screen
captures and `artifacts/standalone-status.json`.

```zsh
./scripts/veda_watch --once
./scripts/manage_veda_watcher.sh install
```

The optional launchd service starts at user login. Stop or remove it with
`./scripts/manage_veda_watcher.sh stop` or `uninstall`.

## Verified card input sequencing

The PS5 card UI can treat the first Cross as selection and a later Cross as
play or target confirmation. Use `veda.controller_state_machine.ControllerStateMachine`
to plan one input from each fresh observation. The planner clears tooltip/card
focus with `up` before End Turn and returns `triangle` only after a clean combat
observation. The warm bridge remains transport-only; never send the planner's
next step without capturing a fresh screen after the previous step.

## Timing and reward batching

Open and close every floor with `floor-start` and `floor-finish`; use
`run-timing --run-id RUN_ID` to see measured floors, missing evidence, and
floors over the 25-minute target. Missing finish evidence stays explicitly
unmeasured. At a reward screen, capture once, make the required visible UI
selections, then record all confirmed acquisitions with one `reward-collect`
transaction. This removes repeated screenshot and SQLite work while keeping
each item in the inventory ledger.
