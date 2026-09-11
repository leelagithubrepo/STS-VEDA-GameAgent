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
all input, and releases every button/stick when it exits. VEDA should capture
the screen once after a completed decision—not after every focus movement—unless
the UI is ambiguous or the predicted transition fails to appear.

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
