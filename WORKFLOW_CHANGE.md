# Accepted scope — 2026-09-28

The Product Owner approved three improvements: event-bound evidence instead of a blanket 30-second limit during stable turns; tolerant, unambiguous file request submission; routine loot collection without repeated strategic reasoning.

Additional accepted scope: the Product Owner approved the map-specific short
navigation-and-verification flow. Retain inspected topology and routes per
run/act, reuse the destination across focus taps, package routine records, and
reassess material changes. Merchant automation is outside this addition.

Implementation is isolated in this clone on codex/workflow-state-loot. Do not connect to the live bridge, read/write active run files, install personal skills, or modify the original checkout. Test with synthetic captures, disposable databases, and fake controllers only. Activation is deferred until the running player has stopped.

## Implemented and verified

1. Session/input epochs replace elapsed-age rejection for action packets bound
   with `--session`. Each attempted input, outside-change invalidation and
   restart/disarm invalidates the relevant earlier binding. Dispatch rechecks
   the epoch, original source, context and owned bridge readiness. Startup arm
   identity remains recent; old callers without a session retain their fallback.
   Combat/menu/inspection results can be recorded after a delay with their exact
   pending action and original after-image. SQLite retains original capture and
   later review times. No timestamp rewriting or input replay is introduced.
2. Both canonical `request_file` and `operation:request_file`/`path` envelopes
   load the exact same bounded action packet. Conflicting fields, duplicate
   keys, nested envelopes and nonregular files remain rejected. A real CLI
   shadow-session test covers both spellings without a controller.
3. A deterministic loot helper collects free gold, collects potions into
   confirmed empty slots, opens card offers and proceeds when rewards are done.
   Card/skip choices are made once and reusable across focus changes, then
   invalidated when the reward set, resources, inventory or context changes.
   Outcomes and inventory additions come from actual inspected results. No
   automatic potion discard or indiscriminate card addition is introduced.

Regression: 1,558 tests ran in 12.247 seconds: 1,557 passed, 1 skipped (optional
local bridge SDK environment absent from the isolated clone). Fake controllers,
private temporary sockets/databases, synthetic images and copied immutable
archive fixtures only. No live gameplay input, capture or bridge contact.
MIRA: APPROVED for the instruction/preview scope; report is retained at
`artifacts/workflow-review/mira-review.md`. This does not claim live timing or
console-layout validation. New helper navigation remains observed step by step.

## Map addition — implemented and verified

`veda_map_step.py` now stores reviewed topology and routes per run/act, retains
the selected destination through focus changes, and packages ordinary checked
menu requests and compact focus results. A saved route advances from the next
actual map snapshot. Inventory/material-resource changes request a new choice
from the advisor, without disarming or asking the player for permission. The
default review thresholds (15% maximum HP, 50 gold) are configurable heuristics;
explicit health/shopping thresholds can trigger review on smaller changes.

Partial maps and mid-act resumes need only a visible reachable next node.
Static bound views keep their original timestamps without a recapture cutoff.
Top coverage requires an inspected boss node or portrait. Contradictory archive
data remains preserved; a newly reviewed one-step route can proceed separately.
Entry resources are forecasts; actual room identity and outcomes still come
from the normal observed verification. No input batching or blind replay.

Regression: 1,577 tests ran in 12.837 seconds: 1,576 passed, 1 skipped (optional
bridge SDK environment absent). This adds 18 map-travel tests and one cropped
map boundary regression. The terminal helper was exercised through the real
reviewed adapter with a fake controller and temporary SQLite: Right, verified
focus, then Cross and verified merchant entry. The same inspected focus-result
image supplied the next action, with its exact input-epoch binding. Skill
validation and diff whitespace checks passed. Regression log and synthetic
preview are under `artifacts/workflow-review/map-*`.
MIRA: APPROVED; independent review saved as
`artifacts/workflow-review/map-mira-review.md`. Its two instruction findings
(redundant final-capture language and the conflicting-cache fallback) were
corrected before approval.

The 20-second ordinary logical-move target remains unmeasured on the live PS5.
Activation and live timing verification remain deferred; merchant automation
is not part of this map addition.

## Activation remains deferred

Base commit: d6a91f4fc510af9362f85f533e0fdc8074ac9d42.
Separate branch: codex/workflow-state-loot.
The original checkout was still clean on d6a91f4 at final verification. No
changes were made to its live SQLite, session files, personal installed skill,
controller connection or running processes.

After the player is stopped and its controller released: review the isolated
commit against the then-current checkout, integrate it without overwriting any
intervening work, update the installed Orchestrator skill from the reviewed
checkout, and start a new Luna session. Do not hot-reload these files into the
running player. Live acceptance should measure reward collection and verify
that a settled decision longer than 30 seconds completes without a recapture
loop. Existing pending inputs must be reconciled before rearming as usual.
For map acceptance, measure an ordinary cached selection including focus and
room-entry verification, confirm no repeated survey/reasoning between focus
taps, then verify a changed health or inventory situation triggers one new
strategic choice while preserving saved topology.
