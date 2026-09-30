# Autonomous recovery and improvement

The live player pauses only for an external boundary: PlayStation power, Remote
Play transport, network, bridge, or loss of the game feed. The pending request
and its action ID stay on disk. The recovery watcher is started only after that
pause, so its periodic probes are not on the normal move path. It
records an `interruption`, then polls with bounded backoff; after a successful
probe result, the player loop re-preflights the same run, captures a fresh
frame, and reconciles the pending action before any new input. The watcher does
not relaunch the player by itself. An action whose delivery is unknown is never
blindly replayed.

Run the watcher from the project root when the player reports an infrastructure
failure:

```text
python3 scripts/veda_recovery_watch.py --run-dir artifacts/reviewed-play/<run-id> \
  --run-id <run-id> --reason "Remote Play feed lost" \
  --pending-action-id <action-id>
```

The resulting `recovery-events.jsonl` is a learning replay of the interruption
and recovery. It is safe to retain even when recovery fails.

## Queued combat cards

A plan may include one current step and a bounded `queue` (at most eight future
steps), for example `Strike, Defend, Defend, End Turn`. The player does not ask
the strategy model to reconsider between those steps when the verified hand,
energy, target, intent, and turn are unchanged. Each step still goes through
the normal focus, selection, and after-image acknowledgement. Unexpected hand,
intent, energy, target, or tooltip evidence must prevent
promotion and request a new plan. The current promotion guard checks the turn,
target, tooltip, complete hand, next-card visibility/playability, and hand UI;
the player must clear the queue when any additional surprising state change is
observed. This makes a queue a performance hint, not permission to send
unverified inputs.

## Hidden or raised cards

The card detector records `occlusion_evidence` whenever its popup, menu-text,
or overlapping-title heuristics indicate that another card may be hidden. It
sets hand completeness to unknown/false and never silently treats the visible
subset as the whole hand. The player must obtain a clear settled frame before
planning; an elevated Disarm frame is therefore treated as incomplete evidence.

## Continuous improvement checks

Timing phases now distinguish `model_inference` from `tool_wait`; verification
and recovery remain separate. This prevents a future report from presenting
all terminal and capture latency as strategy time. The launcher should mark
those phases around long-running work, while controller dispatch remains its
own phase.

At the end of a play block, review these independent signals:

* `recovery-events.jsonl`: interruption, recovery, and failed-recovery counts;
  compare recovery time by attempt.
* `scripts/veda_live_metrics.py`: observation cadence and p95 intervals.
* prediction telemetry and `scripts/veda_play_lessons.py`: predicted versus
  observed intent, resources, zones, and lifecycle outcomes.
* queue outcomes: completed queues versus invalidated queues, with the reason
  for each invalidation (instrumentation still to be added to the ledger).
* card-region `occlusion_evidence` and `hand_complete`: review possible hidden
  cards in archived frames; an incomplete frame is a correct abstention.
* focused regression tests and replay IDs recorded with each repair.

The reviewed session state also records `evidence_metrics`: source checks and
reuses by path, plus workflow recovery attempts. A routine menu action gets
one repair attempt; combat inspection gets two. Once the limit is reached, the
adapter reports the deterministic current-screen fallback instead of opening
another capture loop. Mandatory decision/outcome ledger writes remain durable
and synchronous because they protect against duplicate controller input;
optional performance metrics are kept in the sidecar state and do not add a
second database round trip to routine play.

Workflow mismatches are classified as `small_variation`, `known_family_gap`, or
`new_family_gap`. Small variations stay on the live hot path. The latter two
append a deduplicated record to `builder-gaps.jsonl` for Builder to consume
outside the player process; they do not wait for code changes or tests before
the current floor continues. Infrastructure failures remain a separate class
and use the connectivity recovery watcher.

An improvement is accepted only when the new test/replay passes, the measured
failure rate or latency improves, and no safety boundary is weakened.
