# Evidence needed for the next action

The operator needs enough reliable information to check the next action and its
result. It does not need to know every future draw, hidden enemy move or random
reward. This policy guides inspection and planning; it cannot override the
reviewed adapter's required fields, source checks, inventory coverage, controller
evidence, lifecycle checks or pending-action journal.

The player's policy is to keep moving under uncertain game state: favor survival
and the lowest defensible damage risk. Missing exact state calls for conservative
planning or inspection. It is not a standalone session-stop condition.

## Choose conservatively

Compare supported legal candidates, including protective cards, lethal damage,
potions and setup when applicable. Avoid lethal outcomes when an available
checked alternative does so. Then prefer lower damage risk over the supported
planning horizon; do not protect this turn at the expense of greater known
near-term harm. State the horizon and uncertainty behind the choice.

Where the checked rules provide several possible outcomes, compare their
conservative damage bounds. Use expected damage only when applicable probabilities
are established. Missing probabilities are not zero probability, and an unknown
attack is not zero damage. Inventory and setup can break otherwise comparable
choices; no fixed "always Defend" or "always use a potion" rule replaces judgment.

This preference applies to the advisor's checked candidates and the routine
planner's supported numeric bounds; it is not an exemption from adapter
requirements. The routine planner now ranks a higher conservative player-HP
bound ahead of damage dealt, then uses enemy HP and action economy to break
ties. It does not enumerate every potion, card interaction or future turn.
If the adapter cannot validate any candidate despite a readable game and functioning
controls, identify the exact capability gap for Builder. Preserve the failed
check and considered alternatives; never fabricate facts or dispatch raw input
to conceal that gap. This implementation limitation remains to be expanded where
encountered and must not be described as a strategic need for perfect certainty.

## A card action is not automatically End Turn

The reviewed adapter checks one card's immediate legality without requiring that
the player could already survive ending the turn after that card. For example,
at 5 HP facing 10 damage, the first Defend leaves 5 Block and the player is still
at 5 HP. A second Defend can complete the defense. Rejecting the first card
because ending the turn immediately would be fatal prevents that valid setup.

The first Defend still requires fresh evidence and one verified result. Re-read
the hand, energy and Block before checking the second; lookahead never sends a
sequence automatically. End Turn and a card that actually forces the turn to
end still require the survival check. Immediate lethal HP costs remain rejected.

Internally, `check_plan` retains its strict `complete_line` default. The reviewed
adapter and bounded routine search explicitly use `survival_scope="action_prefix"`.
This is a checker API option, not a user-supplied request flag that disables
checks. The forecast labels its horizon as `if_turn_ended_now` or
`committed_enemy_turn`, and exposes `survival_required` and whether the bound
establishes survival (`survival_established`). A hypothetical HP value of zero is not a claim that playing the non-turn-ending
card immediately killed the player.

Routine search can extend a legal defensive prefix to a supported survivable
continuation, rank it, and return only the first action. Its `prediction_horizon`
and notes describe the first action's conditional HP projection. It does not
present a later planned card's Block or HP as already observed. Search limits
and unsupported draw/effect boundaries remain explicit.

## Continue, inspect or pause

For each candidate, identify what its legality, cost, target, relevant effects
and outcome verification depend on. Classify an unknown according to that
dependency, rather than stopping because the word "unknown" appears:

| Situation | Operator response |
| --- | --- |
| Confirmed next map option beside an unread icon | Exclude the unread option. Evaluate the confirmed option normally, with its own source/control/route checks. |
| Cropped future map | Keep unshown paths unknown. Use confirmed visible connections without claiming a safe route or unseen rest site. |
| Supported random outcome | Use applicable reviewed bounds or complete alternatives; verify the observed branch afterward. Do not predict its identity as fact. |
| Readable Collector nonattack category covered by the existing A2 contract | Keep the exact move unknown and apply the reviewed conservative bound, including other enemies. Bare unknown intent is not zero damage. |
| Proposed card needs an unread pile | Inspect the pile using a supported checked action, or exclude that card and evaluate another candidate whose complete review passes. |
| Two legal tactical preferences | Choose using strategy and state the reason. A disagreement about observed energy, HP or other required facts must be resolved first. |
| Source expired before dispatch | Capture and inspect a fresh source, cancel an unsent prepared proposal if necessary, then prepare again. Never rewrite the old capture time. |
| Required inventory, target, effect or menu control cannot be checked | Inspect the missing evidence if a supported inspection exists. Otherwise exclude the candidate and evaluate a protective alternative. If every action is technically unsupported, record the exact adapter capability gap. |
| No game video, wrong/unknown run, bridge fault, unresolved dispatch or failed consequential logging | Pause controls, preserve evidence and pending state, close the owned bridge and report the specific problem. |

Inspection is work, not an automatic session failure. Use the smallest relevant
view. Never press a button simply to discover its binding. A controller-assisted
inspection still needs the same reviewed input checks as another action.
For the same unchanged evidence blocker, allow at most two unsuccessful fresh
capture/inspection attempts; then re-plan among supported candidates rather
than treating that inspection budget as an automatic session stop or looping
through stale captures. If every candidate is rejected, preserve the concrete
capability gap. A material change, such as a restored picture or newly readable required fact, can justify
a new assessment. A new filename, timestamp or unrelated animation does not
reset the attempt budget. The limit does not permit replaying an
uncertain input, reconnecting after a fault or overriding a rejected contract.

If an actual input was attempted, no further gameplay input is allowed until
the adapter establishes resolution and clears its pending action. Reconciliation
that records an unknown result retains that pending action and does not permit
continued input. Multiple valid outcome alternatives are useful
only when exactly one matches. A screenshot showing an unsupported result is
evidence to preserve, not permission to broaden the contract after dispatch.

## Preserve a useful blocker record

Keep a private JSON record alongside the session artifacts when an evidence
problem prevents progress. Use the existing screenshots, action IDs and SQLite
context rather than copying entire history. No new database schema is required.
The operator supplies these factual fields; this record is descriptive and
grants no permission:

```json
{
  "schema": "veda.action-blocker.v1",
  "run_id": null,
  "action_id": null,
  "last_verified_state": {},
  "missing_fact_or_failed_check": "Exact missing fact or adapter error",
  "affected_action": "The proposed action that depends on it",
  "category": "missing_action_evidence",
  "inspections_attempted": [],
  "alternatives_considered": [],
  "evidence": [],
  "input_status": "not_attempted",
  "pending_preserved": true,
  "next_recovery_step": "Specific supported inspection or external repair",
  "bridge_cleanup": "not_started"
}
```

Use null for IDs that cannot be established; do not attach a no-video capture to
an arbitrarily selected run. Suggested categories are `missing_action_evidence`,
`unsupported_contract`, `identity_or_video`, `control_or_delivery`, and
`logging_failure`. Evidence entries identify actual retained source path/hash
and capture time. Input status distinguishes not attempted, proven not sent,
attempted with unresolved delivery/result, and verified. Do not mark pending
state cleared or cleanup complete without the adapter/bridge evidence.
When describing recovery, state whether the adapter actually cleared pending
input. Avoid "continue after reconciliation": a successful reconciliation that
records `unknown` deliberately leaves the input pending and blocks further play.
For an `unsupported_contract` report, include the actual rejected adapter check
and the candidate/inspection alternatives considered. This is a product gap to
fix, not a request for the player to guess hidden state or repeat authorization.

In the player update, lead with the concrete issue and next step, for example:
"The capture says No Video, so I cannot identify the game screen. No input was
sent. Restore the picture in QuickTime; the current-attempt authorization is
already recorded." Avoid "uncertain, stopped" without an explanation.

## Parallel development and rollout

During evidence collection, the play task owns controller actions and run
telemetry. Builder works in a separate checkout and uses temporary databases
for tests. Do not change the active checkout or installed play instructions
under a running operator. Read-only inspection of retained play artifacts is
permitted; do not edit its session state or resolve its actions from Builder.

The reviewed skill source is tracked in `skills/orchestrator/`. After the
play session reaches a safe stop and its adapter/bridge are closed, merge the
approved documentation/skill-source change and install its `SKILL.md` and
`references/operating-loop.md` into the matching existing personal skill. Compare
the current installed files against the reviewed baseline first, preserve
unrelated files/metadata and keep rollback copies. A fresh Orchestrator session
must load the revised instructions; an already-running session may retain older
ones. Staged guidance is not a deployed fix and an offline behavior check is not
evidence of live speed, recognition accuracy or improved win rate.
