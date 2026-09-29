---
name: orchestrator
description: "Play the current Slay the Spire run with Spire strategy and VEDA's reviewed PlayStation adapter. After current-run arming, keep playing under gameplay uncertainty, record decisions and learn from observed outcomes."
---

# Orchestrator

Play the currently open, authorized attempt. Spire supplies game strategy;
Orchestrator owns controller execution and outcome logging. Read the Spire
skill for advice, but keep its advisory-only role intact.

The player's policy is **learning through continued play**. Missing enemy
rules, uncertain forecasts, incomplete future maps, optional potion reviews
and imperfect strategy are reasons to choose and observe, not end the task.
A suboptimal move or a lost run is an acceptable learning outcome. Do not
require a guaranteed win or a complete model of the game before acting.

## Start once, then keep playing

Read `docs/veda-play-hot-path.md` and only the guide for the current screen.
Use `veda_play_context.py` for recorded IDs, inventory and pending actions;
these are historical expectations until compared with the live game.

Current-run authorization such as **ARM ORCHESTRATOR FOR THIS RUN** starts
continuous play after preflight. The launcher supplies it. Do not ask again
when that authorization already exists. Development approval alone does not
start game control. Do not change save profiles or begin a different attempt.

Confirm the intended game feed, actual attempt, exclusive controller ownership
and bridge readiness. A readable Neow opening uses the existing registration
helper; a finished run's ledger does not become a new run's identity.
Start one owned bridge with `./scripts/warm_bridge --idle-timeout 0 --socket
/tmp/veda-ps5-bridge.sock`. Require its `command_channel:"unix_socket"` and the
adapter's own ready preflight. Use `veda_reviewed_play.py --mode codex
--decision-policy learning --request-server`. This is a gameplay policy, not automatic arming.
Follow `docs/veda-arm-startup.md` to stage, inspect and submit the arm review.

After arming, take the first action and keep working in the same turn. An
armed response, timing overrun, rejected draft or verified floor boundary is
not task completion. Do not return a final response asking for “continue.”

Use `veda_submit.py --session SESSION_DIRECTORY --request PACKET.json` for
request/reply submission. It returns on acknowledgement; avoid fixed 10/30-second
Terminal waits. Add `--capture-after` for action packets and display the returned
image in the same tool call. Inspect before verification; no helper recognizes
pixels. Binding already validates drafts, so ordinary steps need no separate
validation call. On a lost reply, inspect the existing pending action; never
resend it. See the hot-path guide for transport/recovery details.

## Decide, act, observe, remember

1. Inspect the current screen. Use confirmed inventory and relevant recorded
   outcomes; retrieve `veda_play_lessons.py` once per encounter or meaningful
   new mechanic, not on every focus tap. Historical lessons are suggestions,
   never a claim about current cards, HP or enemy intent.
2. Choose one useful move using survival, damage, block, potions and future
   setup. Preserve uncertainties in the draft. A missing damage bound stays
   unknown; it need not prevent End Turn. Briefly explain the tradeoff.
3. Use the compact combat/menu helper with `decision_policy:"learning"` and
   bind with `--session SESSION_DIRECTORY/state.json`. An inspected settled
   state does not expire just because time passed. Capture, inspect, bind and submit
   `--execute` for one reviewed operation through the armed adapter. A warning is information
   for the decision; it is not a request for player approval.
4. Observe the result and verify what actually happened. Focus or selection
   is not card play. An unexpected HP, energy, status, draw or reward can be a
   verified result: record the difference from the prediction and re-plan.
5. Finish the outcome log, use returned context IDs, then choose the next move.
   Keep the chosen action, reason, uncertainties, available prediction and
   observed result linked. Recorded cases inform later choices; they do not
   automatically retrain the model or establish a universal rule.

For combat use the guide's `--last-result`, compact observed results and generated
paths. Retain the card decision through navigation/selection; choose again after
its effect resolves. With actual hand focus, known order and default PS5 controls,
`--bounded-hand` groups up to four identical direction taps; verify final actual
focus before selecting. Other operations remain separate. A partially delivered
batch remains one pending action to reconcile, never replay.

Describe the actual combat focus domain. A raised hand card with keyword help
is still hand focus; do not dismiss it with Up. Status, relic and potion focus
are separate locations. Use the combat guide's observed, single-step recovery
and report its actual destination, including no progress. Never declare hand
focus merely because the intended action was to return there.
On each image, read the yellow focus corners: around Ironclad means player
inspection; around an enemy with no card selected means enemy inspection.
A selected card and target prompt remain targeting. The whole hand's playable
glow does not identify a focused card. An unexpected result of `card_focus`
still uses `veda_combat.py --result` with actual focus and unchanged gameplay;
verify it before preparing recovery. Do not swap to an inspection result schema
or repeat the delivered input.

Use `docs/veda-evidence-continuity.md`: each input invalidates prior action
bindings; results can be recorded later without replay. Report observed manual
input or outside screen changes with `invalidate_evidence` and inspect again.
Submit the helper's `request_file` object unchanged; the operation/path spelling
is also accepted. Never resend gameplay input to repair a result envelope.

For combat loot use `docs/veda-loot-play.md` and `veda_loot.py`. Collect free
gold with the fixed routine reason, collect potions into confirmed empty slots,
and open card offers. Use its compact `--result` commands, generated packet
paths and `--last-result` instead of rewriting snapshots/inventory. After gold
verification, immediately prepare the next routine reward action from that
same inspected state. The adapter already logs the outcome; defer optional
reports and separate SQL work until rewards are handled. Choose the card or
skip once, retain it through focus/select/visible confirmation, and record the
card only after actual acquisition. Do not repeat strategy or boss research
for routine loot. Timing history is available on request, not a task to
reanalyze between taps.

For merchants use `docs/veda-shop-play.md` and `veda_shop.py`. Compact result
commands, session-derived IDs, generated packet paths and `--last-result`
avoid rewriting snapshots. Inspect priced slots separately from tooltips:
Wound beside Wild Strike is a preview; SALE does not mean upgraded. Compare
affordable goods, removal and saving gold once, retaining unknowns honestly.
Keep the buying decision through focus, selection and actual confirmation.
Learn focus from actual slot identities. Verify purchase, Leave and Proceed
separately. Do not leave for lack of navigation examples or write request
builders during play.

For screen-specific commands, use `docs/veda-combat-play.md`,
`docs/veda-menu-controls.md` or `docs/veda-map-play.md`. Default PS5 profiles
can supply known controls when a glyph is absent. Inspect actual upgrade cards
after opening the picker. On maps, use `docs/veda-map-travel.md` and
`veda_map_step.py`: identify the current floor's circled node from the HUD and
canonical current-node record (earlier visited nodes may also be circled),
then trace its outgoing edges and inspect the immediate reachable set. Future forks and the legend do not
require scrolling when those current edges are readable. Save a route per
run/act, retaining its destination through focus changes. Exactly one confirmed reachable node uses the automatic forced
move: omit the decision file and skip route/boss analysis, then verify entry.
At actual forks retain the chosen destination through focus taps and reuse inspected topology. The helper automatically versions/reuses the per-act cache. Use `--connections` to derive immediate choices from reviewed edges, and `--reobserve-result` to correct a misread selectable set after pending focus while preserving unchanged gameplay facts.
After verifying focus, use `--last-result` to carry the actual state forward;
never reuse the original focus declaration. Record unexpected actual focus with
`--focus-result` and continue after verification, without resending the tap.
A visible question mark is `event`, not an unread `unknown` icon. Check the
legend before calling a normal enemy an elite. With little gold, prefer a useful
fight/question mark unless a concrete merchant or route benefit warrants it.
Reassess material changes; inspect
future branches/boss only when useful. `strategy_required` means choose once
and continue, not ask permission or stop. Incomplete future coverage should
not prevent choosing a visible reachable node. Strategy guidance such as
Barricade before Corruption, potion timing or elite readiness is advice to
weigh in context, not a mandatory checklist before every card.

## Recover within the play loop

`recoverable_review` means keep the task running. Follow its specific recovery
step: repair an unsent draft, inspect a changed state, choose another playable
card, inspect a changed UI, or finish logging. Keep the owned connection when
healthy. Do not close/re-arm just because a strategy forecast is unavailable.
After two unsuccessful inspections of the same unchanged fact, switch approach
or choose a conservative available action instead of repeating the same work.

An action that may already have been sent needs fresh observation and outcome
reconciliation before another input. Never blindly replay it or erase its
pending record. This is a temporary recovery step, not a game-strategy stop.
Verified results awaiting logging use `finalize`, not another button press.
Do not fabricate evidence, rewrite capture times or modify product code during
play. If a helper cannot express the observed situation, record the specific
capability gap and keep pursuing a supported inspection or alternative. Report
an ongoing recovery honestly; do not claim progress when the floor is unchanged.

Measure first input (90s), ordinary move (20s), noncombat floor (90s), ordinary
combat (4m), elite (6m) and boss (8m). These are targets, not measured guarantees
or stop deadlines. Report an overrun briefly with the delay and next step;
thinking, inspection and recovery remain on the clock.

## End conditions

End play on confirmed defeat, confirmed completion of this run, or the user's
Stop instruction. Stop controls for actual hardware/network/bridge failure or
loss of the game feed/target; restore the connection and identify the game
before resuming. Preserve any pending action and verify owned bridge cleanup.
A normal combat victory continues to rewards and the next floor.

Uncertainty about game strategy, a new event, a missing rule, a forecast miss
or a time target is **not** an end condition. Keep deciding and learning.
See `references/operating-loop.md` for recovery details. Do not use system-level
controller buttons, change device permissions, publish the site or perform
development work as part of a run.
