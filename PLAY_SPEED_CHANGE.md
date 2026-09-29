# Accepted play-speed implementation

User authorized all changes in the floor-5 performance review before starting
the Terminal player. Base includes prepared reward commit 03bfbcb.

Scope: response-driven local adapter submission (replace 10/30-second terminal
waits); combat state/decision reuse and compact actual results; bounded known
hand navigation with final observed focus before card selection; automatic
packet/cache paths and validated immediate-map edges; integration of prepared
loot changes and current Orchestrator instructions. Keep immediate durable
outcomes, actual observations, distinct card IDs and pending-action tracking.

No live controller input or player launch is authorized for this development
turn. The stopped run has pending map-focus action de6fac6f-986c-4608-a4e5-
62d25d2771df. Preserve it for observation/reconciliation on resume; never replay
or clear it to make startup look ready.

Acceptance: isolated fake-controller/private SQLite tests cover complete card
play and rewards; response submission returns on matching acknowledgement;
interrupted batches remain pending and cannot replay; map cache paths do not
collide; immediate-edge selection excludes future forks. Full regression and
independent MIRA review before installation. No live latency promise.

## Implemented and verified

The persistent adapter has an opt-in private request/reply endpoint. The
short-lived submit helper returns on its correlated reply, preserves unknown
submission outcomes, and can capture an acknowledged input's result without
another model round trip. Legacy JSONL remains available. No authority or
controller ownership moves into the client.

Combat retains sealed actual state and the incomplete decision through focus
and selection. Compact reviews expand only explicit observed values and
unchanged declarations. Known complete hand navigation supports up to four
identical direction taps, never card selection/target confirmation. A partial
or uncertain delivery remains pending. Card resolution invalidates the saved
choice. Canonical next-turn IDs replace provisional IDs on reuse.

Map helpers project reviewed outgoing edges, retain only immediate choices,
version caches without filename collisions and allow an actual corrected map
reading to resolve a pending focus press without claiming room entry. The
prepared reward improvements collect routine gold/open offers without repeated
strategy and preserve a selected card through its actual confirmation.

Verification: final isolated regression ran 1,630 tests in 15.549 seconds;
1,629 passed and one optional dependency test skipped. Added tests cover real
adapter execute/verify through private sockets with fake controller/SQLite,
lost replies without resubmission, four-direction navigation followed by two
completed card plays, retained-state seals and generated CLI packet paths,
canonical turn IDs, immediate map edges, cache versions and map correction.
Skill validation and diff checks passed. No live game input was sent. These
tests establish workflow correctness, not live PS5 timing or recognition.

The previous measured segment needed 18m46 from arm to verified victory,
16m34 for rewards and 10m37 afterward on map. This change removes repeated
work and fixed persistent-Terminal waits; it does not claim those durations
have already improved in a live run. Next-play targets remain 20 seconds per
ordinary logical move and 4 minutes per ordinary combat floor, with actual
measurements retained. A target overrun does not stop gameplay.

Installation includes both the prepared loot commit and this update, plus the
installed Orchestrator skill. Preserve the stopped live pending action exactly;
next launch must inspect/reconcile it with the corrected map-result helper.
