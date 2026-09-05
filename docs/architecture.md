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

`veda.combat_state` is the transition from vision into arithmetic. It requires
the visible player HP/Block/energy, every enemy's name/HP/Block, total intent
damage (with confidence), and visible end-of-turn status damage. Its snapshot
may be passed through `preflight_verified_combat`; omitted or low-confidence
values fail closed rather than becoming LLM assumptions.

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
