# Orchestrator operating loop

Use this checklist only after the current run has been explicitly armed and the bridge is ready.

## Before each decision

1. Take a new passive screenshot with `python3 scripts/capture_observation.py --game-window`. Open and inspect the resulting image. A capture path alone is not proof that the game screen was captured correctly. Finish request setup before the action frame so the 30-second source window is not spent rediscovering schemas.
2. Read the current confirmed run, floor, inventory, map, combat, and zone records from `scripts/veda_memory.py` as appropriate. Follow `.veda/workflows/spire-telemetry-v4.md`; link evidence IDs rather than duplicating or inventing evidence.
3. If in combat, provide Spire with the current screenshot and relevant confirmed ledger state. Request one next action only. Do not ask it to perform inputs.
   When state is uncertain, compare supported actions conservatively: avoid death
   where possible, then lower damage risk while accounting for potions and future
   setup. Do not invent probabilities or require exact hidden outcomes before
   choosing. Report the horizon used for any damage bound.
4. Validate legality against the current screen, energy, target, intent, status, and relevant zones using `docs/veda-action-evidence.md`. When Headbutt depends on an unknown discard pile, inspect it through a supported action or exclude Headbutt; another card remains eligible only if its full checked review passes. Check 0-cost options and whether Body Slam safely converts current Block. Corruption makes Skills free, not Powers. Review draw/discard and Barricade/setup before Corruption in long fights with Runic Pyramid when that affects the strategy. Record potion use when observed, and recheck retained hand/order/space after a draw or major change.
5. Pass only the accepted next action through the reviewed adapter's prepare → send → verify cycle. Use one checked input at a time; never send raw strategic taps to the bridge or batch across a draw, turn boundary, enemy action, or other material transition.

## After each action

1. Capture and inspect the resulting screen. Verify the action actually occurred and update the ledger with observed card movement, HP/Block/energy, enemy health/status/intent, inventory changes, and outcome as applicable.
2. Resolve the pending decision only from its observed outcome. A supported random result may differ from a preferred prediction but still match exactly one complete declared branch. An outcome outside the checked contract remains pending; inspect/reconcile it and never replay input. Do not begin a new plan while that action is unresolved.
3. Once the pending result is verified, review the evidence required for the next action. Preserve unrelated unknowns; do not require every hidden future outcome to become known.

## Non-combat choices

Ask Spire for one choice at a time for map route, reward, merchant, rest, smith, event, and relic/potion decisions. Use visible connections and costs, applicable local rules and the complete checked choice contract. An unfamiliar layout is not inherently blocked. Keep an unclear map sibling separate from a confirmed route, and keep cropped future paths unknown. A question room does not promise safety; supported randomness needs reviewed bounds or alternatives. Unknown inventory additions or missing menu bindings remain unsupported when the current adapter cannot check them. Inspect the specific gap or assess an eligible alternative instead of guessing.

## Safe termination

Stop immediately if the user asks, input may target the wrong game/UI, video or control is lost, the bridge faults, delivery is unresolved, or a consequential move cannot be logged. Uncertain current game state calls for risk-aware re-planning. For missing action evidence before input, inspect within the bounded policy, then reassess supported alternatives. If no action can pass the adapter, preserve its exact missing capability and close the session; do not claim perfect state knowledge was needed or bypass the check. Different valid tactical preferences do not themselves require a stop; conflicting observations need resolution only for dependent actions. Use the adapter's stop operation to close the owned bridge and verify its cleanup. Do not automatically reconnect or begin another run.

At a completed floor, finish the floor record and run the workflow's completeness check. At the end of the run, close the bridge, preserve evidence, and summarize what was confirmed, what remains uncertain, and where the telemetry was stored. Retrospective recommendations do not authorize implementation changes; route them to the Builder request workflow for owner approval.

For an evidence-related pause, preserve the compact blocker record described in `docs/veda-action-evidence.md`. A saved report supplements the pending-action journal; it never resolves or clears that journal.
