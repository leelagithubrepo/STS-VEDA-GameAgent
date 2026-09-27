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

## Package the next reviewed request

Prepare the ordinary `operation: "prepare", kind: "choice"` packet described
in [choice execution](veda-choice-execution.md) with the inspected source,
inventory, observation and strategic choice. Then attach applicable controls:

```sh
python3 scripts/veda_menu.py \
  --request /absolute/path/to/fresh-reviewed-choice.json \
  --control-profile ps5-default-cross-confirm-v1 \
  --output /absolute/path/to/new-menu-request.json
```

The helper checks source bytes, matching review/context/inventory, the menu
profile and the next planned step. It exclusively creates the output file and
returns its `request_file` pointer. Submit that pointer to the already-armed
reviewed adapter. It is a prepare request, not a direct bridge command.
After every input, refresh the observation and profile bindings from the actual
result. A generated proof is a documented layout rule, not pixel recognition.

Custom controls, title/system menus and unsupported menu families retain their
own evidence requirements. Do not broaden a named rule to conceal an unexpected
screen. Preserve the exact failed check for Builder if no supported path exists.
Offline tests of this flow do not establish live hardware success or a complete
autonomous run.
