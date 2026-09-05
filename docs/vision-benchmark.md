# Vision benchmark

The model selection benchmark uses only VEDA-captured, labeled screenshots.
Labels contain facts visible in the image; fields not labeled are not scored.

Each candidate must use the same `StructuredGameState` contract. The report
measures exact field extraction accuracy, response latency, and the individual
mismatches. A model is not selected on title recognition alone.

Required screen coverage before choosing a default model:

- title and character selection;
- Neow and map;
- at least three combats with cards, energy, HP, Block, enemies, and intents;
- card reward, shop, rest site, event, treasure, and boss relic.

## Calibration sequence

VEDA is currently in the combat-calibration stage. Build the benchmark in this
order, using only real screenshots and labels that a reviewer can visibly
verify:

1. Three distinct early combats, including one with a clearly readable attack
   intent and one with a clearly readable defensive/buff intent.
2. For each combat, label player HP, energy, Block when displayed, each enemy's
   HP and intent, the visible hand, and End Turn.
3. Capture a card reward, map, rest site, shop, and event before allowing map
   or reward decisions.
4. Re-run every candidate against the expanded fixture. Keep results by screen
   type, not just one aggregate score.

### Authorization rule

Schema validity and a high score on unrelated frames are not permission to
control the game. A combat action requires a fresh observation that passes
`combat_action_readiness`: confirmed combat, confidence at least 0.85, player
HP and energy, a visible hand, visible enemy HP, and non-unknown enemy intents.

## Combat-verification extension

Before VEDA can use local vision as the source of a combat recommendation, the
benchmark must additionally label and score: player Block, each enemy's Block,
each intent's **total** damage (not a single hit in a multi-hit intent), and
the total visible end-of-turn status damage. The provider must output each
field with confidence. Missing or below-threshold fields are an observation
failure, not an invitation for the reasoning layer to guess.
Until then, VEDA stays watch-only and records the gap as a calibration need.

Run locally:

```zsh
python3 scripts/benchmark_vision.py qwen3-vl:4b blaifa/InternVL3_5:4B
```

The baseline fixture is [data/vision_benchmark.json](../data/vision_benchmark.json). Add only
real VEDA screenshot paths and reviewed labels to it.
