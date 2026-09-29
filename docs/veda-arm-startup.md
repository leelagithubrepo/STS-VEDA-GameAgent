# Prepare arming before the fresh image

Use this after current-run authorization, identity/ownership checks and a
successful adapter `bridge_preflight`. Arming confirms game/run identity and
the screen. Complete map-route analysis belongs after arming, before the
corresponding map action; it is not part of the arm-image review.

Prepare this source-free draft before capture, with the actual authorized run
and expected screen from the planning image:

```json
{
  "schema": "veda.arm-draft.v1",
  "run_id": "ACTUAL_RUN_ID",
  "screen": "map",
  "reviewer": "Codex Orchestrator",
  "phrase": "ARM ORCHESTRATOR FOR THIS RUN",
  "exclusive_client_confirmed": true
}
```

The phrase and exclusivity are the operator's established declarations, not
facts the helper discovers. There is deliberately no completed review or
prewritten claim about unseen pixels in this draft.

```sh
python3 scripts/veda_arm.py validate --draft ARM_DRAFT.json
```

Choose new stage/request files in the existing session directory. Capture,
stage and display the exact image in one tool invocation, avoiding a separate
model round trip between capture and viewing. For the desktop tool runtime:

```javascript
const result = await tools.exec_command({cmd:
  "python3 scripts/veda_arm.py stage --draft ARM_DRAFT.json --capture-new --output NEW_STAGE.json --request-output NEW_ARM_REQUEST.json"});
text(result);
if (result.exit_code !== 0) exit();
const preview = JSON.parse(result.output);
store("armPreview", preview);
image((await tools.view_image({path: preview.image_path})).image_url);
```

The preview is **unreviewed and non-dispatchable**. It reports its run, expected
screen, image path, immutable stage hash and original expiry. It cannot arm the
adapter. Never substitute an older/latest image after a capture failure.

After inspecting the emitted image, give the short acknowledgement. Use the
stage path/hash from the emitted preview, the confirmed run and observed screen,
and a brief actual observation. Prepare the command structure before capture;
do not begin documentation lookup or route planning after viewing.

```sh
python3 scripts/veda_arm.py confirm \
  --stage NEW_STAGE.json --stage-sha256 EMITTED_STAGE_HASH \
  --run-id ACTUAL_RUN_ID --screen map --reviewed \
  --note 'Map visible; Ironclad A2, HP and gold match the current run.'
```

Immediately submit the returned packet path with `veda_submit.py --session SESSION_DIRECTORY --request NEW_ARM_REQUEST.json` to the already running adapter with `--request-server`. Only the adapter can arm. Do not submit the
stage file or repeat `arm` after successful arming. Continue to the checked next
action in the same turn unless the user asked only to arm.

Confirmation pins the staged draft, source bytes and receipt. It delegates to
the ordinary arm writer and the adapter's unchanged checks. Expiry always uses
`capture_requested_at`; staging, viewing and acknowledgement never refresh it.
The **30-second limit remains**. At expiry, retain the validated draft, resolve
the actual delay, and capture/stage/view a replacement. Every replacement must
be inspected; helper failures count against the existing startup clock.

Neither helper recognizes pixels, discovers a run, connects to the console,
sends input, or infers review. A model that still cannot inspect and acknowledge
within the window remains a measured live limitation; offline tests do not
establish that this timing target is met.
