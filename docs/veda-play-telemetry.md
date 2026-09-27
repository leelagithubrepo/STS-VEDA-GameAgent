# Record a play attempt and its observed result

`PlayTelemetry` connects a reviewed play/session caller to the existing private SQLite ledger. It records a pending decision **before** an input, then records the observed result separately. It does not send input, recognize screenshots, check whether a move is tactically safe, or grant controller permission. The caller remains responsible for those checks.

The connector uses existing tables and transactional APIs. It requires an existing database and the exact active run, latest recorded floor, and open combat/turn when applicable. A paused run needs an explicit `record_resume` receipt. No current run is modified by the tests below.

## Caller sequence

1. Obtain a fresh saved image and a reviewed action with an independently valid execution authorization. Preserve the image: this connector checks its bytes but does not copy it.
2. Call `record_decision`. Save its decision ID. The action is now pending, even if the process stops before dispatch.
3. Cross the input boundary in the caller, once. A replayed telemetry receipt is never permission to send the action again.
4. Obtain another saved image and establish what actually happened. Call `record_outcome` with explicit facts. Do not derive a card movement, potion consumption, enemy death or turn transition merely from the intended action.
5. Use `next_context` from the outcome receipt. Re-observe and run the caller's normal state/legality checks before another input.

On restart, call `recover(run_id=...)`. Pending actions have `dispatch_status: "unknown"` and `must_not_repeat: true`. Inspect and reconcile them; do not resend them. A source/context conflict leaves the original decision pending. If an unrelated writer changed the ledger, this connector stops rather than merging competing histories automatically.

There is one narrow recovery exception: if the only intervening ledger change was a single explicit pause checkpoint in the same context, a fresh outcome can reconcile the pending action while the run stays paused. Its receipt identifies that exception. An explicit resume is still required. A pause that also changed inventory or any other ledger fact is not silently accepted by this exception.

## API and common request

```python
from pathlib import Path
from veda.telemetry_database import TelemetryDatabase
from veda.play_telemetry import PlayTelemetry

telemetry = PlayTelemetry(TelemetryDatabase(Path("/absolute/existing-run.sqlite3")))
pending_receipt = telemetry.record_decision(decision_request)
outcome_receipt = telemetry.record_outcome(outcome_request)
recovery = telemetry.recover(run_id=run_id)
```

The variable requests below are templates for caller-supplied evidence, not a script to run against a live game. Every request has:

```json
{
  "schema": "veda.play-telemetry.v1",
  "operation_id": "A NEW UUID FOR THIS LOGICAL WRITE",
  "context": {
    "run_id": "EXISTING RUN ID",
    "floor_id": "LATEST EXISTING FLOOR ID",
    "combat_id": null,
    "turn_id": null
  },
  "source": {
    "path": "/absolute/durable/saved.png",
    "sha256": "EXACT LOWERCASE SHA256 OF THOSE IMAGE BYTES",
    "captured_at": "ACTUAL TIMEZONE-AWARE CAPTURE TIMESTAMP",
    "origin": "reviewer",
    "evidence_note": "What this source actually establishes, including limitations."
  },
  "state": {}
}
```

Use `origin: "reader"` only for facts established by the reader; reviewed declarations must retain `"reviewer"`. The connector does not certify either as visually correct. `state` preserves explicit caller facts: omitted fields and nulls remain unknown. It is not automatically imported as a complete advisory snapshot. Any top-level `floor`, `act`, `ascension`, or `observed_at` must agree with the bound context/source.

Source timestamps must not be future-dated, older than 180 seconds at a new write, or earlier than known ledger changes. Capture filenames containing the standard `ps5_observation_...Z` timestamp must agree with `captured_at`. The connector checks the saved PNG/JPEG signature, bounded bytes and SHA before and after the transaction. It does not claim pixel recognition or image-file decoding. Files must remain durable if later audits need them.

Requests are bounded to 256 KiB, saved images to 32 MiB, mutation lists to 100 items and transitions to eight. Oversized histories fail closed at the connector's revision/chronology limits. A matching committed operation ID returns its original historical receipt even when the source later expires or disappears. A changed request with the same ID fails. All receipts keep `runtime_authorized` and `controller_authorized` false.

## Prepare a decision

Add `phase`, `action`, and `reasoning` to the common request; `prediction` is optional:

```json
{
  "phase": "combat",
  "action": {"kind": "play", "card_id": "OBSERVED CARD ID", "target": "OBSERVED ENEMY ID"},
  "reasoning": "Reviewed choice, with uncertainty already resolved by the caller.",
  "prediction": {}
}
```

Combat phase requires combat and turn IDs. Navigation/focus actions can use `action.kind: "navigation"` with an appropriate phase; they still receive a pending decision and an independently verified outcome. Only one unresolved decision is allowed per run, including a recommendation made through another ledger API. Preparation does not consume inventory, move cards, end a turn or complete a floor.

## Record the outcome

Use a new operation ID and the prepared context/decision ID. Add:

```json
{
  "decision_id": "ID FROM PREPARATION",
  "status": "verified",
  "evidence_note": "The actual result observed after this input.",
  "zone_coverage": "complete",
  "inventory_events": [],
  "zone_events": [],
  "transitions": []
}
```

`verified` means the caller established the performed action's outcome. It requires a later observation with different source bytes. `not_performed` means the caller established that the input was not sent; it marks the decision skipped and cannot carry game mutations. An unchanged image alone does not prove that an input was not sent. `unknown` retains the pending decision, cannot commit inventory/lifecycle mutations, and marks initialized card zones uncertain. A later, new verified observation can reconcile an unknown result. Retrying the unknown write itself does not resolve it.

All explicit inventory/zone changes, lifecycle transitions, source evidence and decision resolution commit in one transaction. A failure rolls all of them back. Matching retries and concurrent duplicate writes return one committed result, avoiding double consumption or duplicate resolution.

### Inventory

An event has `kind` (`card`, `relic`, `potion`), `action` (`acquired`, `removed`, `consumed`, `replaced`, `property_confirmed`), exact `item`, and `evidence_note`. Optional `property` supplies observed text; `related_item` is required for replacement. Removal/consumption/replacement must refer to a confirmed inventory item. For example:

```json
{"kind":"potion","action":"consumed","item":"Fire Potion","evidence_note":"The identified slot is empty and the result was independently observed."}
```

Alternatively, supply `inventory_baseline` with `items`, `coverage`, and `evidence`. `items` use `{ "kind": "potion", "item": "Fire Potion" }`; `coverage` names all three categories as `complete`, `partial`, or `unknown`. Each non-unknown category needs its own evidence note. A complete empty category explicitly establishes emptiness. A partial look does not erase previous known items. Do not send a baseline and events together.

The legacy inventory ledger preserves prior facts when a category is omitted or partially inspected; that is not proof of its current visual state. An unknown action's receipt sets `inventory_requires_inspection: true`, and its unresolved decision blocks another preparation. The caller must invalidate its live inventory context and inspect it before relying on it again. This connector never invents consumption to repair an uncertain history.

### Card zones

Each `zone_events` item has `kind`, optional `card_name`, `from_zone`, `to_zone`, positive `count`, and `evidence_note`. Supported kinds are the existing ledger's `draw`, `play`, `discard`, `exhaust`, `return`, `generate`, `shuffle`, and `unknown`. Zones are `hand`, `draw`, `discard`, and `exhaust`. Shuffle/unknown events cannot assert a card movement.

`zone_coverage: "complete"` explicitly declares that all movements caused by this action were observed. It does not certify the entire reconstructed pile history. Partial/omitted coverage appends an uncertainty marker when zones were initialized. The receipt's separate `zone_state_known` reports whether the existing reconstruction remains known; null means no initialized current-combat zones. A later complete event list does not erase an earlier uncertainty marker.

An optional `zone_baseline` initializes the first observed turn only, with `deck`, `hand`, `complete: true`, `opening: true`, and `evidence_note`, plus complete zone coverage. It can follow an explicit new-combat/start-turn transition in the same outcome. A partially visible or later hand is not an opening baseline. Event order is preserved in opaque IDs while retaining the actual shared capture timestamp.

### Lifecycle transitions

Each transition has `kind` and `evidence_note`, plus exactly these fields:

| Kind | Explicit fields |
| --- | --- |
| `end_turn` | `closing_state` |
| `start_turn` | `turn_number`, `phase`, `opening_state` |
| `end_combat` | `outcome`, `closing_state` |
| `start_combat` | `opening_state`, `encounter_name`, `encounter_type` |
| `advance_floor` | `act`, `floor`, `node_type`, `previous_outcome`, `previous_ending_state`, `starting_state` |

Transitions execute in the supplied order. End an open turn before opening the next one or closing combat. The next recorded turn must have the next explicit number. Close combat before advancing a floor. A later observed floor may skip unrecorded floors; the connector does not fabricate their events. Outcome `state` refers to the final observed context; the decision/outcome remain linked to the context where the input began. `next_context` supplies the new IDs.

To resume a paused run, call `record_resume` with the common fields plus `boundary` (`map`, `reward`, `combat`, `event`, `controller`, `other`) and `evidence_note`. The saved source must follow the previous pause. This records an explicit resume checkpoint, not execution authority. Pending input must be reconciled first.

## Verify locally

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_play_telemetry -v
```

Tests use temporary databases and synthetic source-byte fixtures. They exercise transactions, retries, concurrent duplicates, uncertainty, chronology, source changes, inventory, zones and lifecycle transitions. They provide no recognition-accuracy or live-controller validation.
