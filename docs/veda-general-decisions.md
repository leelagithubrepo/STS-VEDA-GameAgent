# Reviewed decisions on unfamiliar maps and events

VEDA can summarize a newly supplied map graph and verify several declared
outcomes of one choice. The operator still reads the screen and chooses the
strategy. An unfamiliar layout or readable random outcome is not, by itself,
a reason to stop. Missing evidence that affects the next action needs a
specific explanation and a fresh inspection or supported recovery.
Use the [learning policy](veda-action-evidence.md) for warnings, recovery and
actual-outcome logging. The complete-branch checks described below remain
available in strict mode; learning mode records unexpected observed commits
without requiring a matching prediction.

## Operator workflow

1. Inspect a fresh frame and reconcile the current run, resources and inventory.
   Use the current run's context, not a finished run's ledger.
2. For a map, record visible nodes and directed connections. Identify reachable
   next nodes, uncertain icons and where the view ends. Use the route brief
   below to check the graph facts. A low-confidence neighboring option does not
   invalidate a separate confirmed option.
3. Choose using HP, gold, potions, relics, deck, upcoming boss and the visible
   paths. A rest node is not proof that resting is available; a merchant is not
   proof of affordability. A `?` room is not a promise of safety.
4. For an event, read its text, enabled options and costs, then retrieve the
   relevant local rules. A known outcome range can be represented by complete
   alternatives in the [choice contract](veda-choice-execution.md). Do not
   invent an event identity, effect, reward or controller binding.
5. Submit one reviewed action through the adapter with fresh source evidence.
   Afterward, inspect the actual result. In strict mode exactly one declared
   outcome must match; in learning mode a verified unexpected commit is logged
   as a prediction mismatch. Record the actual inventory/lifecycle changes
   before planning the next action. An unresolved result retains its pending
   action; do not repeat the input.

Neow's opening Talk has its own [registration and Talk helper](veda-neow-start.md).
The general outcome support does not expand that helper's narrow control rule.

## Visible-graph brief

The helper reads a small JSON declaration and returns facts. It does not read
screenshots, query SQLite, verify freshness, choose a route or send input:

```sh
python3 scripts/veda_decisions.py brief --input /absolute/path/to/reviewed-map.json
```

A minimal declaration:

```json
{
  "current_node_id": "current",
  "visible_nodes": [
    {"node_id": "current", "kind": "enemy", "confidence": 0.99, "outgoing_complete": true},
    {"node_id": "rest-a", "kind": "rest", "confidence": 0.99},
    {"node_id": "unclear-b", "kind": "unknown", "confidence": 0.70}
  ],
  "visible_edges": [
    {"from_node_id": "current", "to_node_id": "rest-a", "confidence": 0.99},
    {"from_node_id": "current", "to_node_id": "unclear-b", "confidence": 0.99}
  ],
  "graph_complete": false,
  "resources": {"hp": 12, "max_hp": 80, "gold": 20, "potions": [], "relics": []}
}
```

Use `kind` for node classification; raw historical `node_kind` records need
explicit normalization and a fresh review. Supported kinds are `enemy`, `elite`,
`event`, `merchant`, `treasure`, `rest`, `boss`, or `unknown`/null. `shop` is an
alias for `merchant`. Elite and merchant nodes need nonempty
`classification_evidence` describing the inspected icon or tooltip. This is a
reviewer declaration, not automatic pixel recognition.

Node and edge confidence must be at least 0.90 for confirmed paths. Omitting
an edge confidence declares it confirmed; supply a lower value when uncertain.
Missing `outgoing_complete` inherits `graph_complete`, which defaults to false.
Only assert completeness when all exits are actually known. The helper rejects
cycles, duplicate IDs/edges, missing endpoints, and oversized graphs.

The output lists confirmed next options separately from `unverified_reachable`.
Per-option rest/merchant distance maps count directed edges from that next node;
the chosen rest itself has distance zero. Distances describe confirmed visible
paths, not unseen shortcuts. Reachability is `true` when a confirmed path exists,
`false` when a complete search rules it out, and `null` when missing evidence
prevents that conclusion.

The brief also exposes branching exits, visible elite/event nodes and uncertain
frontiers. Its two elite-free predicates differ: a path avoiding **known**
elites may cross an unclassified room; a confirmed elite-free path requires
classified nodes. Neither means damage-free, and a classified event can still
lead to combat. `elite_before_rest_unavoidable` concerns map node types only.
Resources are copied as context, without healing, affordability or risk scores.
The output grants no controller authority and cannot replace the adapter's
source-bound review or map recommendation checks.

## Offline evidence

```sh
python3 scripts/veda_decisions.py benchmark --output /absolute/path/to/new-report.json
```

The default fixture at `.veda/evals/dynamic-map-scenarios.json` was authored
separately from the summarizer and contains manually traced expected paths.
Seven graphs cover branching/merging, rest before elite, merchant access, cropped
frontiers, uncertain neighbors, question rooms and opaque node IDs. Each runs
against three resource profiles and original, reordered and renamed graphs.
Three cycle-rejection variants bring the total to **66 checks**. A wrong oracle
fails the benchmark; missing per-option expectations cannot silently pass.

The JSON report retains the fixture hash, each result and local calculation
time. Output files must be new; existing evidence is never overwritten.
Exit codes are 0 for success, 1 for failed assertions, and 2 for invalid input
or an unavailable output path. Omitting `--output` prints the result.

These checks establish graph-contract behavior on the supplied synthetic cases.
They do not measure screen recognition, route quality, model performance,
controller delivery, floor duration or win rate. Reviewed event alternatives
have separate verifier and temporary-SQLite/fake-controller regression tests.

## Remaining coverage

- Actual inventory additions/removals need an explicit observed digest and
  typed inventory-change record. Learning mode permits a result that differs
  from the predicted inventory, but never invents an item or its property.
- Input needs a supported control mapping: a visible hint, reviewed transition
  or applicable default PS5 profile. Missing future effects are a strategy
  warning; an absent mapping calls for observation/recovery, not fabricated
  controller evidence.
- Strategic quality still needs evaluation on held-out real game states,
  including survival, potions, deck/piles and long-fight setup. This benchmark
  contains no preferred-route oracle.
- Experience is retrieved with `veda_play_lessons.py` to inform later choices.
  It does not retrain the model or promote new rules automatically. A durable
  rule improvement still needs evidence and regression coverage.
