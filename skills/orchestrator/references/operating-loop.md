# Orchestrator operating loop

Use this checklist only after the current run has been explicitly armed and the bridge is ready.

## Before each decision

1. Take a new passive screenshot with `python3 scripts/capture_observation.py` (or the available read-only screen capture). Open and inspect the resulting image. A capture path alone is not proof that the game screen was captured correctly.
2. Read the current confirmed run, floor, inventory, map, combat, and zone records from `scripts/veda_memory.py` as appropriate. Follow `.veda/workflows/spire-telemetry-v4.md`; link evidence IDs rather than duplicating or inventing evidence.
3. If in combat, provide Spire with the current screenshot and relevant confirmed ledger state. Request one next action only. Do not ask it to perform inputs.
4. Validate legality against the current screen, energy, target, intent, status, and known card zones. When card-zone-dependent (especially Headbutt), inspect/read the confirmed discard state; if unknown, inspect the pile or abstain. Check 0-cost options and whether Body Slam safely converts existing Block to damage. Corruption makes Skills free, not Powers. Record potion use when it visibly occurs. For Runic Pyramid or after a draw, verify current hand contents/order/space again.
5. Translate only the accepted next action into the documented bridge JSONL tap(s). Use the fewest button taps possible; never batch across a draw, turn boundary, enemy action, or other material state transition.

## After each action

1. Capture and inspect the resulting screen. Verify the action actually occurred and update the ledger with observed card movement, HP/Block/energy, enemy health/status/intent, inventory changes, and outcome as applicable.
2. Resolve any pending decision only from observed outcome. If the result differs from the prediction, do not continue the old sequence: update facts, ask Spire again, or stop if state is unclear.
3. Repeat only after the next state is confirmed.

## Non-combat choices

Ask Spire for one choice at a time for map route, reward, merchant, rest, smith, event, and relic/potion decisions. Check visible legal options and resources before acting. Capture/record meaningful decisions and the result. Stop at unresolved screens or choices whose effect is not understood; never guess a map connection or item property.

## Safe termination

Stop immediately if the user asks, game input appears to target the wrong UI, the game is not in the expected state, a bridge call errors, screenshots stop updating, or Spire's recommendation depends on unavailable information. Send `{"action":"close"}` to the warm bridge when possible and allow process cleanup to release all inputs. Do not automatically restart or begin another run.

At a completed floor, finish the floor record and run the workflow's completeness check. At the end of the run, close the bridge, preserve evidence, and summarize what was confirmed, what remains uncertain, and where the telemetry was stored. Retrospective recommendations do not authorize implementation changes; route them to the Builder request workflow for owner approval.
