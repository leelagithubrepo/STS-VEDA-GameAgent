# VEDA

VEDA is an autonomous game-playing agent designed to learn a game before and while playing it. Its first target is *Slay the Spire*.

VEDA is not given a fixed winning script. She gathers attributed research, distinguishes rules from advice, studies decision examples, then learns from her own observed play.

Read the live project report: [VEDA — Slay the Spire Learning Agent](https://leelagithubrepo.github.io/STS-VEDA-GameAgent/).

## Design

```text
research → attributed knowledge → state-aware retrieval
                                      ↓
observe → understand → legal choices → reason → decide → act → verify
                                      ↓                                  ↓
                         structured decision record ← experience store ← learn
```

The package is intentionally game-agnostic at its core. A game adapter supplies observation, legal-action discovery, action execution, and state comparison. `SlayTheSpireAdapter` is the initial contract; a PS5 implementation can be added without coupling game-control code to learning logic.

## Decision safeguards

VEDA now has seven explicit pre-action safeguards: a combat arithmetic validator,
before/after state verifier, confirmed run ledger, map/boss confidence gate, and
source-backed Act 1 threat packs, a readable boss-name identity gate, and an
end-turn survival forecast. The survival forecast includes confirmed multi-hit
damage and visible end-of-turn status damage such as Burn. These safeguards
constrain recommendations and preserve unknowns; they are not a fixed strategy
policy.

`veda.preflight` joins those safeguards into the mandatory recommendation gate:
no action recommendation is valid without a fresh state, explicit target,
energy-legal sequence, predicted result, and later state verification. When
damage values are supplied, a lethal projected End Turn is rejected.

VEDA also separates a **map choice** from the **encounter actually entered**.
Encounter-specific advice requires those two facts to agree, preventing a normal
fight from being treated as an Elite. Confirmed Act 1 profiles constrain claims
such as Split: Acid Slime (M) cannot split; only confirmed large slimes receive
split-aware advice. Every completed human-guided recommendation now keeps its
structured prediction alongside a comparison with the observed outcome.

`veda.routine_combat` is the first VEDA-owned planning boundary. After local
vision authorization, it enumerates only fully modeled direct-card sequences,
rejects unverified/lethal lines, and chooses among them deterministically.
Unknown cards, unconfirmed statuses, and special encounters such as Gremlin Nob
explicitly escalate to human-guided, LLM-audited play.

## Human-guided autonomy transition

While a person provides physical PS5 inputs, `veda.human_guided` runs the same
one-step observe → preflight → act → verify loop and stores the result as
experience. See [human-guided-operation.md](docs/human-guided-operation.md).

## Evidence-first decision support

Every human-guided combat recommendation now receives a structured decision
brief: facts verified on the current frame, values that remain unknown, the
immediate threat, and confirmed ledger context. The primary line must pass
preflight; an optional safer line is independently preflight-checked. The
following observation records the forecast comparison and the decision brief
as auditable experience. See [decision-support-protocol.md](docs/decision-support-protocol.md).

## Quick start

```bash
python -m unittest discover -s tests -v
```

See [docs/founding-intent.md](docs/founding-intent.md) for the project charter and [docs/architecture.md](docs/architecture.md) for the operational design.

## PS5 bridge

VEDA has her own isolated Remote Play bridge; its setup and safety policy are in [docs/veda-ps5-bridge.md](docs/veda-ps5-bridge.md).

With the PS5 feed visible in QuickTime, capture a watch-only observation:

```bash
python3 scripts/capture_observation.py
```

VEDA currently interprets screenshots using a local model only; see [docs/local-vision.md](docs/local-vision.md). No OpenAI API key is required.

## Current operating boundary

InternVL3.5 remains **observation-only** in combat. Local vision must earn
separate authorization on at least 12 real QuickTime frames for the combat
fields (HP, Block, energy, hand, enemies, intent totals, and end-turn damage)
and map-node fields. The current empty calibration manifest is
[`data/quicktime_vision_calibration.json`](data/quicktime_vision_calibration.json).
No OpenAI API key is required.

## Retrospect after each stage

After an Act, boss, or any meaningful floor sequence, create a review artifact
before changing VEDA's strategy. Proposed lessons are intentionally **not**
promoted to knowledge automatically: a Codex review must connect a lesson to
evidence, an implementation, and a regression test.

```zsh
./scripts/veda_retro.py --stage "Act 2" --outcome completed \
  --start-hp 72 --end-hp 41 \
  --note "Slime Boss kill was secured with Feed." \
  --lesson "Preserve Feed for safe lethal when max HP matters."
```

The local report is saved to `artifacts/retrospectives/`. In Terminal VEDA,
ask: `Review the newest stage retrospective. Validate only evidence-backed
lessons, then implement each validation with a regression test.`

For the standalone VEDA handoff, run this immediately after the retrospective:

```zsh
./scripts/veda_handoff.py --latest
```

It writes one short packet to `artifacts/handoff/latest-review.json`. The
handoff loop is deliberately closed before VEDA begins its next stage:

1. VEDA writes the retrospective and runs `./scripts/veda_handoff.py --latest`.
   This updates the screenshot-backed HTML handoff dashboard.
2. Codex reviews the telemetry and publishes a recommendation with
   `./scripts/veda_review_feedback.py --feedback "..."`.
3. VEDA reads the feedback, applies an evidence-backed change when warranted,
   then confirms it with `./scripts/veda_acknowledge_feedback.py --status implemented --summary "..."`.

The HTML entry moves from `awaiting_codex_review` to `feedback_sent_to_veda`,
then to `handoff_complete`. A proposed lesson is still not promoted to durable
strategy without supporting evidence and a regression test.

## Log each completed floor to HTML

Choose a screenshot from `artifacts/veda-inbox` (or any local image), then log
the completed floor. The command stores structured telemetry in
`data/floor_runs.json`, copies the evidence image into the site assets, and
regenerates the player-facing [`docs/report-card.html`](docs/report-card.html).

```zsh
./scripts/veda_floor_log.py --act 2 --floor 18 --outcome victory \
  --screenshot artifacts/veda-inbox/floor-18.png \
  --hp 72 --max-hp 90 --gold 204 --ascension 0 \
  --trophy "Elite defeated" --loss "HP fell below 15" \
  --action "Used Cleave for confirmed lethal" \
  --strategy "Hold a safe line until lethal is verified." \
  --note "Human executed VEDA's advisory line."
```

## Standalone review mailbox

Before each meaningful decision, standalone VEDA reads and acknowledges any
unread LLM feedback:

```zsh
./scripts/veda_inbox.py
```

VEDA submits review requests through `veda_handoff.py`; the reviewer sends a
durable response through `veda_review_feedback.py`. Receipt is explicit rather
than assumed from a separate terminal conversation.

## Build a relic inventory from evidence

When the relic bar is hard to read, VEDA must not guess. Record the exact
relic name and property from a tooltip, reward screen, or trusted manual
reference. The inventory persists the source, confidence, Act/floor, and an
optional evidence screenshot, then refreshes the Report Card.

```zsh
./scripts/veda_relic_inventory.py \
  --name "Burning Blood" \
  --property "At the end of combat, heal 6 HP." \
  --source "visible relic tooltip" --confidence 1 \
  --act 1 --floor 0 --screenshot artifacts/veda-inbox/burning-blood.png
```

## Run VEDA outside VS Code

VEDA can run as a standalone **watch-only** macOS process. It captures the
visible QuickTime PS5 feed, writes a timestamped image, and publishes the most
recent observation to `artifacts/standalone-status.json`. It does not open the
Remote Play bridge or send controller input.

```zsh
./scripts/veda_watch --once       # one passive capture
./scripts/veda_watch --interval 20 # keep watching until Ctrl-C
```

To start this observer automatically when you sign in to the Mac:

```zsh
./scripts/manage_veda_watcher.sh install
./scripts/manage_veda_watcher.sh status
```

Stop it with `stop`, or remove it completely with `uninstall`. The service is
deliberately observation-only. An independent decision service requires a
separate vision/agent approval gate; it is not silently enabled by this setup.
