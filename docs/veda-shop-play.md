# Merchant: inspect slots once, choose, select, confirm, leave

Use `scripts/veda_shop.py` for both actions and observed results. The helper
supplies session IDs, pending action IDs, control bindings and new packet paths.
Submit its returned `request_file` object unchanged. `--execute` packages one
input for the armed adapter; the helper itself sends nothing.

Use `--last-result` after the adapter verifies a result made by this helper.
It checks the complete sealed result in this session's `shop-packets` directory.
Do not retype the inventory, inspect source code or write a request builder.
`--after-result FILE` and explicit `--output FILE` remain available. A custom
output filename is reused with `--after-result FILE`; `--last-result` discovers
only the helper's generated result packets. A preview
omits the full draft by default; use `--verbose` only for debugging.

## Inspect actual stock, not tooltips

The advisor still inspects the image; this helper does not recognize pixels.
Only real priced slots belong in `items`. Wild Strike's Wound preview is a
related-card tooltip, not another item for sale or a focus position. Record the
actual arrow/raised shop card as focus. Use the exact card name and upgrade
state: a SALE label does not mean the card is upgraded. Unread names stay null.

For the opened shop, write a compact stock file with `focused_id`, `items`,
and `leave_hint`. Each item has `id`, `role` (`card`, `relic`, `potion` or
`remove_service`), `name`, its own visible `price`, and center coordinates `x`,
`y`. Optional `upgraded` must agree with the name. Optional `previews` are
annotations only and never become choices. Use a separate unique ID per actual
slot, including duplicate cards. The helper derives affordability, menu family,
phase, order and focus geometry. Unknown affordable offers remain comparison
uncertainties; do not rank an unidentified potion as low value.

The local catalog rejects status/curse previews as stock and provides brief
Exhaust reminders. It does not prove pixel recognition or rank purchases.
Compare affordable goods, removal and saving gold once against the deck and
route. Read exact relevant effects before describing them; Disarm exhausts, so
its lasting Strength reduction is not repeated play of the same card by default.

## Compact result commands

After each delivered input, inspect its after-image and package the actual
result. Every command below also needs `--session SESSION/state.json`,
`--capture EXACT_AFTER.png`, `--reviewer 'Codex Orchestrator'`,
`--evidence-note 'What this image shows.'`, and `--reviewed`.

| Observed result | Additional arguments |
| --- | --- |
| Shop opened | `--result opened --stock STOCK.json --unchanged --observed-result 'Stock and focus inspected; resources unchanged.'` |
| Directional focus | `--result focus --focused-id ACTUAL_SLOT --unchanged --observed-result 'Actual focus inspected; all other facts unchanged.'` |
| Purchase confirmation | `--result confirmation --focused-id CHOSEN_SLOT --hint-button cross --hint-text 'Cross Confirm' --unchanged --observed-result 'Chosen item selected; actual Confirm prompt visible.'` |
| Purchase completed | `--result purchased --actual PURCHASE.json --observed-result 'Gold, deck, acquired item and sold slot inspected.'` |
| Shop closed, Proceed visible | `--result left --hint-button triangle --hint-text 'Triangle Proceed' --unchanged --observed-result 'Stock closed; separate Proceed visible.'` |
| Returned to map | `--result map --unchanged --observed-result 'Map visible; resources and inventory unchanged.'` |

`--unchanged` declares that resources, inventory, facts and UI details not
explicitly supplied as changed were inspected as unchanged. Focus results
preserve stock; opened/left/map results explicitly describe a different UI.
It is not an inference from controller acknowledgement. Use the
actual displayed hint, not a remembered button. Never resend an unresolved
input. Focus-only outcomes cannot record a purchase.

For a completed ordinary purchase, `PURCHASE.json` supplies actual `gold`,
`deck_size`, `acquired: {"kind":"card","name":"ACTUAL NAME"}`, `sold_id`,
`focused_id`, and `others_unchanged:true`. The last declaration means all other
stock prices/slots, resources, inventory and facts were inspected as unchanged.
The helper updates that one acquired item and removes that sold slot only
after this explicit observation. If stock restocks, a relic has extra effects,
a different item appears, or another choice opens, use a full actual
`veda_menu.py --result` instead of asserting the ordinary-purchase delta.

## One saved buying decision through confirmation

Routine entry uses an inspected `veda.shop-snapshot.v1` with `context:"session"`,
`inventory`, `resources`, `facts`, and `ui.menu_family:"shop_entry"`. Use actual
free Merchant/Skip hints; `veda_shop.py --snapshot ENTRY.json` chooses Merchant.
For resumed legacy results without an actionable family or result seal, make
one current snapshot using `shop_stock`, `shop_remove` or `shop_exit` as observed.
Do not turn an old `phase:"result"` declaration into a new action by guessing.

After the opened result is verified, choose once:

```sh
python3 scripts/veda_shop.py --last-result --session SESSION/state.json \
  --choose ACTUAL_SLOT --reason 'Concrete deck, budget and route tradeoff.' \
  --decision-output NEW_DECISION.json --capture EXACT_INSPECTED.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Stock and actual focus inspected.' \
  --reviewed --execute
```

After each focus and confirmation result is verified, repeat the same compact
planning call with the saved decision:

```sh
python3 scripts/veda_shop.py --last-result --session SESSION/state.json \
  --decision NEW_DECISION.json --capture EXACT_AFTER.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Same settled inspected result.' \
  --reviewed --execute
```

It navigates to the item, selects it, then uses its observed confirmation hint.
Selection changes no gold or inventory. Confirmation must identify that same
item and retain actual prices/resources. This does not repeat buying strategy.
A focus mismatch learns the observed transition from actual slot identities;
a separately captured no-op does not imply purchase or justify blind retry.

After a verified purchase, assess remaining purchases/saving gold once. To
leave, use `--last-result --choose leave --reason 'Concrete reason to finish
shopping.'` and the normal binding flags. Record `--result left`, then call
`--last-result` with binding flags and `--execute` to prepare Proceed without
another strategic decision. Record `--result map` before map travel.

Card removal uses `shop_remove` with the actual selected deck copy and remaining
fee. Inspect whether payment already occurred to avoid charging twice. Supply
actual selected/pending IDs and confirmation hint if a removal preview appears;
rebind that same removal choice at confirmation. Record the actual removal
through the full compact menu result. Potion purchases never discard a slot
implicitly. `strategy_required` means the advisor chooses and continues.

The engineering targets remain 20 seconds per ordinary logical move and
90 seconds per noncombat floor. Offline replay verifies packaging and state
transitions; live end-to-end timing must still be measured after activation.
