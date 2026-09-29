# Accepted combat recovery correction

Scope continues the owner's approved automation repairs. Diagnose the stopped
floor-5 turn-2 input and fix the incomplete combat navigation recovery path.
The archived before image shows yellow markers around Ironclad, while the
submitted request incorrectly says Disarm is focused. Right moves focus to
Jaw Worm. Gameplay resources and hand stay unchanged. The result helper first
rejects a 385-byte explanation against an opaque 256-byte limit, then rejects
actual enemy focus because the pending prediction is card_focus.

Acceptance: learning mode must log an explicit unchanged-gameplay focus result
for a pending card-focus navigation, even outside the hand. Preserve the old
declaration and record the mismatch, without calling it card play or inventing
controller mappings. Test continued recovery through actual card play with fake
inputs. Give a clear result-note validation error and sufficient bounded room.
Update the focus-reading and same-helper result instructions; independent MIRA
review is required. Test the actual pending record on a disposable SQLite and
session copy before stopped-checkout integration and offline reconciliation.

No live capture, arming, controller connection or gameplay input is authorized.
Keep root development separate from Luna gameplay. Base fbb866b4c584d0357df3cb8f318413ce1241e84f.

## Implemented and verified

- Learning-mode card-focus navigation now records actual explicit focus while
  requiring identical gameplay/context and no selected card. Mismatches are
  logged; navigation never becomes a completed card. Strict mode stays strict.
- Compact combat results allow a bounded 2048-byte explanation and name that
  field/limit in validation errors. The existing result helper supplies UUIDs
  and post-input evidence binding; no manual reconciliation envelope is needed.
- Focus instructions distinguish character markers, enemy inspection and card
  targeting. Manual archived-frame annotations preserve actual source hashes
  and do not claim automatic pixel recognition.
- Fake-controller regression continues unexpected enemy focus through recovery,
  selection and a separately observed completed card. Negative cases preserve
  context/resources, no-selection and strict-policy constraints.
- Exact current pending recovery succeeded on disposable SQLite/session copies
  with a controller factory that throws if constructed. Completed count 77→78,
  pending cleared, actual enemy focus recorded; no card played or return to hand.

Full regression: 1,607 tests in 14.572 seconds, 1,606 passed and one optional SDK
test skipped. The prior test requiring rejection of a valid unexpected hand
focus was updated to the intended learning behavior; all other tests passed.
Skill validation and diff checks passed. MIRA APPROVED after independent image
review and clarification that enemy targeting requires a selected card/target
prompt. Private report and replay artifacts: `artifacts/combat-focus-review/`.

Live play and speed have not been demonstrated by this repair. Original false
before-focus evidence remains in the audit with an explicit correction; it
must not teach a controller edge. Install while stopped, back up local state,
then use only offline result verification to reconcile the delivered input.
