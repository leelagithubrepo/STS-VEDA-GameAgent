# Orchestrator operating loop

Use this checklist only after the current run has been explicitly armed and the bridge is ready.

## Before each decision

For menus, validate the source-free draft before taking the action frame; use
the exact compact commands in `docs/veda-menu-controls.md`. After viewing the
fresh frame, bind it in one helper call and immediately prepare/send through
the adapter. Read the capture path from the standalone capture command; never
combine capture and packet rewriting in a shell substitution pipeline. If the
capture fails, do not touch a draft or output packet. Diagnose the actual failing
stage before spending another capture attempt.

1. Read the current confirmed run, floor, inventory, map, combat, and zone records from `scripts/veda_memory.py` as appropriate. Follow `.veda/workflows/spire-telemetry-v4.md`; link evidence IDs rather than duplicating or inventing evidence. Finish schema discovery and request setup now. For menus, use a planning observation if needed, then finish strategy and draft validation before the fresh action image.
2. Take a new passive screenshot with `python3 scripts/capture_observation.py --game-window`. Open and inspect the resulting image. A capture path alone is not proof that the game screen was captured correctly. For menus, confirm the prepared draft still matches, then use the compact binding/prepare/send path above immediately; if the state changed, revise and validate before a new action image. Do not repeat ledger lookup or schema discovery inside that 30-second window.
3. If in combat, provide Spire with the current screenshot and relevant confirmed ledger state. Request one next action only. Do not ask it to perform inputs.
   When state is uncertain, compare supported actions conservatively: avoid death
   where possible, then lower damage risk while accounting for potions and future
   setup. Do not invent probabilities or require exact hidden outcomes before
   choosing. Report the horizon used for any damage bound.
4. Validate legality against the current screen, energy, target, intent, status, and relevant zones using `docs/veda-action-evidence.md`. When Headbutt depends on an unknown discard pile, inspect it through a supported action or exclude Headbutt; another card remains eligible only if its full checked review passes. Check 0-cost options and whether Body Slam safely converts current Block. Corruption makes Skills free, not Powers. Review draw/discard and Barricade/setup before Corruption in long fights with Runic Pyramid when that affects the strategy. Record potion use when observed, and recheck retained hand/order/space after a draw or major change.
5. Pass only the accepted next action through the reviewed adapter's prepare → send → verify cycle. Use one checked input at a time; never send raw strategic taps to the bridge or batch across a draw, turn boundary, enemy action, or other material transition.

## After each action

For menus, use `veda_menu.py --result ... --session ...` from
`docs/veda-menu-controls.md` to generate verification from a compact actual
result and the exact pending action. Never hand-build after-frame hashes,
old-frame references or mutation reviews. Use a short file pointer, not full
JSON in the adapter terminal. View each exact capture before declaring it
reviewed; an earlier unchanged-looking image does not review a new capture.
The helper generates source-bound fields and required upgrade-event notes.
Keep result schema preparation outside the capture window. Do not re-arm or
replay when `verified_pending_log` requires finalization/metadata recovery.

1. Capture and inspect the resulting screen. Verify the action actually occurred and update the ledger with observed card movement, HP/Block/energy, enemy health/status/intent, inventory changes, and outcome as applicable.
2. Resolve the pending decision only from its observed outcome. A supported random result may differ from a preferred prediction but still match exactly one complete declared branch. An outcome outside the checked contract remains pending; inspect/reconcile it and never replay input. Do not begin a new plan while that action is unresolved.
3. Once the pending result is verified, review the evidence required for the next action. Preserve unrelated unknowns; do not require every hidden future outcome to become known.

## Non-combat choices

At a map boundary, read `docs/veda-map-play.md` once. Review every selectable
sibling including the focused node, inspect overlapping views through the
reviewed map survey action, and build a source-linked map/route plan with
`veda_map.py`. Ask Spire to compare actual paths, elite readiness, rests,
useful upgrades and merchants using current inventory and HP. Identify the
expected boss from an inspected top-map portrait and retrieve applicable
ascension rules for preparation; keep it separate from combat identity.
Use compact `map_nodes` requests and `room_entry` results, then use returned
canonical context IDs. An exhausted inspection direction calls for choosing
among confirmed nodes, not restarting to repeat it. Preserve cropped future
paths and the A20 second Act 3 boss as unknown until observed.

When a picker reveals cards, use category-scoped `inventory-discover` after
the prior pending input resolves. Do not mark uninspected relics or potions
complete and empty while logging a deck. Check the current HUD against the
saved inventory before creating the next action digest.

For event choices and upgrade pickers, load `docs/veda-menu-controls.md` before
declaring a missing button hint a blocker. Apply its named profile only to the
reviewed supported menu family. Opening the picker, focusing a card, selecting
its preview and confirming its upgrade are separate checked inputs. Read the
cards and upgrade preview before deciding/committing; a highlight alone is not
an upgrade. Preserve the existing attempt and current pending-action journal.

Ask Spire for one choice at a time for map route, reward, merchant, rest, smith, event, and relic/potion decisions. Use visible connections and costs, applicable local rules and the complete checked choice contract. An unfamiliar layout is not inherently blocked. Keep an unclear map sibling separate from a confirmed route, and keep cropped future paths unknown. A question room does not promise safety; supported randomness needs reviewed bounds or alternatives. Unknown inventory additions or missing menu bindings remain unsupported when the current adapter cannot check them. Inspect the specific gap or assess an eligible alternative instead of guessing.

## Safe termination

Stop immediately if the user asks, input may target the wrong game/UI, video or control is lost, the bridge faults, delivery is unresolved, or a consequential move cannot be logged. Uncertain current game state calls for risk-aware re-planning. For missing action evidence before input, inspect within the bounded policy, then reassess supported alternatives. If no action can pass the adapter, preserve its exact missing capability and close the session; do not claim perfect state knowledge was needed or bypass the check. Different valid tactical preferences do not themselves require a stop; conflicting observations need resolution only for dependent actions. Use the adapter's stop operation to close the owned bridge and verify its cleanup. Do not automatically reconnect or begin another run.

At a completed floor, finish the floor record and run the workflow's completeness check. At the end of the run, close the bridge, preserve evidence, and summarize what was confirmed, what remains uncertain, and where the telemetry was stored. Retrospective recommendations do not authorize implementation changes; route them to the Builder request workflow for owner approval.

For an evidence-related pause, preserve the compact blocker record described in `docs/veda-action-evidence.md`. A saved report supplements the pending-action journal; it never resolves or clears that journal.
