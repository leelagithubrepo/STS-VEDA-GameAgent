# Continue through event and upgrade menus

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

The [inventory-baseline workflow](../.veda/workflows/spire-telemetry-v4.md#persistent-run-facts)
accepts JSON files, avoiding fragile shell-quoted card names. Put the observed
items in an object with an `items` list of `{kind, item}` entries, preserving
duplicate counts, and coverage in a separate `card`/`relic`/`potion` object:

```sh
python3 scripts/veda_memory.py inventory-baseline \
  --run-id CURRENT_RUN_ID --floor-id CURRENT_FLOOR_ID \
  --items @/absolute/path/to/observed-items.json \
  --coverage @/absolute/path/to/observed-coverage.json \
  --screenshot /absolute/path/to/inspected-picker.png \
  --source 'Inspected upgrade picker after verified opening'
```

Use `complete` only for fully inspected categories, then refresh compact play
context and the action image. This bookkeeping is not controller input.

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

This helper creates prepare requests only. Post-input after/outcome review uses
the existing [reviewed-play verification contract](veda-reviewed-play.md).
Prepare its structure before input, fill it from the actual resulting image,
and never rebind the before-action draft as proof that the action succeeded.

If preparation fails, report whether the failure came from capture, draft
validation, packet construction, adapter prepare, or dispatch. Preserve the
actual command error. A helper rejection is not evidence that the bridge or
game failed, and an empty shell variable does not identify a capture failure.

Custom controls, title/system menus and unsupported menu families retain their
own evidence requirements. Do not broaden a named rule to conceal an unexpected
screen. Preserve the exact failed check for Builder if no supported path exists.
Offline tests of this flow do not establish live hardware success or a complete
autonomous run.
