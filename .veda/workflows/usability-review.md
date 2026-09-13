# Builder ↔ MIRA usability review

1. Builder opens or updates a pull request for a user-facing VEDA change.
2. Builder completes the MIRA review request with the preview/build, audience,
   user goal, screenshots, and a concise change summary.
3. MIRA reviews independently and returns `KEEP / CHANGE / REMOVE / TEST` plus
   an outcome: `APPROVED`, `APPROVED_WITH_CHANGES`, or `NOT_READY`.
4. Builder addresses findings and requests re-review. Repeat until approved.
5. Escalate only unresolved product decisions to the Product Owner.

## Current trigger

Until a separately authorized automated reviewer exists, request MIRA by
opening the repository's MIRA Review issue (or using the PR checklist) and
asking Codex to review against `.veda/team/mira.md`. This keeps the packet,
review, decision, and follow-up durable in GitHub without pretending an
autonomous MIRA service is running.

## Future automation

An approved GitHub Action or agent integration may create the review packet and
post a MIRA review. It must use explicitly configured credentials and retain
the same role boundaries and approval outcomes.
