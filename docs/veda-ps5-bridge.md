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
