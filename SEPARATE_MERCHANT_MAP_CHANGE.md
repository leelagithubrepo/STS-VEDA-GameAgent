# Accepted change — 2026-09-28

The Product Owner authorized implementing merchant corrections and automatic
selection of the only confirmed reachable map node in a separate checkout,
while Terminal Orchestrator continues playing.

Base: f0c2a729c684fa50d9c5e344627f4b3f43dfb3b6.
Branch: codex/merchant-map-fast-path.

Scope: exclude tooltip/status previews from merchant choices; retain accurate
card identity and uncertainty in buying comparisons; represent purchase
selection and actual confirmation separately; compact observed result commands
preserve actionable UI and place artifacts automatically under the session;
skip route analysis for a complete single reachable map option. Test against
the recorded merchant sequence and disposable adapters/databases. Obtain MIRA
review of the revised player instructions.

Do not integrate, install personal skills, edit live telemetry/session files,
capture the current screen, connect to the bridge, or send controller input.
Use only copied historical images and isolated test state. Deliver tested
commits and an integration bundle for after the player stops.

## Implemented

- The complete, one-node reachable set bypasses route comparison and archived
  map replay. It packages the normal checked input and still verifies entry.
  The strategic baseline survives so HP/inventory changes matter at the next fork.
- Merchant stock slots and preview annotations are separate. Known status/curse
  previews cannot become stock/focus; inconsistent declared upgrade labels are
  rejected. Unknown affordable goods and Exhaust receive short factual reminders.
  These are semantic checks on inspected declarations, not automatic vision.
- Stock selection and confirmation are separate steps. The exact same buying
  decision is retained through focus, select and visible confirmation; selection
  cannot add inventory or spend gold.
- Compact opened/focus/confirmation/purchased/left/map result commands derive
  pending IDs, preserve actionable menus and generate session-local file paths.
  `--last-result` reuses only a matching sealed verified result. Explicit custom
  output paths remain usable through `--after-result`.
- The replay uses the archived shop's actual stock, 113/39 gold and 11/12 card
  counts. Independent screenshot review confirms the tooltip/focus distinction
  and the actual confirmation/purchase screens. Fake-controller tests verify the
  full Cross, Right, Right, Right, Cross, Cross, Circle, Triangle sequence.

## Validation

Full offline regression: 1,598 tests ran in 14.162 seconds; 1,597 passed and
one optional local bridge SDK test was skipped. The fresh clone initially
lacked a private historical HP fixture; the archived template images were
copied, source hashes checked, and the complete suite rerun successfully.
Skill validation and diff whitespace checks passed.

Private review packet, immutable archived images, synthetic previews and full
regression log are under `artifacts/merchant-map-review/` in this checkout.
Fixtures explicitly distinguish manually inspected annotations from automatic
pixel recognition. The 20-second move and 90-second noncombat-floor targets
remain unmeasured on live gameplay for this change.

## Activation deferred

Do not merge or update installed skills while Terminal Orchestrator is playing.
The original checkout remained clean at the base commit during final checks.
No live session/SQLite writes, game capture, controller connection or input
was performed. No message was sent to or interruption made to the Terminal player.

After the user stops the player: compare the bundle against then-current HEAD,
integrate without overwriting intervening work, update the installed skill from
the reviewed checkout, and use a fresh Luna play session. Resume from actual
screen/ledger/pending-action evidence. Validate a live merchant visit and a
single-option map entry against the timing targets before claiming live speed.

MIRA outcome: APPROVED. The independent review corrected single-node evidence
wording, the meaning of unchanged UI fields during transitions, and custom
output-path reuse instructions before approval. Report:
`artifacts/merchant-map-review/mira-review.md`.

## Accepted stopped-player correction

The owner stopped Terminal on the floor-4 map and requested diagnosis/fix of
the floor-5 node choice. Archived evidence shows 64/80 HP, 39 gold, a merchant,
a normal enemy and a question mark. Terminal misclassified the enemy as elite
and the question mark as an unread icon. It then reused center focus after a
verified Left moved focus to the merchant; the next Left wrapped to the question
mark. Acceptance: reuse verified actual map focus, reconcile a reviewed focus
mismatch without repeating input, preserve uncertainty and original evidence,
and give clear one-choice map guidance. Validate with fake input and archived
evidence, obtain MIRA review, then integrate while the player is stopped.
No gameplay, live capture, arming or controller connection is authorized here.

Implemented: the session retains the sealed actual map result; `--last-result`
reuses it, and an old snapshot bound to the same verified image refreshes only
focus. Compact focus results derive pending IDs and output paths. Learning
verification accepts actual sibling focus plus explicit icon corrections while
preserving resources, inventory, context, IDs, positions and reachability. It
logs each mismatch without inventing controller edges. An action-bound review
can resolve the same image previously logged as unknown; the old audit remains.

Final full offline regression: 1,603 tests, one optional local SDK skip, 14.635
seconds. A final bounded icon-kind validation also passed the affected map and
telemetry tests. Skill validation and diff checks passed. MIRA APPROVED after
independent archived-image review and instruction corrections; report is
`artifacts/merchant-map-review/map-repair-mira-review.md`.

The exact real pending action was replayed against disposable session/SQLite
copies with a controller factory that throws on use. Historical verification
resolved the pending focus, recorded both corrected icon types, and advanced
the completed count from 66 to 67 with zero controller calls. No node was entered.
The two Shining Light upgrade identities remain uninspected; focus recovery
does not establish a complete current deck. Live timing remains unmeasured.
