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
