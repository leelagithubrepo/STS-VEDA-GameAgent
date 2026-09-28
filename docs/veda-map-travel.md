# Quick map travel

On an ordinary map arrival, reuse the saved route, inspect current selectable
nodes and focus, navigate, and verify room entry. Do not scan up and down or
repeat boss research for each focus tap. Use the existing warm bridge and
reviewed adapter; this helper never sends input.

The target remains **20 seconds for an ordinary logical menu move**, including
focus and verification. This is an engineering target, not measured PS5
performance or a reason to skip observing outcomes. New route decisions can
take longer.

## Only one reachable next node

When `map_siblings` confirms the complete reachable set and it contains exactly
one enabled, identified node, call `veda_map_step.py --snapshot MAP_SNAPSHOT.json`
with the normal binding flags and `--execute`. Omit `--decision`; no route file
or boss survey is needed. The helper returns `forced_move:true`, records
"Only available path", and skips archived-map replay and strategic comparison.
Verify the actual room entry normally. One visible node in a cropped map is
not proof that it is the only reachable node; inspect its current connections.

Keep any saved route/cache. The forced step preserves its strategic baseline,
so health/inventory changes are reconsidered at the next fork, not while there
is no choice. Optional new archived views are deferred on this shortcut; keep
their files for when planning could matter. Decisions inside the next room
still receive the usual strategy.

## Save one choice, then navigate

Keep a private cache under the session, named by run and act. Write a new cache
version when saving a decision, adding views or advancing to a new map node.
It is planning memory: it never arms the controller or proves arrival.

Make one `veda.map-travel-snapshot.v1` per map arrival. It uses the same
`context`, `inventory`, `resources`, `facts` and `ui` as the compact map-node
draft in [map controls](veda-map-play.md#focus-and-enter-one-room), without
`choice` or `reasoning`. Include all current selectable siblings, connections
and actual focus. Preserve stable node IDs and use the actual HUD floor.
Never reuse old current choices just because the future map is cached.

Choose once with a small decision file:

```json
{"node_ids":["ACTUAL_CURRENT_NODE","CHOSEN_NEXT_NODE"],"reason":"The concrete route tradeoff for the current deck and resources."}
```

The route can extend along inspected edges, for example current → merchant →
rest. Future edges require saved bound views. A single reachable next-node
choice works without a full survey or boss identity, including mid-act resumes.
Unknown future coverage does not mean the route is safe; weigh the uncertainty.

Prepare one checked step and save the choice:

```sh
python3 scripts/veda_map_step.py --snapshot MAP_SNAPSHOT.json \
  --decision ROUTE_DECISION.json --cache-output NEW_ACT_CACHE.json \
  --session SESSION_DIRECTORY/state.json --capture EXACT_INSPECTED.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Actual selectable nodes, focus and HUD inspected.' \
  --reviewed --execute --output NEW_REQUEST.json
```

Include `--cache EXISTING_ACT_CACHE.json` when extending/reassessing a route.
Add `--view NEW_BOUND_VIEW.json` only for newly inspected topology; repeat it
for several views. Existing views stay cached. Omit `--decision` when reusing
the route. Omit all binding flags to preview a plan without an action packet.
Submit the returned `request_file` object unchanged to the armed adapter. It
executes **one** adjacent focus tap or focused activation; no extra `send`.

## Verify focus without rewriting the inventory

Inspect the after-image. If only focus changed:

```sh
python3 scripts/veda_map_step.py --focus-result ACTUAL_FOCUSED_NODE \
  --action-id EXACT_PENDING_ACTION --unchanged \
  --observed-result 'Actual focus inspected; other map facts and resources unchanged.' \
  --session SESSION_DIRECTORY/state.json --capture EXACT_AFTER.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Actual observed focus transition.' \
  --reviewed --output NEW_FOCUS_VERIFICATION.json
```

Submit this result pointer first and require verification. `--unchanged` means
the advisor actually inspected unchanged options, inventory, resources and
other facts; a button acknowledgement cannot establish it.

Reuse the original snapshot and destination, supplying only actual focus:

```sh
python3 scripts/veda_map_step.py --snapshot MAP_SNAPSHOT.json \
  --cache ACT_CACHE.json --focus ACTUAL_FOCUSED_NODE --unchanged \
  --session SESSION_DIRECTORY/state.json --capture EXACT_AFTER.png \
  --reviewer 'Codex Orchestrator' --evidence-note 'Same inspected frame; only focus changed from snapshot.' \
  --reviewed --execute --output NEXT_REQUEST.json
```

The inspected after-image can also be the next before-image with no intervening
input or outside change. Do not recapture because thinking exceeded 30 seconds.
Session input epochs prevent reuse across later inputs. If anything else changed,
update the snapshot instead of declaring it unchanged.

After activation, use the ordinary compact `room_entry` result in
[map controls](veda-map-play.md#focus-and-enter-one-room). Record actual screen,
node, resources and facts; combat also needs the actual encounter/opening state.
Use the adapter's returned canonical context. The next actual map snapshot
advances the saved route; the cache never infers arrival from a sent button.

Entry forecasts default to unchanged resources. Review known entry relic
effects: optional snapshot `entry_resources` maps a destination ID to its
complete predicted resource constraints. These are predictions, not outcomes.
Learning mode records the actual observed result even if the forecast differs.

## Reassess when it matters

`strategy_required` asks **Luna to choose once and continue**, not the player for
permission. It does not stop or disarm the session. Reassess when the route ends,
the path/destination changes, new map knowledge appears, inventory or maximum HP
changes, or HP/gold changes materially. Saved topology remains available.

Default material-change thresholds are 15% of maximum HP at planning time
(rounded up) and 50 gold from the last strategic decision. They are tunable
review heuristics, not game rules. Small cumulative changes eventually trigger
review; focus movement and another image of the same graph do not. For a narrow
health margin or shopping budget, use explicit crossing thresholds:

```json
{"node_ids":["CURRENT","CHOSEN"],"reason":"Actual reviewed tradeoff.","reassess":{"hp_change":5,"gold_change":50,"hp_thresholds":[30],"gold_thresholds":[150]}}
```

A new run/act needs its own cache. Correct contradictory views from their images;
retain old evidence rather than repeatedly scrolling. The advisor can still
choose from currently inspected reachable nodes while correcting archival data.
For that fallback, keep the conflicting archive unchanged, omit `--cache` and
`--view`, choose a newly reviewed one-step route and write a distinct cache file.
Do not submit the conflicting cache again or erase it to make validation pass.

Inspect more map only when it could change the route or useful boss preparation.
Bind and save those views with the existing survey helper. Original timestamps
remain valid for static topology, never live action authority. A cropped upper
view stays partial; claim the top only after inspecting its boss node or portrait.
