# Local vision

VEDA's screenshot interpreter is provider-neutral. Every provider must return
the `StructuredGameState` contract in `veda.vision`:

- screen category and confidence;
- selected UI item and visibly offered actions;
- character, Ascension, act, floor, HP, energy, Block, and gold when visibly readable;
- visible hand cards; and
- enemies with HP and intent where visible.

Unknown values are `null`; unknown screens are `UNKNOWN`. A provider must not
guess hidden values merely to populate a field. The agent treats low-confidence
or unknown state as a reason not to act.

## Current provider

`LocalOllamaVisionProvider` uses the provisional local default
`blaifa/InternVL3_5:4B` via `http://127.0.0.1:11434`.
Frames remain on this Mac. It uses no API key and makes no controller call.

```zsh
python3 scripts/capture_observation.py
python3 scripts/interpret_observation.py artifacts/observations/<frame>.png
```

The interpreter is currently validated on the title screen. Combat, rewards,
map, shops, rest sites, and events need a labeled observation set and
screen-by-screen validation before VEDA is permitted to act.

Initial comparison evidence is in [vision-benchmark-results.md](vision-benchmark-results.md).

## Future comparison

Another vision provider can implement `VisionProvider.observe(image_path)` and
must return the same `StructuredGameState`. Comparison evaluates schema
validity, field accuracy, confidence calibration, latency, and false-action
risk. No provider is part of VEDA's required architecture.
