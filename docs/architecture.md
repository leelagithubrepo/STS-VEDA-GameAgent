# Architecture

## Boundaries

`veda.knowledge` holds evidence-backed claims. It does not decide actions.

`veda.experience` preserves immutable decision records and aggregates repeatable evidence. It does not rewrite researched facts.

`veda.agent` orchestrates the loop and delegates game-specific operations to an `GameAdapter`.

`veda.sts` defines the initial Slay the Spire state shape and adapter interface. It intentionally contains no preferred card, path, or strategy.

`veda.combat` validates a proposed sequence against observed energy, targets,
Artifact, Block, Weak, Vulnerable, and Frail before it can be recommended.
When player HP, total current intent damage, and end-of-turn status damage are
all confirmed, it also projects survival after the proposed sequence and rejects
lethal turns. `veda.bosses` confirms boss identity only from readable name text;
it does not infer a boss from a portrait or expected map outcome.
`veda.state_diff` compares observations to verify predictions. `veda.run_ledger`
maintains confirmed inventory separately from visual guesses. `veda.map_reader`
refuses route readiness when the boss or reachable nodes are uncertain.

`veda.preflight` is the integration point. A teaching-mode recommendation must
pass this gate before it is shown: the combat/map observation must be fresh,
the sequence must be legal, targets must be explicit, and a prediction must be
available for post-action verification. It fails closed rather than guessing.

`veda.human_guided` is the interim execution layer: it hands an approved move
to a person, permits no second move until a newer screenshot verifies or
abandons the first, and appends the outcome to the experience store.

`veda.run_ledger` is the confirmed-inventory boundary. It retains duplicate
cards and potion slots, records explicit potion replacement/discard events, and
can hold a confirmed combat hand and energy value. A recommendation using a
named hand must consume only cards visible in that hand; deck membership alone
is not enough.

`veda.combat_state` is the transition from vision into arithmetic. It requires
the visible player HP/Block/energy, every enemy's name/HP/Block, total intent
damage (with confidence), and visible end-of-turn status damage. Its snapshot
may be passed through `preflight_verified_combat`; omitted or low-confidence
values fail closed rather than becoming LLM assumptions.

`veda.controller_verification` records a human controller press as an intent,
not an achieved action. The next observation must match the preflight's
explicit predicted fields before the attempt is confirmed. `veda.potion_safety`
requires named, capacity-checked ledger slots before a full-inventory potion
replacement. `veda.prediction_telemetry` aggregates exact-match-only forecast
accuracy without promoting outcomes into game knowledge.

`veda.mailbox` is the standalone review boundary. VEDA submits a durable review
request after a floor, major choice, or danger event; the LLM reviewer returns
feedback; VEDA explicitly reads and acknowledges it before treating it as
received. Separate terminal sessions do not imply delivery. The current
mailbox is local and file-backed; its message schema can migrate to SQLite
when VEDA becomes a packaged application.

`veda.floor_telemetry` and `veda.relic_inventory` create the player-facing
Report Card. They record floor outcomes, rewards, losses, actions, strategy,
one or two evidence screenshots, and confirmed relic properties with sources
and confidence. The published Report Card never exposes private LLM↔VEDA
review messages; it presents only the verified run record.

## Source and confidence policy

Each claim has one or more sources. A source records URL, publisher, type, captured time, and a reliability assessment. Claims retain conditions and rationale. Contradictory claims coexist; retrieval returns each matching claim, never a forced consensus.

Experience produces evidence events. Only a future evaluator may promote or adjust a hypothesis; it must use repeated evidence and leave an audit trail.

## Adapter contract

An adapter is responsible for:

1. obtaining a structured observation;
2. normalizing it into a serializable game state;
3. listing only currently legal actions;
4. executing an action; and
5. observing the resulting state for verification.

The agent refuses to execute an action that was not listed as legal for the same observation. This protects against stale UI interpretations.

## Decision policy seam

`DecisionPolicy` receives the current state, legal actions, retrieved knowledge, and related experience. It returns a `Decision` containing action, rationale, confidence, and prediction. The default policy is deliberately conservative: it chooses no action. A future reasoning model can be introduced here without changing evidence, control, or audit formats.

## Standalone review protocol

1. Before a meaningful decision, VEDA reads its mailbox and records the
   current game state and relevant evidence.
2. After every floor, and immediately after a major choice, Elite, boss, death,
   or dangerous HP state, VEDA writes a review request with linked records.
3. The LLM reviewer returns evidence-bound feedback. VEDA acknowledges receipt
   and records any implementation or clarification request.
4. The Report Card regenerates from floor and inventory evidence. Strategy
   changes remain proposed until evidence and a regression test warrant
   promotion.
