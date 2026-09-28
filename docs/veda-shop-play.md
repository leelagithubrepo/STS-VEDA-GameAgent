# Merchant: inspect once, choose, navigate, verify

Use `scripts/veda_shop.py`; do not write a Python request builder during play.
It supplies the ordinary checked action, original image binding, and the exact
canonical context from the session. `context: "session"` avoids copying UUIDs.
Never leave solely because an old guide lacks a merchant navigation example.

The four reviewed menu families are `shop_entry` (Merchant/Skip Merchant),
`shop_stock` (goods/removal/Leave), `shop_exit` (Proceed), and `shop_remove`
(the actual card removal picker or its confirmation). An actionable Proceed
prompt uses `phase: "choose"`; a logged intermediate result is not automatically
an actionable choice. Circle Leave may close the shop before a separate Triangle
Proceed. Observe both boundaries; never assume Circle reached the map.

Use a `veda.shop-snapshot.v1` with `context`, `inventory`, `resources`, `facts`
and `ui`, like the [compact menu snapshot](veda-menu-controls.md). Set
`context: "session"` and retain the actual merchant floor/node facts. The helper
refuses an ID different from the canonical session context. An unresolved action
must first be reconciled; no new merchant request replaces it.

`ui` has `menu_family`, `choice_id`, actual `focused_id` and all current `options`.
Stock options have unique IDs (including duplicate card copies), visible labels,
`enabled`, exact `costs: {"gold": PRICE}`, `role: "card"|"relic"|"potion"`,
and `offer: {"name": "ACTUAL NAME"}`. Use role `remove_service` for the service,
then `remove_card` for an actual removable card in its picker. Inspect whether
the fee has already been charged; use the remaining actual charge in picker
costs. Do not charge it twice. If a confirmation appears, declare actual
`phase:"confirm"`, `selected_ids`, `pending_ids` and `confirm_hint` before that
final input. Inventory removal is recorded only from the actual result.
Opening confirmation changes the UI decision key. Inspect the same selected
card and remaining fee, then use `--after-result VERIFIED_PREVIEW.json --choose
SAME_CARD_ID --reason 'Confirm the previously chosen removal after inspecting
its preview.'` with the normal binding flags. This binds the confirmation;
it does not require choosing a different card or repeating removal strategy.

Boundary options are free and use their actual visible global button hint:

```json
{"id":"proceed","label":"Proceed","enabled":true,"costs":{},"role":"proceed","shortcut_hint":{"button":"triangle","hint_text":"△ Proceed"}}
```

Other boundary roles are `open`, `skip`, and `leave`. The helper opens the
merchant or follows a visible Proceed routinely. Purchases, card removal,
skipping the merchant and leaving stock require one strategic decision.

For navigable goods/cards, add `shop_positions: [{"id":"ITEM","x":500,"y":300}, ...]`
with observed item-center positions, excluding global shortcuts and sold items.
The profile predicts one directional focus movement from visible geometry;
it does not claim a verified console mapping. Inspect the actual focus before
Cross. Unaffordable items can occupy focus positions but cannot be purchased.

## One purchase decision

Inspect stock/prices once and weigh useful purchases, removal and saving gold
against the deck, potions and planned route. Do not research every item on
every focus tap. Choose an actual option and save its decision:

```sh
python3 scripts/veda_shop.py --snapshot SHOP_SNAPSHOT.json \
  --session SESSION_DIRECTORY/state.json --choose ACTUAL_OPTION --reason 'Concrete deck/budget tradeoff.' \
  --decision-output NEW_SHOP_DECISION.json --capture EXACT_INSPECTED.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Stock, prices, resources and focus inspected.' \
  --reviewed --execute --output NEW_REQUEST.json
```

For routine entry/Proceed, omit choose/reason/decision-output. For a source-free
preview, omit capture/reviewer/evidence-note/reviewed/execute/output. Submit the
returned `request_file` object unchanged; `--execute` produces one input only.
Do not send another `send`.

## Observe the actual result and reuse it

Use the ordinary `veda_menu.py --result` with the exact pending action ID.
For focus, declare `result:{"kind":"focus","focused_id":"ACTUAL ITEM"}` and
explicitly reviewed unchanged resources/inventory/facts. Bind the inspected
after-image and submit verification before the next action. In learning mode,
an unexpected focus or a separately captured unchanged focus becomes a recorded
transition; it never counts as buying an item. The helper retains this observed
directional mapping, and removes a direction that produced no movement from
that item. Inspect a changed layout or another route rather than repeating it.

Continue with the actual verified result, keeping the purchase decision:

```sh
python3 scripts/veda_shop.py --after-result VERIFIED_RESULT_REQUEST.json \
  --session SESSION_DIRECTORY/state.json --decision SHOP_DECISION.json \
  --capture EXACT_AFTER.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Same settled inspected state following verified focus.' \
  --reviewed --execute --output NEXT_REQUEST.json
```

`--after-result` requires the exact last verified result; it copies actual state
and learned focus without retyping the deck, gold or IDs. The after-image can be
the next before-image if there has been no further input/outside change. Thinking
time alone never requires recapture. A raw legacy result lacking a merchant
family needs one inspected snapshot describing the current actionable menu.
A legacy result without a retained complete-result fingerprint also needs that
snapshot. Modified result content cannot be reused merely by keeping its image
hash and action ID.

After buying, inspect actual gold, inventory and remaining stock/focus. Supply
those in the compact result; inventory events are derived from actual changes.
Cards/relics with further choice screens need those actual screens inspected.
Predicted immediate effects never establish a purchase on their own. A changed
price, resource total or stock invalidates the old purchase decision: assess the
remaining shopping plan once and continue. A full/unknown potion belt asks the
advisor to choose another purchase or handle the belt explicitly, never to
discard automatically. `strategy_required` is advisor work, not a stop or a
request for player permission.

Keep the 20-second ordinary logical-move and 90-second noncombat-floor targets
from [timing guidance](veda-play-hot-path.md). They are engineering targets;
live merchant timing and controller layout remain to be measured.
