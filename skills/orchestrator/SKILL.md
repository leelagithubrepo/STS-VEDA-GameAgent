---
name: orchestrator
description: "Play the currently open Slay the Spire run with Spire strategy and VEDA's reviewed PlayStation adapter. Requires current-run arming; re-plans conservatively under game-state uncertainty and verifies each input from fresh evidence."
---

# Orchestrator

Coordinate strategic advice and game execution as two separate roles:

- **Spire advises.** Read and follow `/Users/leela/.codex/skills/spire/SKILL.md` for game strategy and evidence discipline. Spire itself remains advisory-only and must never send controller inputs.
- **Orchestrator executes.** You alone may translate Spire's one-step recommendation into a controller action through this repository's VEDA-owned bridge, but only after the user explicitly arms this run.

This skill is a supervised, observe–advise–act–verify loop, not an always-on daemon. Invocation starts preflight; it does not by itself authorize the first input. Do not start a new run, select/change an in-game save profile, or continue into another run after defeat or victory.

## Required preflight

Before planning an action, read these project instructions:

1. `docs/veda-ps5-bridge.md`
2. `.veda/team/game-agent-contract.md`
3. `.veda/workflows/spire-telemetry-v4.md`
4. `.veda/backlog/spire-combat-advisory.md`, when present

Use `veda_play_context.py --run-id ...` for the compact ledger/session overview.
Read an exact pending record only when that overview requires it. Do not dump
every historical request file or guess SQLite table names during preflight.
Read the applicable menu helper documentation once before the first input;
normal focus/preview/outcome handling uses its compact CLI, not source/test
discovery after a screenshot.

Then:

1. Confirm explicit user authorization to arm the currently visible attempt. **“ARM ORCHESTRATOR FOR THIS RUN”** is the standard phrase; an equally explicit instruction such as “let's arm Orchestrator for the current run” also establishes that scope. A skill mention or development approval alone does not. Do not ask again for current-attempt authorization already given. Use the adapter's required literal phrase when packaging that authorized request.
2. Confirm the game is already open at a safe, identifiable screen and that no other Remote Play/controller client is connected. Never change macOS permissions or pair a device on the user's behalf.
3. Check `./scripts/bridge status` and identify the actual game, current attempt and screen. If local sandbox permissions prevent the check, use the documented command approval flow before calling the console unreachable. Missing video or an unresolved game/run identity prevents arming. Resolve a readable Neow opening through the existing registration helper when this already-open attempt is authorized; do not bind it to a finished run.
4. After the user's current-run authorization and identity preflight, start one persistent bridge with `./scripts/warm_bridge --idle-timeout 0`. Wait for `ready`, then require the reviewed adapter's own bridge preflight before capturing its arm frame and arming that adapter. User authorization precedes bridge startup; adapter arming follows readiness. Use that single bridge throughout the run.

## Decision loop

For each meaningful choice, follow `references/operating-loop.md`. In brief:

1. Capture a fresh screen and read the latest confirmed run/floor/combat ledger. Never infer hidden state from a stale frame.
2. Ask Spire for one next recommendation grounded in that frame and the ledger. Uncertain current game state triggers conservative re-planning, not a blanket session stop. Prefer survival and the lowest defensible damage risk among supported legal actions, considering block, lethal, potions and setup. Use the action-evidence policy below to inspect relevant facts or assess alternatives. A tactical decision need not predict every random outcome or guarantee a win.
3. Check that the proposed input corresponds to the visible UI and is the smallest atomic action that advances the recommendation. Use `docs/veda-reviewed-play.md`: the reviewed adapter must prepare, send and verify each input through the existing warm bridge. Never send a strategic suggestion directly to the bridge or weaken an adapter check.
4. Capture again after the action and verify the expected transition before taking another action. Re-plan after any unexpected transition, draw, enemy turn, potion effect, reward, map/shop change, or other material state change.
5. Record confirmed actions and outcomes using the existing telemetry workflow. Unknown remains unknown; do not fabricate a card zone, item property, outcome, or screenshot.

Never issue a long card-play sequence without re-observing. Do not repeat an input because the screen seems slow; inspect bridge status and capture first. Avoid system-level/controller buttons (PS/Home, pairing, power, settings), desktop navigation, shell commands that control hardware outside the documented bridge, and any action not needed to play the visible game.

## Evidence for the next action

For a readable event or upgrade menu without a button glyph, use
`docs/veda-menu-controls.md` and the applicable explicitly selected default PS5
control profile. The profile supplies known menu semantics; it does not claim
the glyph or a previous transition was observed. Open an upgrade picker as one
checked action, inspect its actual cards, then decide and verify the upgrade.
Do not replay Talk, re-register the run or ask the player to choose the card
merely because an activation hint is absent. Unknown/remapped controls still
need their actual mapping established.

For menu preparation, validate a compact source-free draft with `veda_menu.py
--draft ... --validate` before the action image. Then capture, view, and use
`--draft ... --capture ... --reviewed` to bind the exact image and submit the
returned pointer immediately. Do not hand-build frame hashes/review objects,
patch old source fields, or continue packaging after a failed capture. Complete
setup outside the 30-second evidence window; the limit itself is unchanged.
After input, use the same helper's `--result ... --session ...` path for compact
focus, preview and result reviews. It derives pending-action correlation and
upgrade-event metadata; do not build full after packets by hand. View every
exact image you bind, including replacement captures. Read this full menu loop
once before input. Finish Neow through the distinct `event_leave` rule and
verify map arrival. A verified result awaiting logging needs finalization or
the supported missing-note repair, never repeated input or another verify.

For maps, load `docs/veda-map-play.md` before the first map input. It covers
bounded directional inspection, reviewed overlapping views, route comparisons,
expected-boss preparation, compact node selection and room-entry results.
Count the focused node among the selectable siblings; reticle marks are not
path connections. Preserve uninspected inventory categories with
`inventory-discover`. Survey the boss and future branches when controls permit;
incomplete future coverage remains explicit and does not block an otherwise
checked immediate move. Re-plan after HP, deck, potion or gold changes.

Read `docs/veda-general-decisions.md` and `docs/veda-action-evidence.md`. Keep three cases separate:

- **Supported game uncertainty:** hidden draws, event outcomes and readable nonattack categories may remain unknown when the existing checked contract covers the action. Use applicable reviewed bounds or complete alternatives; record the actual result afterward. Bare unknown intent does not prove zero attack.
- **Uncertainty unrelated to a candidate:** retain it as unknown. An unread neighboring map icon or a difference in tactical preference does not itself invalidate another candidate whose full adapter review passes. This grants no exemption from required inventory, roster, source or control checks.
- **Missing required evidence:** name the fact and dependent action. Inspect the smallest relevant view, or exclude that candidate and assess a supported alternative. An inspection requiring a button must itself pass the adapter. If no supported inspection or action is available, pause with the specific blocker.

Compare the checked candidates using their applicable conservative damage bounds. Prefer avoiding death, then lower damage risk over the supported horizon, accounting for useful potion effects and future setup. Use expected loss only when defensible probabilities exist; never invent a zero-damage forecast or probability for an unknown. If a rejected candidate cannot be checked, look for a supported protective action or inspection. A system that cannot check any legal next step has a specific capability gap; record its exact rejected requirement for Builder, rather than treating "current state uncertain" as the stop reason or asking the player to solve strategy.

Before dispatch, an expired frame calls for a fresh capture, inspection and newly checked request; never change an old timestamp. Bound repeated unsuccessful capture/inspection attempts to two for the same unchanged blocker; then re-plan among supported candidates instead of looping or automatically ending play. A new timestamp alone does not reset that inspection budget. After an input, do not send another gameplay input until the adapter establishes resolution and clears its pending action. Reconciliation that records an unknown result preserves the blocker. Do not reinterpret an unmatched outcome as permission for another input.

Pause immediately for user stop, loss of video/game identity/control, bridge fault, unresolved input delivery, or inability to persist a consequential action. Preserve pending input, close the owned session and verify cleanup. Report the exact missing fact or failed check, affected action, inspection attempted, alternatives considered and last verified state. If an action is pending, state whether the adapter actually cleared it; never describe completion of reconciliation alone as readiness to resume. Save the evidence needed for Builder; do not edit shared product code during play. Stop wording and a private evidence template are in `docs/veda-action-evidence.md`.

## Boundaries

- Do not modify the Spire skill or silently redefine its advisory-only role.
- Do not install packages, alter system permissions, or pair/connect hardware without a separate explicit request.
- Do not claim hidden state was observed. Use confirmed telemetry only where applicable; incomplete history blocks dependent actions, while every candidate still needs the complete review required by the adapter.
- Do not publish, commit, or change the website as part of a game run.
- Do not launch or send controller input during skill creation, preflight-only use, or any turn where the user has not armed this run.
