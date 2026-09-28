# Start Orchestrator from Terminal

Run the launcher at the normal shell prompt in a new macOS Terminal tab. A
shell prompt commonly ends in `%` or `$`. The `›` prompt belongs to an existing
Codex task and accepts instructions, not a command to start another task.

If another task owns the game controller, first ask that task to stop and
release its bridge. Then run in Terminal:

```sh
cd /Users/leela/Documents/Ps5-Slaythespire
./scripts/orchestrator
```

This starts a fresh Luna High task with the current learning-play instructions.
Actual gameplay still begins only after the game, attempt and controller
ownership are checked and the adapter is armed. Add `--run-id ACTUAL_RUN_UUID`
only when resuming that specific attempt.

To continue an existing Codex task, give it the play instruction there. Do not
run the launcher again inside its `›` prompt. If a replacement is needed,
stop the existing play task cleanly before using a new Terminal tab.

The launcher now rejects a detected nested launch before starting another
Codex process or writing a startup clock. It checks a bounded parent-process
chain. An ordinary Terminal/login/app boundary is allowed even when Codex
environment values were inherited. If process inspection is unavailable, it
requires the combined Codex shell-execution markers; a thread ID alone does
not reject a launch. This convenience check does not establish exclusive
controller ownership, which remains part of live preflight.

`./scripts/orchestrator --print-command` only prints the command. It does not
inspect processes, start Codex, write timing state or touch the game.
