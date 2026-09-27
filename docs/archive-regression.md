# Saved-image regression audits

The archive audit checks whether the saved-frame reader processes reviewed
images consistently. A completed sweep does **not** establish that its readings
are correct, that it understands every card or enemy, or that it can control a
run. Independent visual labels and separate mechanics checks are still required.

The tool reads local files and uses the existing native OCR helper. It does not
capture the live screen, send controller input, update run inventory, or call a
paid model service. Private images, labels, and results belong under `artifacts/`
and remain outside Git.

## Prepare the evidence

1. Inventory every source by its actual file format, SHA-256 hash, and dimensions.
   Preserve duplicate and invalid files in the accounting. The reader currently
   accepts PNG bytes; a `.png` filename alone is insufficient.
2. Review each game viewport from the saved image. Equal desktop dimensions do
   not establish equal game-window bounds. Preserve the visual review and pixel
   boundary evidence. Exclude captures whose game viewport cannot be established.
   Retain in-game overlays such as selected cards and shop tooltips, marking
   obscured fields unknown; these are useful regression cases.
3. Group related scenes before looking at predictions. Reserve whole groups for
   evaluation. A date block without previous references is only a reserved
   archive group unless its independence from earlier runs is established.
4. Freeze independently inspected labels before running predictions. Keep
   unclear values unknown, and distinguish selected previews, ordinary cards,
   menus, tooltips, and pile pages. A visible subset does not prove completeness.

The label-free input document uses schema `veda.archive-regression-input.v1`.
It contains `images`, `excluded`, optional `created_at`, and optional `notes`.
This minimal document shows the required wrapper and fields for one image:

```json
{
  "schema": "veda.archive-regression-input.v1",
  "images": [
    {
      "image": "relative/to/manifest/source.png",
      "sha256": "<64 lowercase hexadecimal characters>",
      "dimensions": [2940, 1912],
      "viewport": [0, 168, 2140, 1372],
      "viewport_review": "<review artifact, hash, and source reference>",
      "group_id": "<related scene group>",
      "cohort": "development"
    }
  ],
  "excluded": []
}
```

Replace the placeholders with verified source metadata. The numbers above
illustrate the format; they are **not default bounds**.
Use `development`, `locked_evaluation`, or `exposure_unknown` for the cohort.
An excluded row needs `image` and `reason`, with an optional `sha256`.
Paths resolve relative to the manifest. Labels must never be added to this file
or supplied to the reader. The review reference records provenance; the tool
cannot itself certify that the visual review was adequate.

## Run and resume

```sh
python3 scripts/audit_observation_archive.py \
  --manifest artifacts/my-audit/inputs.json \
  --native-helper artifacts/native-ocr/native_ocr-threats \
  --output-dir artifacts/my-audit/results
```

Use the Python environment and native helper prepared for this project. If the
native helper cannot initialize in the execution sandbox, preserve that failed
attempt and run a new output directory with the required local permission.
Never count a successful outer result as valid OCR when its crop operations
failed.

`--max-new N` bounds a batch. `--resume` continues the same output directory
after verifying its manifest, implementation, sources, and saved results.
Unchanged failures remain recorded and are not silently retried. Changed
sources or implementations need a separate run so earlier results remain
reviewable. The runner also refuses an unindexed result left by an interrupted
write; inspect and preserve it before deciding how to recover.

Exit status `0` means every eligible unique source has a recorded outcome,
including failures. Status `2` means a bounded batch still has pending sources
or the command rejected invalid inputs. Consult the printed summary and error
message. Neither exit status measures recognition accuracy.

## Interpret the results

- Count every archived path, each unique image, duplicate aliases, exclusions,
  processing failures, and unresolved fields separately.
- Inspect crop failures and unsupported areas even when the outer read succeeds.
- Score only independently labeled fields. Match regions geometrically, without
  choosing matches by expected values. Retain unknowns and unmatched candidates;
  do not report a subset's accuracy as archive-wide accuracy.
- Compare inventory, statuses, piles, enemy behavior, and action sequences
  separately from title and number recognition. If a field has no recognizer or
  executable rule, report that gap directly.
- Retain original results before fixing a discovered issue. Images used to tune
  a fix become development evidence. Keep a separate reserved group for the next
  evaluation.

Synthetic behavior tests check the software's safeguards and supported effects.
They do not independently verify all game rules or prove that archived actions
had the claimed outcomes. A saved action sequence needs explicit before/after
source hashes, action identity, encounter context, and intervening-input notes
before it can support a causal replay claim.
