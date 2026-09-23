# Spire to Builder handoff

This local workflow keeps game advising separate from product engineering.

| Step | Owner | Local record |
| --- | --- | --- |
| Identify a persistent need | Spire | Builder request with rationale and scope |
| Accept and implement it | Builder | Builder request status and implementation summary |
| Test the system change | Builder | Verification summary and focused test result |
| Confirm real-world use | Spire | Later game evidence or floor record |

The request ledger is private SQLite data. It does not run an always-on
reviewer, send messages automatically, control the game, or publish anything.

## Current adoption

The confirmed run ledger is the first Builder request. Its acceptance criteria
are: confirmed inventory remains separate from visual guesses; duplicate potion
slots and replacements remain auditable; combat hand and energy can be saved;
and the focused ledger and database tests pass.

## Route-graph request

The route-graph ledger records only the current map snapshot: the confirmed
current node, each currently legal next node, and direct edges to those nodes.
It never stores a guessed future path. Builder may attach a route recommendation
only when it names one of those legal nodes and explains both the safety reason
and the reward tradeoff. The ledger remains local and advisory-only.
