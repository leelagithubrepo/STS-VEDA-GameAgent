# Continue through event and upgrade menus

For map inspection, route comparison, node focus/activation and room-entry
results, use [the map workflow](veda-map-play.md). The same compact request and
result CLI handles those scoped map families.

A missing button glyph is not, by itself, missing game state. When the operator
has explicitly selected the unchanged default PS5 control layout, use its named
menu rules. Keep that rule separate from evidence actually seen on screen.
Never manufacture a visible hint or a previously observed button transition.

`veda.menu_controls.bind_reviewed_menu_controls` supplies scoped bindings for
an ordinary reviewed choice observation. It neither presses a button nor
changes the ledger. The reviewed adapter still owns arming, exact source bytes,
freshness, costs, one pending input, outcome verification and inventory changes.

## Resume an existing Neow reward menu

Keep the current run and floor IDs. A verified Talk that already revealed
rewards must not be replayed, and this attempt must not be registered again.
Read the full visible options and current focus from a fresh image. If choosing
the free **Upgrade a Card** option, its first checked action opens the picker;
it does not select a card or claim that an upgrade happened.

For the default layout, put these declarations in the ordinary observation's
`ui` object:

```json
{"menu_family":"event_options","control_layout":"ps5_default"}
```

Use `screen: "event"`, the actual complete options and focus, immediate
selection, quota one, and explicit reviewed costs. The named profile supplies
Cross for the focused enabled event option. It does not infer unseen rewards,
unknown costs or mappings for remapped controls. The normal choice contract
still constrains all resources, run/floor identity and inventory.
Also set `facts.event_id` from the identified event. At this Neow screen,
`event_id` is `neow` and the observed `event_phase` is `reward_options`;
`opening_dialogue` belongs to the separate Talk rule.
If the preferred option is not focused, an optional complete `ui.grid` with the
same explicit row/column structure below supplies adjacent directional steps
for this event menu. Verify each focus change before activating. Without that
geometry, use a separately reviewed navigation mapping; do not invent wrapping
or jump to an unseen option.

For opening a free upgrade picker, declare the next screen as `selection` in
phase `choose`, with unchanged resources, context and inventory. Keep stable
game facts exact; allow only the particular picker/dialogue fields whose
contents will be revealed. Inspect and verify the resulting picker before
another input. Do not predeclare a particular card as selected.

## Inspect, choose, preview, confirm

Read the actual card identities, upgrade states, visible positions and focus.
Duplicate cards need distinct IDs. Preserve unseen cards as unknown. A cropped
or paginated view is not a complete grid; inspect the missing view through a
supported control before declaring completeness. Do not fill the deck from a
remembered starter list.

Opening the picker may retain unknown card coverage in its unchanged inventory
contract. After verifying that opening, record the newly observed deck baseline
using the existing telemetry workflow, then take a fresh source for the next
request. A discovery is not a card acquisition. Do not combine a new inventory
digest with an opening contract that promised an unchanged digest.

The [inventory workflow](../.veda/workflows/spire-telemetry-v4.md#persistent-run-facts)
accepts JSON files, avoiding fragile shell-quoted card names. Put the observed
cards in an object with an `items` list of `{kind, item}` entries, preserving
duplicate counts. Declare only the category actually inspected:

```sh
python3 scripts/veda_memory.py inventory-discover \
  --run-id CURRENT_RUN_ID --floor-id CURRENT_FLOOR_ID \
  --items @/absolute/path/to/observed-items.json \
  --categories '{"card":"complete"}' --reviewer 'Codex Orchestrator' --reviewed \
  --screenshot /absolute/path/to/inspected-picker.png \
  --source 'Inspected upgrade picker after verified opening'
```

Use `complete` only for fully inspected categories. Unmentioned categories
retain their prior ledger evidence, then refresh compact play context and the
action image. This bookkeeping is not controller input.

Ask Spire to compare the actual upgrades with the current deck and known route.
Do not ask the player to make an ordinary upgrade decision. Card choice follows
inspection; an expected starter deck or the first highlighted card is not a
substitute for that inspection.

The upgrade observation uses these `ui` fields in addition to its ordinary
options and source-bound review:

```json
{
  "menu_family":"card_upgrade",
  "control_layout":"ps5_default",
  "selection_purpose":"upgrade",
  "grid":{"complete":true,"cells":[
    {"id":"card-a","row":0,"column":0},
    {"id":"card-b","row":0,"column":1}
  ]}
}
```

Each option includes `card: {"name":"observed name", "upgrade_name":"reviewed upgrade name"}`.
Use the actual complete positions, not this synthetic two-card example.
Use `selection_mode: "toggle"`, `required_count: 1`, phase `choose` and initially
empty selected/pending lists. This describes selecting one preview before the
separate confirmation, not an immediate permanent upgrade.
The named profile covers nonwrapping movement between reviewed neighboring
positions and selecting a focused card. It does not select off-screen cards
or assume wraparound. Send only one direction or selection at a time, then
verify its actual effect.

Selecting a card opens a preview. Inspect the exact chosen card and its upgrade
text. Record `ui.upgrade_preview` with `option_id`, `before_name`, `after_name`
and `observed_upgrade_text`. The selected and pending IDs must name that same
card. Bind `ui.confirm` to the freshly visible confirmation hint using the
ordinary `visible_hint` proof; read its actual button rather than assuming it
is Cross. Preview and confirmation are separate inputs.

The final choice predicts the exact resulting deck digest and retains numeric
resources and the run/floor context. Its postcondition facts must include
`upgraded_card_id` for the chosen option and `upgraded_card_name` matching that
option's reviewed `card.upgrade_name`; a different card is not a valid result.
After confirmation, verify the actual upgraded card and record one
`replaced` card inventory event with its `related_item`, together with the
source-bound mutation review required by the adapter. Preserve duplicate card
counts. If the result is unresolved, retain the pending input and inspect;
never press confirmation again to see whether it worked.

## Prepare the decision before taking the action image

Finish schema discovery, strategy and draft repair **before** the action image.
The 30-second window is for inspecting, binding and submitting that image.
Do not spend it copying hashes, rewriting source fields, or typing repeated
review objects. Do not use shell substitutions or regex replacement to refresh
a packet; failure can leave an old or empty source attached to a complete review.

Start from [the compact opening example](../.veda/examples/menu-upgrade-open-draft.json).
It is an example of previously seen options, not current game evidence. Replace
the run/floor IDs and all gameplay facts with the inspected state and ledger.
Keep the current attempt; do not replay Talk or register another run.

A `veda.menu-draft.v1` contains `context`, `inventory`, `resources`, `facts`,
`ui`, `choice`, and `reasoning`. It contains **no** capture, timestamp, hash,
review or controller proof. The compact `ui` names its menu family, stable
choice/layout IDs, complete options, focus, and any required grid. The builder
derives ordinary order, selection mode, quota and screen from the menu family.
For a preview, explicitly supply phase `confirm`, selected/pending IDs,
`upgrade_preview`, and `confirm_hint: {"button":"actual visible button",
"hint_text":"actual visible hint"}`. Inspection of the final image must confirm
that hint. The helper generates its source-bound proof.

The compact choice names `kind`, `option_ids` and `postconditions`. Its
postconditions use `inventory: "unchanged"` or the exact expected inventory
object; the builder computes the digest. Outcome resources/facts stay explicit.
Omit outcome `context` to retain the current context. There is no reason to
compute a frame ID or duplicate a review by hand.

Validate the decision before capture:

```sh
python3 scripts/veda_menu.py --draft /absolute/path/to/menu-draft.json \
  --validate --control-profile ps5-default-cross-confirm-v1
```

This checks the choice/schema/profile only. It cannot authorize an input or
produce an action request. Fix any reported draft errors now. Keep the adapter
and bridge ready, with no pending action, before entering the short live path:

1. Run `python3 scripts/capture_observation.py --game-window` as a standalone
   command. If a specific Movie Recording window was already identified, add
   `--window-id ACTUAL_WINDOW_ID`. An empty result or nonzero exit means no
   capture; stop this attempt before packaging and read the actual error.
2. View that exact returned image. Confirm the draft's resources, inventory,
   options, focus and chosen action still match. If something changed, revise
   and validate the draft before taking a new action image.
3. Bind the reviewed image in one call, using the literal returned path:

   ```sh
   python3 scripts/veda_menu.py --draft /absolute/path/to/menu-draft.json \
     --capture /absolute/path/to/inspected-frame.png \
     --reviewer 'Codex Orchestrator' --evidence-note 'Actual inspected state and limitations' \
     --reviewed --control-profile ps5-default-cross-confirm-v1 \
     --output /absolute/path/to/new-menu-request.json
   ```

4. Immediately submit the returned `request_file` pointer to the already-armed
   adapter, then send its one prepared action ID. Read and verify the result
   before any next action. Avoid unrelated narration, code search or setup
   between inspection, preparation and send.

The helper reads the image's original capture receipt and derives all source,
frame and review fields together. It rechecks bytes, receipt, time and the
decision before exclusively creating a new file. It never overwrites the draft
or an earlier packet, captures a replacement, or claims inspection automatically.
An expired frame needs a new inspected capture, not a timestamp change.
`--request` remains available for existing complete packets, but is not the
recommended live preparation path. A generated profile rule is not pixel
recognition, and the builder itself never dispatches input.

`--draft` creates prepare requests. After input, use the compact `--result`
path below; it builds the normal reviewed-play verification request. Never
rebind the before-action draft as proof that the action succeeded.

If preparation fails, report whether the failure came from capture, draft
validation, packet construction, adapter prepare, or dispatch. Preserve the
actual command error. A helper rejection is not evidence that the bridge or
game failed, and an empty shell variable does not identify a capture failure.

Custom controls, title/system menus and unsupported menu families retain their
own evidence requirements. Do not broaden a named rule to conceal an unexpected
screen. Preserve the exact failed check for Builder if no supported path exists.
Offline tests of this flow do not establish live hardware success or a complete
autonomous run.

## Verify the result with the same helper

Read this path before sending a menu input. Do not discover Python schemas,
calculate source hashes, copy the entire grid, or type full JSON into the
adapter terminal after an action. Submit only a short `request_file` pointer.

For a navigation result, the complete source-free result draft is:

```json
{
  "schema": "veda.menu-result.v1",
  "action_id": "EXACT_PENDING_ACTION_ID",
  "resources": "unchanged",
  "inventory": "unchanged",
  "facts": "unchanged",
  "result": {"kind": "focus", "focused_id": "ACTUALLY_OBSERVED_CARD_ID"},
  "observed_result": "The inspected focus moved to the named card; other declared fields are unchanged."
}
```

`unchanged` is an explicit inspection declaration. It copies the known prior
values only after the reviewer confirms them against the actual result. The
helper compares observed focus to the pending action's expected destination;
the caller never supplies the old frame ID or a verification operation UUID.

For an observed upgrade preview, replace `result` with:

```json
{
  "kind": "upgrade_preview",
  "selected_id": "ACTUALLY_OBSERVED_CARD_ID",
  "observed_upgrade_text": "Actual visible upgraded effect",
  "confirm_hint": {"button": "triangle", "hint_text": "Confirm"}
}
```

Use the actual button and text. The helper retains the selected card's identity
and layout from the pending action, removes grid controls and binds the newly
observed hint. A highlighted card without the preview cannot satisfy this step.

For a newly opened picker, `result` is `{"kind":"menu","ui": ...}` with its
complete compact `card_upgrade` UI: actual options, focus and grid. Keep action
inventory `unchanged` while verifying the opening. Once the pending action is
clear, record newly discovered card knowledge through `veda_memory.py
inventory-discover`, using the exact inspected picker screenshot, explicit
items and reviewed categories. Do not claim the opening acquired those cards or infer a
starter deck that was not fully visible. Do this once; navigation retains it.

For a completed upgrade, set `inventory` to `selected_upgrade_applied` only
when the inspected selected-card preview and actual confirmation transition
establish that replacement. This declares exactly one known selected card
changed to its upgrade and everything else stayed unchanged. Supply the actual
result `facts` and a `result` menu UI containing `screen`, `phase: "result"`,
`choice_id`, `layout_id`, `options` and `focused_id`. Each result option has
`id`, `label`, `enabled` and `costs`. Result-only menus have no controls.
The helper creates the exact replacement event, its required evidence note
and source-bound mutation review together. Describe the evidence honestly:
Neow's Granted screen shows event advancement; Bash+ text was in the preceding
preview, not on that result screen.

Validate the compact actual result before the final evidence capture if any
structure needs repair:

```sh
python3 scripts/veda_menu.py --result /absolute/path/to/result-draft.json \
  --session /absolute/path/to/current-session/state.json --validate \
  --control-profile ps5-default-cross-confirm-v1
```

Then capture and **view that exact new image**, bind and immediately submit:

```sh
python3 scripts/veda_menu.py --result /absolute/path/to/result-draft.json \
  --session /absolute/path/to/current-session/state.json \
  --capture /absolute/path/to/exact-inspected-after.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Actual inspected result and limits' \
  --reviewed --control-profile ps5-default-cross-confirm-v1 \
  --output /absolute/path/to/new-result-request.json
```

No `send` follows a verification pointer. Read the adapter's response: only
`verified` with cleared pending state completes that step. Each new capture
needs its own image inspection, even when it looks unchanged; reviewing an
earlier image is insufficient. A replacement filename never inherits review.

## Finish Neow and recover a logging failure

After the upgrade is logged, use `menu_family: "event_leave"` for the single
focused free Leave option at Neow's Granted dialogue. Keep `event_id: "neow"`,
`event_phase: "reward_resolved"`, `dialogue_text: "Granted..."` (the actual
visible text), Act 1 and floor 0 exact. The new **action** UI uses `phase:
"choose"`; do not copy the verification-only `result` phase or quota zero.
For example, the compact next draft's `ui` is:

```json
{
  "menu_family": "event_leave", "choice_id": "neow-leave",
  "phase": "choose", "focused_id": "leave",
  "options": [{"id":"leave", "label":"[Leave]", "enabled":true, "costs":{}}]
}
```

Preserve the reviewed `facts` including `event_id: "neow"`,
`event_phase: "reward_resolved"`, `dialogue_text: "Granted..."`, `act: 1`,
`floor: 0`, and any confirmed character, ascension and upgrade facts. The
helper derives the action quota of one. Use an event choice for `["leave"]`
with map `result` postconditions, unchanged inventory/resources/context and
the same other facts; only `event_phase`/`dialogue_text` may be explicitly
allowed to change. The named default
PS5 Leave rule supplies Cross and permits only map arrival with unchanged
resources, inventory and context. Do not relabel this as `reward_options`.
Verify the map arrival before planning a route. A map `result` observation can
confirm arrival without inventing node choices; the next move requires a new
actual map review. Neow is complete only after this transition is verified.

Outcome metadata is validated before a new verified result is frozen for
logging. If a journal is already `verified_pending_log`, do not submit another
`verify`, replay Confirm, re-arm, or edit state.json. `finalize` retries its
exact durable write. A legacy outcome missing an inventory-event evidence note
has a bounded `repair_outcome_metadata` operation documented in
[reviewed play](veda-reviewed-play.md#repair-retained-outcome-metadata).
It preserves all game facts, IDs, original image and capture time, adds only a
reviewed missing note, then finalizes once. Changed ledger/source or a previously
committed operation rejects the repair and remains pending for reconciliation.
