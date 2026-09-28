# Survey the map, prepare for the boss, then enter one room

Use this workflow at an act entrance and whenever the route branches or the
deck, potions, gold or health materially changes. Spire reviews the images and
strategy. The helpers check declarations and package input; they are not image
recognizers or proof that a route will win. Read this before taking an action
image. Use the existing warm bridge and reviewed adapter for each input.

## Read the actual map

Count every immediately selectable node, including the focused node. The white
outline and yellow corner markers indicate focus; they are not path edges or
proof that the room has been visited. On the September 27 starting-map evidence,
there are three enemy nodes and the middle is focused. Do not reuse that count
or those identities for a different map. Trace the dotted connections separately
from the order in which controller focus moves.

At the start, use a logical `current_node_id` for the already verified act
entrance (floor zero); it is a ledger anchor, not another visible room icon.
The selectable first-row nodes connect to that anchor because they are the
observed available starting choices. For later floors, use the actual traversed
node and its visible outgoing connections. Preserve stable node IDs across views.

Review the upper map and boss portrait when possible. Use overlapping map views
and trace shared nodes before joining them. Record only inspected icons and
connections. An unknown icon is not an event, and an event is not guaranteed
safe. A cropped map cannot establish that there are no further elites or rests.

## Browse without entering a node

Use the ordinary `veda_menu.py --draft` workflow with family `map_inspect`.
The selected default PS5 profile provides one Up or Down survey tap. This is a
directional map inspection, not an assumed analog-stick scroll. It may change
focus, change the viewport, or have no visible effect. Inspect the actual result
and never reinterpret a directional acknowledgement as a successful scan.

The compact UI for Up is:

```json
{
  "menu_family":"map_inspect", "choice_id":"act-map-survey",
  "focused_id":"inspect-up",
  "map_inspection":{"direction":"up","purpose":"survey","evidence_note":"Inspect the upper part of this visible map."},
  "options":[{"id":"inspect-up","label":"Inspect upper map","role":"map_inspection","enabled":true,"costs":{}}]
}
```

For Down, use `inspect-down`, `direction: "down"`, and `Inspect lower map`.
These are inspection contracts, not claims that these labels appear on screen.
The choice is `kind: "map"`, with that single option ID. Postconditions must
be map/result with unchanged context, exact resources, inventory and facts.
Use source-free draft validation, an exact inspected settled image,
`--session SESSION_DIRECTORY/state.json` for event-bound evidence,
binding and prepare/send sequence from [menu controls](veda-menu-controls.md).
Do not send an analog movement or raw bridge command as a substitute.

Afterwards the compact `veda.menu-result.v1` retains inspected resources,
inventory and facts as `unchanged`; its `result` is:

```json
{
  "kind":"map_view",
  "view":{
    "top_visible":false,"bottom_visible":true,
    "focused_node_id":"example-center-node",
    "visible_node_ids":["example-left-node","example-center-node","example-right-node"],
    "effect":"viewport_changed",
    "evidence_note":"Describe the actual changed viewport, changed focus or unchanged view."
  }
}
```

Use JSON `null` when focus is not legible, and record all actual visible node
IDs. `effect` is `viewport_changed`, `focus_changed` or `unchanged`. Supply the
exact pending action ID and actual `observed_result`, then use `--result
... --session ...` to bind a new inspected result capture. No `send` follows a
verification request. Identical image bytes are allowed only for a separately
captured, explicitly unchanged map inspection; they never verify room entry.

Two inspections in the same direction without viewport progress exhaust that
direction until the viewport or game state changes. The count survives adapter
restart. Do not restart or alter facts to reset it. Use inspected coverage and
a supported next node instead of stopping merely because the boss is offscreen.
At a map boundary, avoid another tap in the exhausted direction. The whole
survey also has a 64-input bound. Every node activation still needs its own
fresh review of current focus after browsing.

The profile is a declared default mapping, not a retained hardware transition.
Custom controls need their actual mapping. Live PS5 browsing remains to be
verified; offline tests cannot establish that Up/Down will scroll this console.

## Keep the survey and compare routes

`scripts/veda_map.py` merges reviewed map views and compares concrete paths:

```sh
python3 scripts/veda_map.py --survey /absolute/map-survey.json \
  --plan /absolute/map-plan-review.json --output /absolute/new-map-plan.json
```

The survey is `veda.map-survey.v1` with `run_id`, `act`, `current_node_id` and
`views`. Each view contains a unique `view_id`, its exact source
`{path,sha256,captured_at}`, a review `{complete:true,reviewer,evidence_note}`,
coverage `{top_visible,bottom_visible,complete_rows}`, observed nodes and edges.
Nodes contain `node_id,row,lane,kind,confidence,outgoing_complete,classification_evidence`.
Rows and lanes are stable logical positions, not changing screenshot pixels.
Edges contain `from_node_id,to_node_id,confidence,evidence_note`, with both
endpoints visible in that view. A complete row contains every node across that
row; a node with complete outgoing edges includes every outgoing connection.
Contradictory overlaps or a newly discovered node omitted from a previously
complete row are rejected. Correct the mistaken declaration from its image.

Do not hand-copy hashes or review objects. Prepare a source-free
`veda.map-survey-view-draft.v1` with `run_id`, `act`, `current_node_id` and `view`.
The view contains its ID, coverage, nodes, edges and optional expected boss;
omit source and review. Validate it before the action image:

```sh
python3 scripts/veda_map.py --bind-view /absolute/view-draft.json --validate
```

Capture and inspect the exact returned image, confirm the draft matches, then:

```sh
python3 scripts/veda_map.py --bind-view /absolute/view-draft.json \
  --capture /absolute/exact-inspected.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Actual inspected map and coverage limits' --reviewed \
  --output /absolute/new-bound-view.json
```

Append the returned envelope's `view` to `survey.views`. The helper derives
the original receipt and source identity. Upper cropped views may omit the
current node; the merged survey must contain it. Survey validation is archival
planning evidence; inspect the current viewport/focus after scrolling before binding an action.

A top-map view may contain `expected_boss: {name,confidence,evidence_note}`.
That is advance preparation evidence, never confirmation of the current combat
enemy. A20 Act 3's second boss remains unknown until actually revealed.

The plan review is `veda.map-plan-review.v1`, with the same run/act/current node,
actual `ascension`, named `reviewer`, `resources`, `readiness`, ordered `criteria`
and `boss_preparation`. Resources include HP/max HP/gold, an evidence note and
view IDs, plus `deck`, `relics`, and `potions`. Each inventory contains
`status`, counted `items: [{name,count}]`, `evidence_note`, and `view_ids`.
An inventory observed earlier can instead use empty view IDs plus
`ledger_reference: {run_id,baseline_id,event_ids}` naming the actual ledger
records. Do not claim that cards in a closed deck were inspected on the map.
Read current inventory context first; ledger references are retained, not
independently queried by this helper. Missing coverage remains unknown.

Spire supplies readiness with `health` (`critical`, `strained`, `comfortable`,
`unknown`), `elites` (`ready`, `limited`, `avoid`, `unknown`), `shop` (`useful`,
`optional`, `defer`, `unknown`) and a concrete `rationale`. Choose the order of
`avoid_forced_elite`, `elite_free_rest`, `earliest_rest`, `merchant_access`, and
`fewest_elites`. Conservative health/elite readiness prefixes rest-access
criteria. The helper ranks concrete observed continuations, preserves ties,
and reports unknown or truncated paths. It never combines a rest on one branch
with a shop on an incompatible branch or invents damage probabilities.

Consider healing versus useful upgrades, the deck's immediate damage/block
needs, available potions, affordability and escape branches. Avoid chaining
elites the deck cannot support; do not blindly minimize all elites regardless
of useful rewards. Full health alone is not proof of elite readiness. The
ranking is a topology aid: Spire still selects among ties and explains the
current deck's tradeoff.

For `boss_preparation`, give `expected_name` matching the reviewed portrait or
null, and `priorities: [{need,reason,reference}]`. Retrieve the actual boss and
act elite rules for the run's ascension from the local research catalog, or
research a missing fact before using it. Turn those rules into needs such as
front-loaded damage, sustained block, scaling, draw/exhaust or a useful potion.
Record the applicable source with each priority. Map expectation can guide
preparation; combat identity and turn intent still require actual combat
evidence. Research coverage is not the same as checked combat-manifest coverage.

Save the plan privately, and link it from the route recommendation. Record a
`route-snapshot` for the actual current choices and `route-recommendation` for
the selected node with safety rationale and reward tradeoff. Use `map-snapshot`
only for what that inspected view actually contains. Re-plan after new map
evidence, room arrival, rewards, potions, spending or material HP changes.

## Focus and enter one room

Use family `map_nodes`. `ui.map_siblings` contains
`{complete:true,selectable_count,from_node_id,evidence_note}`. Options are all
current selectable siblings in increasing visible X position; each option has
ordinary `id,label,enabled:true,costs:{}` and `node`:

```json
{"node_id":"SAME_AS_OPTION_ID","kind":"enemy","act":1,"floor":1,
 "x":919,"y":835,"reachable":true,
 "reachability_evidence":"Actual available starting node or traced connection from current node.",
 "classification_evidence":"Actual observed room icon."}
```

Replace the example coordinates and floor with the reviewed view. Before facts
must include `act`, `floor` and `current_node_id`. Selectable siblings all lead
to the next floor in the same act. Unknown nonselected node types may remain
unknown. They still occupy their actual focus positions. A known node is
selected using one adjacent Left/Right tap at a time, then focused Cross;
there is no wrapping or card-grid geometry. Verify each focus result with the
ordinary compact `result: {kind:"focus",focused_id:...}`.

The choice uses `kind:"map"` and one selected ID. Each complete arrival
postcondition declares `phase:"result"`, the actual possible room screen,
`context:"next_room"`, resource constraints, unchanged inventory, and facts
`act,floor,current_node_id,node_type` matching the selected node. Named allowed
changes can cover later-revealed encounter facts; do not invent an enemy or
hand before entry. Review entry relic effects before declaring resources
unchanged. Event nodes may use complete outcome alternatives. The helper derives
provisional floor/combat/turn identities; do not create database rows before input.

Use these exact room-screen names in arrival postconditions:

| Selected node kind | Possible arrival screen |
| --- | --- |
| `enemy`, `elite`, `boss` | `combat` |
| `rest` | `rest` |
| `merchant` | `shop` |
| `treasure` | `treasure` or `reward` |
| `event` | `event`, `combat`, `shop`, `treasure` or `reward` |

Keep `max_hp` as its exact unchanged number. Other resources may use explicit
reviewed bounds when an entry effect requires them; do not use an unbounded
guess. Declare only outcomes supported by the selected room and known effects.

For an inspected room arrival, use `result: {kind:"room_entry",node_id,screen}`.
Supply actual resources and facts at the top of the normal result draft. Combat
arrival also needs `encounter: {name,type,opening_state}`. The opening state has
`screen:"combat"`, `turn:1`, the same resources and observed combat facts. Omit
database IDs and `observed_at`; the original capture supplies source time. If
the entire opening hand is read and the deck is complete, add
`opening_hand:["ACTUAL_CARD_NAME",...]`; it must agree with the hand in
`opening_state`. Otherwise omit it and inspect zones before dependent combat advice.

Here is a complete **schema example**, not a prediction of the next encounter.
Replace all gameplay values and IDs with the actual inspected result. Both
`encounter` and optional `opening_hand` are nested inside `result`:

```json
{
  "schema":"veda.menu-result.v1",
  "action_id":"EXACT_PENDING_ACTION_ID",
  "resources":{"hp":80,"max_hp":80,"gold":99,"deck_size":10},
  "inventory":"unchanged",
  "facts":{"act":1,"floor":1,"current_node_id":"ACTUAL_SELECTED_NODE",
           "node_type":"enemy","character":"Ironclad","ascension":2},
  "result":{
    "kind":"room_entry","node_id":"ACTUAL_SELECTED_NODE","screen":"combat",
    "encounter":{
      "name":"ACTUAL_ENCOUNTER_NAME","type":"enemy",
      "opening_state":{
        "screen":"combat","turn":1,"hp":80,"max_hp":80,"gold":99,"deck_size":10,
        "hand":["ACTUAL_CARD_NAME"]
      }
    },
    "opening_hand":["ACTUAL_CARD_NAME"]
  },
  "observed_result":"Describe the observed room, resources and opening hand."
}
```

List every observed opening card, preserving duplicates. `opening_state.hand`
may contain names or objects with a `name`; the optional zone baseline uses
names and must match their multiplicities. Retain all unrelated before facts
unless the selected arrival contract explicitly allows their change. For a
rest/shop/treasure result, omit `encounter` and `opening_hand` entirely. Validate
the completed result draft before its final capture; then use the normal
`veda_menu.py --result ... --session ... --capture ... --reviewed` binding path.

The helper builds `advance_floor`, and for combat `start_combat` and `start_turn`,
their required evidence notes and optional opening-zone baseline. Room identity,
resources and hand must agree in all declarations. Submit the verification
pointer only. Use the returned canonical `next_context` for subsequent combat
or menu actions, not provisional IDs. An unexpected room remains pending for
inspection; never repeat Cross. Resume from the actual observed encounter.

## Inventory discovery

Use `inventory-discover` for a single newly inspected category:

```sh
python3 scripts/veda_memory.py inventory-discover --run-id CURRENT_RUN \
  --floor-id CURRENT_FLOOR --items @/absolute/deck-items.json \
  --categories '{"card":"complete"}' --reviewer 'Codex Orchestrator' \
  --reviewed --source 'Actual complete deck inspection' \
  --screenshot /absolute/inspected-picker.png
```

Uninspected relics/potions retain their prior contents, coverage and provenance.
Only an explicitly inspected complete empty category means empty. A discovery
is knowledge enrichment, not an acquisition. Finish it after a pending action
has resolved, then refresh inventory context before preparing the next action.
