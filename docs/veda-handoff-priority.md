# Workflow handoff priority

The player treats a handoff as a short transition between already observed
screens. It must preserve the pending action and continue from the actual
screen; it must not restart planning or ask the player to mediate.

## P0: keep the controller and run safe

Check PlayStation, Remote Play, network, bridge ownership and the QuickTime
feed. If one is unavailable, pause the active timing clock, keep the pending
action, poll without gameplay input, then re-preflight and resume the same run.
Only an unresolved identity, delivery or hardware failure can stop the run.

## P1: preserve and reconcile the delivered action

An input that may have been delivered is never replayed. Capture the next
settled frame, classify the observed screen, and reconcile that exact action.
Malformed packets, stale metadata, focus mismatches and unexpected tooltips
are recoverable workflow failures. Record the mismatch and continue from the
current screen.

## P1: map activation to the actual room

After a map activation, trust the settled room screen and current floor/node
facts. If a stale icon predicted Enemy but the frame shows Rest, Merchant,
Treasure or Event, record the actual room once and hand off directly to its
handler. Do not submit another map result, re-click the node or restart the
adapter.

## P2: room handlers use deterministic paths

Use the shortest handler for the settled screen:

- Loot: collect an already focused Gold option immediately, then process the
  next reward from the fresh frame.
- Rest: at or above 75% HP with an observed unupgraded Armaments, choose Smith
  within 10 seconds; at or below 50% HP, choose Rest within 10 seconds.
- Merchant: inspect the visible prices once, choose a purchase or Leave, and
  verify the resulting screen without repeating inventory or route analysis.
- A single reachable map node: activate it directly after counting the
  immediate outgoing edges.

## P2: combat focus and card resolution

Keep a selected card through focus, target selection and resolution. A tooltip
or newly revealed hand card is an observation detail, not a reason to restart
the turn. Queue a verified sequence such as card, card, card, End Turn when
the cards are legal and no new enemy state is expected between them.

## P3: record learning data after progress

Write the interruption, observed alternative, recovery path, completion and
latency after the handoff has progressed. SQLite and reports are asynchronous
bookkeeping; they never sit in front of the next deterministic input.

The acceptance checks are: no duplicate controller input after an uncertain
delivery, no player approval for a recoverable mismatch, one fresh result per
delivered action, direct room-handler dispatch, and bounded decision time for
routine choices.
