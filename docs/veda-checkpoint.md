# Save a reviewed pause

The advisor can save one pause request without rebuilding the run history. The command records the observed numeric state, an optional inventory snapshot, source evidence and resume notes together. It neither captures the screen nor controls the game.

The advisor prepares the request from a recent saved image and the exact run/floor IDs already in its session. A person does not need to reconstruct the ledger at pause time. The command checks the source bytes and the request's consistency; it does not independently recognize the image or certify the supplied observations.

Use an archived capture or the advisory session's retained source PNG for the
pause image. This command records its path and hash but does not copy it. Keep
that file with the private run evidence; a temporary capture can disappear after
cleanup even though the historical database receipt remains.

From the repository root:

```sh
python3 scripts/veda_checkpoint.py \
  --database artifacts/veda-memory.sqlite3 \
  --request artifacts/pause-request.json
```

This writes to the named existing private database. There is no default database, capture step, controller input, installation or automatic history repair. Exit 0 means a receipt was saved or an identical committed request was found. Exit 2 returns `saved:false` and an error; no checkpoint or inventory from that failed operation is committed.

## Request contract

The complete shape below is a template, not a runnable observation. Replace the IDs, absolute image path, SHA-256 and timestamps with the current session's actual evidence. Never set capture time to the current time merely to make an old image pass. Use a new UUID for each new pause; reuse exactly the same request when retrying an uncertain result.

```json
{
  "schema": "veda.advisory-checkpoint.v1",
  "operation_id": "<new UUID>",
  "run_id": "<existing active run ID>",
  "floor_id": "<latest recorded floor ID>",
  "boundary": "map",
  "paused_at": "<actual pause time with timezone>",
  "source": {
    "path": "<absolute saved-image path>",
    "sha256": "<64 lowercase hexadecimal characters>",
    "captured_at": "<actual capture time with timezone>",
    "origin": "reviewer",
    "evidence_note": "Observed directly in the saved game image."
  },
  "state": {"hp": 35, "max_hp": 80, "floor": 32, "act": 2, "ascension": 2, "gold": 50, "deck_size": 20},
  "inventory": {
    "items": [{"kind": "potion", "item": "Explosive Potion"}],
    "coverage": {"card": "unknown", "relic": "unknown", "potion": "complete"},
    "evidence": {"potion": "All five slots inspected: named potion in slot 1; four empty slots."}
  },
  "expectations": [{"claim": "Collector is the next boss", "basis": "Earlier route observation; verify before acting."}],
  "resume_notes": ["Inspect the current map and potion tooltip before the next decision."]
}
```

Only copy the illustrated values when the actual evidence supports them. `origin` is `reviewer` or `reader`; the named origin remains visible in the receipt's stored provenance. It is not proof of automatic recognition. A visible reward is not yet an inventory item.

`state` accepts observed `hp`, `max_hp`, `gold`, `deck_size`, `energy`, `energy_max`, `block`, `floor`, `act` and `ascension`. Missing and `null` values remain unknown; neither means zero. This compact checkpoint is not a complete combat snapshot. Hand, enemy, status and pile details require the separate inspected-state workflow; bounded resume notes can describe what must be inspected next.

Inventory is optional. Each category is `complete`, `partial` or `unknown`; an evidence note is required for every observed category. An unknown category cannot list items. Duplicate cards and potions are retained, while duplicate relics are rejected. A complete empty list asserts that the category was inspected and empty. A partial list supplements the existing ledger and does not erase items that were not visible. The receipt reports the coverage supplied for this observation, not a newly certified complete historical inventory.

Expectations are stored separately from observed state. For example, an expected heal must not replace the last observed HP while the game screen is hidden. If no sufficiently recent image exists, obtain one or use the existing historical/user-reported evidence workflow; this confirmed-pause command deliberately refuses stale evidence.

For an in-combat pause, set `boundary` to `combat` and supply `combat_id` and `turn_id` for the latest open combat/turn. It saves the pause without ending the turn, combat or floor. A still-open recorded combat blocks a noncombat pause until that discrepancy is explicitly reconciled.

## Guarantees and limits

- The run must be active and the floor must be its latest recorded floor. Observed floor, Act and Ascension must agree with that context when supplied. Context records must already exist; the command does not create or guess them.
- Floor `recorded_at` is a ledger-write timestamp and can lag the game transition. The command checks the latest recorded floor ID, not a reconstructed floor-transition time. The reviewer or reader must bind the supplied observation to that floor; this command cannot prove that relationship from pixels. Combat sources must also follow the recorded opening of their supplied turn.
- Capture must precede pause and must be no more than 180 seconds old at a new write. Future timestamps are rejected. Timestamped capture filenames must agree with the supplied capture time. Other capture times are caller-supplied facts; file modification time is never substituted. A hash binds bytes, not the truth of a live-feed claim.
- Source bytes are checked before and after the write work. A changed source, validation error, closed/wrong context or failed write rolls back the checkpoint and inventory baseline together. This uses the existing ledger schema and transaction owner.
- The inventory baseline retains actual capture time. The checkpoint retains actual pause time and a separate write timestamp. Newer recorded inventory prevents an older image from replacing it.
- Repeating an identical committed operation returns its original receipt, even later when the source has expired or disappeared. `idempotent_replay:true` means historical success, not a fresh observation. Reusing the operation ID with different content fails. An unrelated second pause is rejected while the run is already paused.
- Every receipt sets `controller_authorized:false`, `runtime_authorized:false` and `requires_new_observation_on_resume:true`. Resume notes never authorize an action. Source PNG/JPEG reads are bounded to 32 MiB and request JSON to 128 KiB.

The receipt is a compact JSON object containing checkpoint and inventory-baseline IDs, context IDs, capture/pause times, source hash, observed state, supplied coverage, separate expectations and resume notes. It does not scan or return the full historical log. This command does not rewrite floor summaries or make `run-card` a fresh screen observation.

## Safe local verification

The runnable fixture suite creates only temporary databases and source fixtures:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_advisory_checkpoint
```

It covers atomic rollback after an inventory write, source changes during a write, concurrent identical retries, conflicting operation reuse, missing/wrong/closed context, stale/future images, unknown and partial inventory, and CLI errors. These tests validate persistence behavior, not visual recognition or game strategy.
