# Campfire: choose once, observe the outcome, return to map

Use `scripts/veda_campfire.py` for a Rest site. No source-code search is needed.
It handles Rest, Smith, the existing upgrade picker/preview and the observed
Proceed/Leave button. It packages requests; the reviewed adapter sends input.

On entry make one `veda.campfire-snapshot.v1` with `context:"session"`, observed
`resources`, known `inventory` (preserve coverage), `facts` including the actual
act/floor/current_node_id and `node_type:"rest"`, and the actual `ui`. If the
room entry is still pending, verify it with the map room-entry result first.
Do not register a second floor or take another image solely for bookkeeping.

The initial UI has `menu_family:"campfire_options"`, `choice_id`, actual
`focused_id`, options with distinct `id`, visible `label`, `role:"rest"` or
`"smith"`, `enabled`, `costs:{}`, and a complete observed `grid` of cells
`{id,row,column}`. Include other visible options honestly; this handler does
not implement special relic/key campfire actions. A disabled option stays
unavailable. A visible heal amount can be recorded as `reward:{amount:24}`;
this is a forecast, never proof that HP changed. If unread, omit it.

Choose Rest versus Smith once using current HP, relevant relic effects,
useful upgrades and the next encounters. Do not choose Rest solely because
it is focused. Default PS5 Cross selects the focused enabled option; reviewed
grid positions provide adjacent direction taps without inventing button hints.

For a routine handoff, the helper applies a bounded fast path. With complete
card coverage, at least 75% HP and an unupgraded `Armaments`, it plans Smith;
at or below 50% HP it plans Rest. Either choice is made within a 10-second
decision budget, then the same pending action is carried through focus and
verification. A result that opens the picker goes directly to the picker
handler; it does not return to map planning.

```sh
python3 scripts/veda_campfire.py --snapshot CAMPFIRE.json \
  --choose ACTUAL_OPTION_ID --reason 'The current health/upgrade tradeoff.' \
  --decision-output NEW_DECISION.json --session SESSION_DIRECTORY \
  --capture INSPECTED.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Actual campfire options, focus and HUD inspected.' --reviewed --execute
```

Use the returned packet path with `veda_submit.py --request PACKET.json
--capture-after`. Explicit-run launches bind `VEDA_PLAY_SESSION` for that
process, so these two helpers can omit repeated `--session`; an explicit flag
always wins. For an existing player/advisory task keep `--session` in commands.
No global session is guessed from the latest run.

Capture once to the observation archive and display that exact image. Do not
capture an ephemeral image and then another image simply to retain evidence.
Binding validates internally. To preview this helper without creating a packet, omit capture/review/execute/output flags; it has no `--validate` flag. The separate `veda_menu.py --validate` is available for debugging a full menu draft.
All result commands take `--observed-result`, the actual inspected `--capture`,
reviewer, evidence-note and `--reviewed`. They derive the pending ID and output
filename. Never add `--execute` to a result.

| Actual result | Compact result flags |
| --- | --- |
| Only focus changed | `--result focus --focused-id ID --unchanged` |
| Smith opened the actual picker | `--result menu --ui ACTUAL_PICKER.json --unchanged` |
| Rest completed and exit is visible | `--result healed --actual ACTUAL_HP.json --ui ACTUAL_EXIT.json` |
| Selected upgrade preview | `--result upgrade_preview --preview ACTUAL_PREVIEW.json --unchanged` |
| Upgrade applied and exit visible | `--result upgraded --actual ACTUAL_UPGRADE.json --ui ACTUAL_EXIT.json` |
| Proceed returned to map | `--result map --unchanged` |

`ACTUAL_HP.json` contains `{"hp":ACTUAL_TOTAL,"others_unchanged":true}`.
`--unchanged`/`others_unchanged` require inspecting the other gameplay facts;
never copy predicted HP into a result. Changed gold, inventory or other effects
use a full `veda_menu.py --result` declaration. If healing directly opens the
map, likewise use the full result with actual HP and actual map UI.

`ACTUAL_EXIT.json` uses `menu_family:"campfire_exit"`, a choice ID, focused
exit ID and one enabled free option with `role:"proceed"`. Its `activate_hint`
names the actual visible button and text, e.g. `{button:"circle",hint_text:
"Circle Proceed"}` only when seen. Do not carry Rest's Cross into the exit.
The healed/upgraded helper records `campfire_phase:"resolved"` after inspection.

After verification, `--last-result` reuses the sealed actual screen and canonical
IDs. Use `--decision NEW_DECISION.json` through focus. A changed option set or
resources invalidates that decision. On the Smith picker, use the existing
`card_upgrade` UI from [upgrade menu controls](veda-menu-controls.md). Each option includes `id`, `label` equal to the current card name, `enabled`, `costs:{}` and `card:{name:"Strike",upgrade_name:"Strike+"}`, plus complete observed grid cells `{id,row,column}` and actual focused ID. Replace these example names with the inspected card. Choose a specific card ID with a reason once. Preserve
separate IDs for duplicate cards. The preview JSON contains `selected_id`,
`observed_upgrade_text` and its actual `confirm_hint:{button,hint_text}`.
After preview verification, `--last-result` keeps the selected upgrade without
another strategy pass. The actual upgrade JSON contains `upgraded_card_id`,
`upgraded_card_name`, `others_unchanged:true`; inventory replacement is logged
only after actual confirmation. Partial inventory requiring new discovery or
unexpected item changes use the ordinary full menu result/discovery tools.

At the resolved exit, `--last-result` automatically chooses Proceed without
another strategic decision. Verify arrival at the map before selecting a node.
An older session without retained menu state can use `--after-result` with an
exact sealed result, or one new inspected snapshot. These helpers cannot infer
pixels or prove live responsiveness. Targets remain 20 seconds per ordinary
logical move and 90 seconds per noncombat floor; measure actual completions.
