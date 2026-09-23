---
name: orchestrator
description: "Play the currently open Slay the Spire run by using the Spire skill for strategy and VEDA's isolated PlayStation bridge for controller actions. Requires explicit per-run arming, verifies each move from fresh screen evidence, and stops on uncertainty."
---

# Orchestrator

Coordinate strategic advice and game execution as two separate roles:

- **Spire advises.** Read and follow `/Users/leela/.codex/skills/spire/SKILL.md` for game strategy and evidence discipline. Spire itself remains advisory-only and must never send controller inputs.
- **Orchestrator executes.** You alone may translate Spire's one-step recommendation into a controller action through this repository's VEDA-owned bridge, but only after the user explicitly arms this run.

This skill is a supervised, observe–advise–act–verify loop, not an always-on daemon. Invocation starts preflight; it does not by itself authorize the first input. Do not start a new run, select a profile, or continue into another run after defeat or victory.

## Required preflight

Before planning an action, read these project instructions:

1. `docs/veda-ps5-bridge.md`
2. `.veda/team/game-agent-contract.md`
3. `.veda/workflows/spire-telemetry-v4.md`
4. `.veda/backlog/spire-combat-advisory.md`, when present

Then:

1. Confirm the user has explicitly said **“ARM ORCHESTRATOR FOR THIS RUN”** in the current conversation. Until then, capture/inspect and advise only; send no input.
2. Confirm the game is already open at a safe, identifiable screen and that no other Remote Play/controller client is connected. Never change macOS permissions or pair a device on the user's behalf.
3. Check `./scripts/bridge status`. If the bridge, game identity, screen, or current state is uncertain, stop and explain the exact blocker.
4. Start one persistent bridge session with `./scripts/warm_bridge --idle-timeout 0` only after arming. Wait for its `ready` event. Use that single session throughout the run; do not invoke a new bridge session for every button press.

## Decision loop

For each meaningful choice, follow `references/operating-loop.md`. In brief:

1. Capture a fresh screen and read the latest confirmed run/floor/combat ledger. Never infer hidden state from a stale frame.
2. Ask Spire for exactly the next safe recommendation, grounded in that frame and the ledger. If Spire cannot make a legal, sufficiently certain recommendation, do not act.
3. Check that the proposed input corresponds to the visible UI and is the smallest atomic action that advances the recommendation. Then send it through the existing warm bridge.
4. Capture again after the action and verify the expected transition before taking another action. Re-plan after any unexpected transition, draw, enemy turn, potion effect, reward, map/shop change, or other material state change.
5. Record confirmed actions and outcomes using the existing telemetry workflow. Unknown remains unknown; do not fabricate a card zone, item property, outcome, or screenshot.

Never issue a long card-play sequence without re-observing. Do not repeat an input because the screen seems slow; inspect bridge status and capture first. Avoid system-level/controller buttons (PS/Home, pairing, power, settings), desktop navigation, shell commands that control hardware outside the documented bridge, and any action not needed to play the visible game.

Use a fail-closed stop on stale/missing capture, ambiguous screen, unexpected UI, unverified card/relic/potion state, bridge error/disconnect, conflicting advice, or inability to log a consequential move. On stop, close the warm session so its cleanup releases inputs, tell the user what was last verified, and wait. The user may stop the run at any time; honor that immediately.

## Boundaries

- Do not modify the Spire skill or silently redefine its advisory-only role.
- Do not install packages, alter system permissions, or pair/connect hardware without a separate explicit request.
- Do not claim the skill can reason from truly hidden game state. It may use confirmed telemetry to reconstruct state; where history is incomplete, ask to inspect or stop.
- Do not publish, commit, or change the website as part of a game run.
- Do not launch or send controller input during skill creation, preflight-only use, or any turn where the user has not armed this run.
