# Autonomous recovery change

Authorized scope: let the terminal Orchestrator recover packet and helper
failures without player mediation while preserving the same run, action IDs,
input epochs, evidence and SQLite history.

Implemented:

- learning-policy combat drafts tolerate nonnegative numeric pile counts as
  unknown; strict policy and negative counts remain rejected;
- generated combat-result envelopes are unwrapped for compact actual-result
  review only after their pending action ID matches, unsupported fields are
  rejected, and `others_unchanged` remains explicit;
- launcher and skill instructions classify pre-input, post-input and
  infrastructure failures, repair source-free packets, reconcile attempted
  inputs without replay, and stop adapter plus bridge before product-code
  repairs;
- added `docs/veda-recovery-play.md` with the recovery order and hard stops.

Validation:

- 44 combat/launcher tests passed;
- 52 execution/guard/recovery tests passed;
- 62 reviewed-play tests passed;
- 8 private Unix-socket adapter tests passed;
- skill validation and Python compilation passed;
- no bridge, controller input, capture or live session mutation occurred.

MIRA review approved the bounded recovery policy after action-ID, strict-policy,
and bridge-stop wording corrections. Live end-to-end recovery remains to be
measured on the next run.
