# Reward and forced-path performance correction

Accepted scope: prepare isolated improvements for the user's reported slow gold,
card reward and single reachable map node flow. Keep the running player, live
SQLite, session and installed skills untouched. Base: 05ba13d.

Observed floor 5 evidence (UTC 2026-09-29): adapter verification plus logging
was 63.05 ms for gold, 49.79 ms for opening cards, 40.55 ms for selecting
Headbutt, 64.05 ms for confirming it and 36.71 ms for Proceed. This includes
validation and session bookkeeping; it is not an isolated SQLite benchmark.
Gold input was 01:43:32; card offers input was 01:47:45. The transcript shows
repeated result packaging and a rejected real confirmation phase. No evidence
supports blaming those minutes on SQLite.

Acceptance:
- Gold and opening offers remain deterministic; no repeated strategy analysis.
- Reuse sealed actual results and the canonical session context; no handwritten
  action identifiers or repeated whole-inventory JSON for routine outcomes.
- Represent card selection and visible confirmation separately. Record acquired
  inventory only after the observed acquisition, preserving one card decision.
- Keep the small synchronous durable result before the next input; no separate
  reporting/SQL task between reward inputs.
- Trace immediate outgoing map edges first. One visible reachable node requires
  no optional future-map survey or boss research before entering it.
- Exercise a full reward sequence with fake input/private SQLite and negative
  cases. Offline evidence does not establish real end-to-end latency.
- Obtain independent MIRA review for the player instructions before completion.

Implemented in this isolated branch:
- Compact observed loot result modes (gold, offers, focus, confirmation,
  acquired, returned, map), with automatic pending IDs and unique packet paths.
- Sealed last-result reuse plus canonical session context and retained card
  decisions. Unverified/modified results cannot become the next snapshot.
- Card-select/confirm distinction, confirmation-hint support and deck-count
  prediction. No inventory acquisition event on highlight/selection.
- Matching optional result decision policy accepted without letting a result
  alter the pending policy (the old extra-field rejection no longer applies).
- Compact per-action timing display; explicit summary or --verbose-timing
  retains full detail. Actual errors and pending/recovery fields remain intact.
- Immediate outgoing map edges take priority over optional upper-map surveys.

Validation: 63 focused tests passed; full suite ran 1,614 tests: 1,613 passed,
1 optional SDK test skipped. Skill validation and whitespace checks passed.
Fake-controller reward replay reached map after five inputs, persisted one
Headbutt acquisition, and did not duplicate inventory on result replay.
The existing forced-node adapter test directly activates and verifies arrival.
These are software/declared-observation tests, not automated image recognition
or live timing measurements.

Deployment: not installed or merged into the live checkout. Do not replace
loaded modules or installed instructions while Luna has pending input. Apply
at a settled, reconciled restart boundary; no new game run is required.

MIRA refinements: explicitly hand verified map results from the loot helper to
quick map travel; distinguish the current floor's circled node from older
visited circles; allow concise observed-result notes up to 2,048 UTF-8 bytes
with a field-specific error. Follow-up result/merchant tests passed (28 tests).

Compatibility: the saved card-decision key now ignores changing control proofs
and normalizes offer semantics. An older saved decision file can be rejected
once after installation. At the settled restart boundary, re-bind its existing
option/reason against current verified offers with --choose/--reason and a new
--decision-output; do not repeat strategy, reuse an invalid key or resend input.
Prefer integration between reward sequences. No automatic live hot reload.

Independent MIRA outcome: APPROVED after inspecting all five archived images,
source-free preview, helper/docs consistency, final regression evidence and
integration limits. Review: artifacts/loot-speed-review/MIRA_REVIEW.md.
Final full suite after review refinements: 1,614 tests, one optional skip,
no failures. Live end-to-end latency remains unmeasured for these changes.
