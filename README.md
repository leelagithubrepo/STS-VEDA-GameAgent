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
