# Quick combat loot

Gold and opening card offers need no strategic comparison. Inspect the rows
and focus, collect free gold, verify the outcome, then open the offers using
that verified state. Do not insert a separate SQLite update, report, boss lookup
or repeated deck analysis between these steps. The adapter already writes the
small durable outcome before allowing the next input. Defer optional summaries
until the reward sequence is complete.

Routine reward rows are a fast path. Gold, an empty-slot potion, opening the
card offers, and a focused Proceed row get one compact request, one actual
after-image, and one result packet. Do not create planning notes or run helper
availability checks between them. Card selection remains the one deliberate
reward decision.

Compare the offered cards with the current deck once, including the option to
skip. Retain that decision through focus, selection and confirmation. Opening
the offers is routine; adding a card is a strategic choice.

## Describe the screen once

Use `veda.loot-snapshot.v1` with `context:"session"`, inventory, resources,
facts and UI. The helper resolves canonical IDs from the verified session.
`facts.reward_source` must be `"combat"`. Use `ui.menu_family:"loot_rewards"`
for loot rows or `"loot_cards"` for offers. Keep actual focus, stable option IDs
and visible grid geometry. [The synthetic example](examples/loot-snapshot.json)
is schema guidance, not current run evidence.

Rows use roles `gold`, `potion`, `card_reward`, `relic` or `proceed`; offers use
`card` or `skip`. Gold has `reward:{amount:N}` and items have
`reward:{name:"Actual item name"}`. Record visible costs. Supply an actual
`activate_hint:{button,hint_text}` for Skip/Proceed or another visible control;
otherwise the default PS5 profile selects the focused row with Cross and moves
between adjacent observed grid cells. Verify real console outcomes.

```sh
python3 scripts/veda_loot.py --snapshot LOOT.json --session SESSION/state.json \
  --capture INSPECTED.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Actual reward rows, focus and resources inspected.' --reviewed --execute
```

Submit the returned `request_file` object unchanged to the armed adapter. This
helper packages one step and never sends input itself. Paths are generated
under `SESSION/loot-packets`; `--output` remains available. Omit binding flags
for a compact preview; `--verbose` includes the full draft.

## Record only the observed changes

Inspect the settled after-image. Use `--result` with the fields below; action
IDs and result paths come from the pending session. Every result also needs
`--session`, `--capture`, `--reviewer`, `--evidence-note`, `--reviewed` and
`--observed-result 'Short description of the actual outcome.'`. Submit its
returned pointer and wait for `verified` before preparing another input.
Keep this note concise; its maximum is 2,048 UTF-8 bytes.

| Result | Actual observation fields |
| --- | --- |
| `gold` | `--ui REMAINING_ROWS.json --actual GOLD.json` |
| `offers` | `--ui OFFERS.json --unchanged` |
| `focus` | `--focused-id ACTUAL_OPTION --unchanged` |
| `confirmation` | `--focused-id SELECTED_CARD --hint-button cross --hint-text 'Cross Confirm' --unchanged` (use the actual hint) |
| `acquired` | `--ui REMAINING_ROWS.json --actual ITEM.json` |
| `returned` | `--ui REMAINING_ROWS.json --unchanged` (e.g. after Skip) |
| `map` | `--unchanged` |

`--ui` contains just the actual menu UI, including remaining rows/offers, grid
and focus. Do not assume where focus moved after a row disappeared.
`GOLD.json` is `{"gold":56,"others_unchanged":true}` with the observed total.
`ITEM.json` is `{"acquired":{"kind":"card","name":"Headbutt"},"deck_size":13,"others_unchanged":true}`
with the actual acquired item and deck count (`kind` also supports potion/relic).
These numbers/names are examples. `--unchanged` or `others_unchanged:true` means
the other resources, inventory and facts were inspected unchanged, not guessed
from a successful input acknowledgement. Unexpected broader changes use the
full `veda_menu.py --result` declaration.

A highlighted card has not been acquired. The first Cross selects it; verify
its actual confirmation screen with unchanged inventory. Then confirm using
the visible hint and record acquisition only after observing it. If the actual
UI differs, reconcile that outcome instead of replaying the tap.

If a delivered commit leaves the same reward UI unchanged, submit the observed
no-op reconciliation once, retain the verified UI, and immediately plan the
next control from its visible hint. Do not stop, re-arm, modify code, or replay
the first input.

## Immediately reuse the verified result

After gold verification, this opens the card offers (or collects an available
potion into a confirmed empty slot first):

```sh
python3 scripts/veda_loot.py --last-result --session SESSION/state.json \
  --capture EXACT_VERIFIED_AFTER.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Same inspected verified reward state.' --reviewed --execute
```

No new snapshot, inventory rewrite or recapture is needed if nothing changed
since that inspected result. State and action tracking enforce continuity.
`--after-result VERIFIED_RESULT.json` can reuse a verified result written with
a custom path. Changed or unverified packets cannot supply the next state.

After a verified `--result map`, leave the loot helper: use the same inspected
map image and canonical session context with [quick map travel](veda-map-travel.md).
Trace the current outgoing edges and enter the sole reachable node, or make
the actual route choice. Loot `--last-result` intentionally does not plan map
input; a `no longer a loot screen` message means hand off to that map flow.

After offers are verified, make the card/skip decision and prepare its first
step together:

```sh
python3 scripts/veda_loot.py --last-result --session SESSION/state.json \
  --choose ACTUAL_CARD_ID --reason 'Concrete benefit to the current deck, or reason to skip.' \
  --decision-output CARD_DECISION.json \
  --capture EXACT_VERIFIED_AFTER.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Actual offers and focus inspected.' --reviewed --execute
```

For further focus/confirmation steps, replace `--choose`, `--reason` and
`--decision-output` with `--decision CARD_DECISION.json`. An actual confirmation
can also continue automatically using its already selected card. Changed
rewards, inventory, resources or run/floor invalidate the saved choice;
rebinding control hints or changing focus does not.

Free ordinary gold, an available potion with a confirmed empty slot, opening
offers and Proceed when rewards are handled have deterministic priorities.
A full/unknown potion belt or Sozu requires a tradeoff; never automatically
discard a potion during the normal strategy pass. `strategy_required` asks
Orchestrator to choose and continue, not to request player permission. If that
bounded replacement review reaches the routine watchdog, the reviewed
`--fallback` path may use an explicitly visible Skip Potion shortcut once and
verify the map transition. Avoid a separate strategy round for gold, card-menu
opening, focus or confirmation.

The ordinary logical move target remains 20 seconds, including inspection and
verification; this is an engineering target, not measured console performance.
Normal adapter responses show compact current timing. Full timing remains on
disk and is available through `operation:summary` or `--verbose-timing`.
Do not repeatedly investigate old overruns between routine reward inputs.
