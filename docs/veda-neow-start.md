# Start at Neow and advance Talk

When the user authorizes the already-open attempt, the Orchestrator handles its
run record and the opening **Talk** action. The player should not repair SQLite
or choose a UUID. Neow's opening conversation, including “Another try...?” with
one focused `[Talk]`, is a known introductory screen. Read Neow, the character,
Ascension and resources from the actual game image; a Talk label alone does not
identify the event. Inspect the new reward choices only after Talk advances.

## Bind the attempt before arming

1. Read compact context and inspect one game-window capture to identify the
   current attempt **before starting the warm bridge or arming an adapter**.
   A corroborated floor/combat defeat is terminal even if an older ledger left
   `runs.status` active. HP zero alone and an ordinary combat victory are not
   terminal-run evidence. The adapter now refuses a terminal run before opening
   its controller connection.
2. Honor the user's current scope. An instruction to play the already-open new
   attempt authorizes its bookkeeping. A command explicitly limited to resuming
   a different run does not. Never continue into another attempt automatically
   after victory/defeat. Resolve a genuine authorization conflict once; do not
   repeatedly ask for authorization already given for this visible attempt.
3. If this is a newly observed Neow opening, copy
   `.veda/examples/neow-review.json` into the private session work area. Fill its
   fields from the exact inspected image and set `review_complete` true only
   after inspection. Record every visible relic and potion and their coverage;
   leave the unseen deck unknown, with only its visible count. Do not import an
   old run's inventory or mark a typical starter deck as observed.
4. Use the registration helper. `--previous-run-id` identifies the finished
   historical attempt to close, **not** the attempt to arm:

   ```sh
   python3 scripts/veda_neow.py register \
     --review /absolute/path/to/inspected-neow-review.json \
     --previous-run-id PREVIOUS_FINISHED_RUN_ID \
     --phrase 'ARM ORCHESTRATOR FOR THIS RUN' \
     --authorization-scope current_visible_attempt
   ```

   Omit `--previous-run-id` only when there is no prior matching ledger. The
   helper never captures, connects or presses a button. It validates the original
   capture receipt/time/hash, requires explicit authorization and review, checks
   pending decisions and saved adapter ownership, and atomically creates the
   run, floor 0, inventory baseline and evidence. A corroborated previous defeat
   becomes `completed` with defeat provenance; old floor/combat outcomes stay
   intact. A retry for the same registered attempt returns its existing IDs.
   Conflicting or already-progressed records require reconciliation rather than
   silently creating another run. An idle `CURRENT_RUN` pointer to a closed run
   stays historical and does not override the new canonical directory.
5. Use the returned `run_id`, `floor_id` and `session_directory` throughout.
   Follow [bounded startup](veda-reviewed-play.md#bounded-startup-for-the-operator).
   Finish setup and the adapter's `bridge_preflight` before the fresh arm frame.
   Confirm client exclusivity from an actual check; a healthy bridge alone does
   not prove it. If the process check needs command approval, obtain that check
   through the normal approval flow instead of declaring exclusivity from an
   earlier session.

The registration frame has a 30-second limit. Prepare the template and commands
before capture. If it expires, inspect a replacement frame; retain the previous
run ID on a registration retry. Registration does not arm or reset the freshness
clock. The first gameplay frame must also be newer than the new ledger entries.

## One reviewed Talk

For the inspected default PS5 layout, explicitly set
`control_profile: "ps5-default-cross-confirm-v1"`. This is the named default
Cross-confirm rule, **not** a claim that the screenshot contains a Cross hint or
that a hardware transition has already been observed. Do not select this profile
if controls are known to be remapped or their mapping is uncertain.

The opening review must identify Neow, act 1/floor 0, character/Ascension,
visible dialogue, resources and the complete single focused option:

```json
"visible_options": [{"id":"talk","label":"[Talk]","enabled":true,"costs":{}}],
"focused_id": "talk"
```

After arming, capture/inspect a fresh frame and update the compact review. Then:

```sh
python3 scripts/veda_neow.py talk \
  --review /absolute/path/to/fresh-neow-review.json \
  --run-id CURRENT_ATTEMPT_ID \
  --output /absolute/path/to/new-talk-request.json
```

Submit only the returned `request_file` pointer plus newline to the reviewed
adapter. Its `prepare` returns the pending action ID and exactly one Cross tap.
Send that action ID once through the adapter's normal `send` operation. Do not
send the helper's preview directly to the bridge. The named rule cannot confirm
reward choices, arbitrary Talk events, navigation, a paid option or another run.

## Verify the dialogue before deciding a reward

Capture and inspect the result. Update the compact review with the new capture,
actual dialogue, complete visible options, current focus and resources/inventory.
Set `facts.event_phase` to `dialogue` or `reward_options` as actually observed.
Each visible option still needs its observed label, enabled state and costs;
unknown rewards are not invented, and no new controller binding is implied.

```sh
python3 scripts/veda_neow.py talk-result \
  --before-request /absolute/path/to/new-talk-request.json \
  --review /absolute/path/to/inspected-neow-result.json \
  --action-id EXACT_PENDING_ACTION_ID \
  --observed-result 'Describe the actual dialogue or options that appeared.' \
  --output /absolute/path/to/new-talk-result-request.json
```

Submit the returned pointer to the adapter. It requires a later distinct source,
unchanged run/floor/resources/inventory and visibly advanced dialogue/options,
then records the outcome. A changed internal ID or bridge acknowledgement alone
cannot verify Talk. If input or outcome is unresolved, keep it pending and inspect;
never repeat Cross automatically. After a verified result, use normal Spire
advice for the newly visible reward choices.

The helpers use explicit reviewed declarations, not automatic pixel recognition.
Offline tests exercise separate run registration, one fake Cross dispatch and
fresh outcome logging. They do not establish successful live hardware play or
an end-to-end timing improvement.
