# Find the current play handler once

Read this table at a new screen; then read only its linked guide. Do not scan
source files, tests or historical artifacts to rediscover a listed operation.
A handler being listed is not a claim that every possible variant was tested.

| Observed screen | Helper and guide | Completion covered |
| --- | --- | --- |
| Combat / combat focus | `veda_combat.py`, [combat](veda-combat-play.md) | Focus, selection, card effect, turn and supported boundaries |
| Combat rewards / card offers | `veda_loot.py`, [loot](veda-loot-play.md) | Gold, offers, selected card confirmation, acquisition, departure |
| Map | `veda_map_step.py`, [map](veda-map-travel.md) | Current edges, focus, corrected observations and room entry |
| Merchant | `veda_shop.py`, [shop](veda-shop-play.md) | Reviewed stock/removal, purchase and departure |
| Rest / Smith / campfire upgrade / campfire exit | `veda_campfire.py`, [campfire](veda-campfire-play.md) | Rest→actual HP→map; Smith→preview→upgrade→map |
| Neow Talk / event options / Neow leave / upgrade picker | Existing helpers in [menus](veda-menu-controls.md) | Named supported flows; not every event branch |
| Chest interaction, boss relic selection, special campfire actions | No dedicated compact flow currently | A map room-entry record alone does not prove room completion |

For an unlisted variant, inspect its actual options and consult the single
relevant menu guide once. Use an existing applicable contract when available;
otherwise record the exact missing capability and observed controls. Repeated
source searches cannot add missing support. Never rename a screen to fit an
unrelated family or claim a room completed from a navigation acknowledgement.
Gameplay uncertainty still calls for a decision; a missing execution handler
is a product gap, not missing game strategy. Escalate it clearly and preserve
pending actions. Do not leave controls armed if ending the task with a genuine
technical gap; release the owned adapter/bridge as described in the hot-path
guide, without replaying or erasing pending input.
