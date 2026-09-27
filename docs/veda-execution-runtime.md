# Persistent execution runtime

Builder request: `acde1394-9a16-4fb6-bd93-364179700700`.

The runtime connects frame acquisition, a reader, shared advisory checks, local
card planning, UI navigation, a persistent bridge client, fresh verification and
a durable action journal in one process. The same loop runs an offline replay.
Imports and replay never start the controller bridge or capture the game.

**Current readiness: offline integration verified; full autonomous play remains
unvalidated.** No complete, independently validated runtime reader is bundled.
The existing local vision provider does not supply the full advisory/UI contract.
The new worker transport is an integration point for such a reader, not a claim
that recognition is solved. Current-deck effects, active powers, unfamiliar
encounters and card-choice screens still have explicit support gaps.

For the active Codex operator's reviewed play path, see
[Play with Codex Orchestrator](veda-reviewed-play.md). That adapter uses explicit
screen reviews and shares the pure combat input checks; it does not authorize
this standalone reader-driven runtime or replace its calibration requirements.

## Offline use

```sh
python3 scripts/veda_execute.py PATH_TO_MANIFEST.json \
  --trace artifacts/runtime/replay-trace.jsonl \
  --journal artifacts/runtime/replay-actions.jsonl \
  --report artifacts/runtime/replay-report.json \
  --max-inputs 3 --max-seconds 30
```

The manifest has `run_id`, `evidence_kind`, `frames`, `expected_commands` and an
optional `calibration` object. Each frame contains a unique `frame_id`, image
path, SHA-256, observation timestamp and recorded `Reading`. A Reading contains
the structured visual state, the complete advisory context, explicit UI phase,
ordered card/target IDs, focused/selected IDs, encounter, run, floor and turn IDs.

Recorded readings test execution logic; they do not validate image recognition.
Synthetic fixtures are labeled `synthetic_contract_not_vision_validation`.
`--mode shadow` proposes one action using the same checks without sending input.
Replay and shadow reject live socket, reader and arming options.

## What each action checks

The planner uses the advisory registry and observed current costs. It checks
upgrades, complete hand evidence, HP/energy/Block, per-hit enemy intent, powers,
inventory and modeled effects. It emits only the first checked action. Draw,
exhaust, generated-card and HP-loss effects require a new observation and plan.
Unsupported affordable cards prevent a fallback End Turn.

Live construction requires a persistent action journal. Its first append syncs
the file and newly created directory entries before any input; a sync failure
stops the loop. Every input has a unique request ID and an attempted journal record flushed to
disk before transmission. Focus movement, selection, targeting, card resolution
and End Turn each require a fresh frame. Every settled reading is checked for
visual/context agreement. A changed snapshot timestamp alone is not a game
transition. Confirmed journal records include the observed state and frame hash.

Before a potentially consequential card or End Turn input, the runtime saves
the before-image in the configured evidence directory. It saves the verified
after-image before marking that input verified. Files are addressed by their
content hash, written without overwriting existing evidence, and survive
working-cache eviction and shutdown. Failed or uncertain navigation also retains
the available before/after images. Successful focus-only moves keep their
temporary frame identities without archiving every intermediate image. A
bounded pending-image snapshot protects the before-image while animations are
checked. Retention failure before a consequential input prevents sending;
failure after sending leaves the action unresolved. These receipts preserve
evidence, not recognition accuracy or permission to act.

An uncertain send is never replayed automatically. The runtime attempts one
fresh reconciliation observation, stops and leaves the action unresolved.
Restart observes before stopping on unresolved actions; an operator must reconcile
them using evidence. A transport acknowledgement alone never verifies gameplay.
Ordinary capture-provider failures produce a structured stop report after the
existing bounded capture retry and adapter cleanup; they never repeat an input.
Stop requests, stale frames and the wall-clock budget are checked again after
planning/journaling, immediately before sending.

The bridge client reuses one serialized socket with bounded connection, write,
read and response sizes. The server serializes commands across clients and keeps
a bounded request-ID result cache. Duplicate IDs cannot repeat a command. A full
cache rejects new input instead of forgetting old IDs. Ambiguous outcomes latch
the session. This runtime does not start or reconnect a warm bridge.

## Recognition evidence and live prerequisites

Use independently inspected, hash-bound saved images and separate predictions:

```sh
python3 scripts/validate_runtime_vision.py \
  --manifest PATH_TO_LABELS.json --predictions PATH_TO_PREDICTIONS.json \
  --output artifacts/runtime/vision-validation.json
```

Omitting `--predictions` explicitly runs the existing local vision provider on
the saved images only. `--timeout-seconds` and `--max-frames` bound that operation;
`--save-predictions` preserves actual outputs for offline rescoring. This does not
capture the current screen or send controller input. The timeout is per image
(default 30 seconds); the default provider-run limit is 24 images, processed
sequentially. Total request time can approach image count times the timeout,
plus local processing overhead. Use a small explicit image limit for a short test.

A zero exit code from either CLI means a report was produced without a CLI error
(and, for execution, without unresolved actions). It does not mean recognition
was authorized, gameplay completed, or cleanup succeeded. Read `runtime_authorized`
and its blockers in validation reports, and `outcome`, `reason`,
`unresolved_actions` and `cleanup_errors` in execution reports.

Runtime recognition requires at least 12 distinct, fully labeled combat images
for every required field, at least 95% accuracy per field and zero unflagged
critical errors. Missing/unknown values remain in the denominator. Repeating the
same image cannot increase coverage. Partial nested objects cannot certify full
hand, enemy or advisory state. The evaluator records who declared independent
labels; it does not claim to prove that declaration.

Live authorization additionally binds that evidence to the exact worker command,
declared code hashes, model, prompt, preprocessing and output contract. Worker
responses must identify the same recognizer and exact input frame. Saved replay
provenance cannot authorize live control. None of these checks replaces the
Orchestrator skill's separate per-run arming and ready-bridge preflight.

## Timing and storage

Monotonic traces correlate run, floor, turn, decision, action, frame and request
IDs. Spans cover acquisition, image preparation, state extraction, parsing,
consistency/rules, planning, controller round trip, animation/verification,
telemetry and recovery. `input_verification` measures the complete input and
verification path including durable logging and action-evidence retention.
`action_evidence` reports retention work separately. `evidence_errors` identifies
failed diagnostic retention; it does not resolve an uncertain action. The local vision provider separately
measures its actual `model_request_round_trip`; this is not internal thinking time.

Reports include total wall time, active time, explicit pause/maintenance intervals,
overlap-aware stage totals, unaccounted active time, retries, errors, sample counts,
p50/p95 and decision routing coverage. A local decision that later fails
verification remains in the routing denominator and is excluded from successful
local coverage. No timing instrumentation itself calls a model.

The recorder supports explicit pause/resume and maintenance boundaries. This
bounded CLI stops on interruption; it does not infer pauses from screenshot gaps
or automatically resume after an operator intervention. Calendar ledger intervals
and observation cadence remain historical record intervals, not active-play time.

Live frame storage uses one temporary directory and a small bounded ring. Every
frame, including an ephemeral one, has a UUID and content hash. A boundary frame
can be retained before cleanup. Live-frame age starts at the capture request,
so capture, encoding and hashing cannot restart the freshness clock. This is a
conservative bound, not proof of exact rendering time or correct viewport. The
default still uses the existing screenshot subprocess; persistent in-memory
OS capture is not implemented.
Expensive public reports, exports and website updates are outside the action loop.

## Evidence from this implementation

The private `artifacts/performance-integration/` directory contains the baseline
diff, synthetic contract replay, correlated trace/journal, independently labeled
archived images, actual local-provider predictions and scoring reports.

The contract replay covers one decision, three fake inputs and four synthetic
frames with zero unresolved actions. It verifies wiring and stop behavior, not
live performance or recognition accuracy.

The first two archived-image requests both returned truncated JSON. A diagnostic
response reported 4,059 prompt tokens and only 37 generated tokens before its
context limit. The provider now requests an 8,192-token context and a bounded
2,048-token response, and explicitly rejects truncated output. Retesting produced
one parseable combat response with several critical values unknown; the map
request reached its 30-second deadline. Neither report authorizes live operation.

A separate [saved-frame reader](veda-saved-frame-reader.md) now combines native
macOS OCR for HP/energy with experimental card candidates. Its output is always
partial and is not connected to this runtime or eligible as a calibration report.

The future targets remain ordinary supported combat under 10 minutes and routine
input-plus-verification median at most 2 seconds / p95 at most 5 seconds. They
have not been demonstrated. Synthetic timings and failed/partial recognition
request timings must not be presented as achieved gameplay speed.
