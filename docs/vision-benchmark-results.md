# Vision benchmark results — initial local selection

Date: 2026-09-02

Candidates were evaluated locally through the same `StructuredGameState`
contract against three VEDA-captured, reviewed screenshots: title, Ironclad
selection, and a live Floor 1 combat.

| Model | Scored fields correct | Field accuracy | Mean latency |
| --- | ---: | ---: | ---: |
| `blaifa/InternVL3_5:4B` | 13 / 13 | 100% | 7.33 s |
| `qwen3-vl:4b` | 0 / 5 | 0% | 4.84 s |

InternVL3.5 recognized all three frames and correctly extracted the labeled
character-selection values and live-combat Floor 1, HP (67/80), energy (3),
gold (199), and four visible cards. Qwen3-VL failed closed as `UNKNOWN` on the
two initial frames because the current Ollama runtime produced hidden reasoning
without contract JSON.

The combat benchmark intentionally does **not** score player block, enemy names,
enemy intents, or Ascension. In its raw combat response, InternVL3.5 incorrectly
reported block as 67 and Ascension as 1. Those fields remain untrusted; VEDA
must not select a combat action until a state passes the explicit action-readiness
gate and the missing combat fields have been calibrated on more labeled frames.

**Selection:** InternVL3.5 4B is VEDA's provisional local default. It must be
re-evaluated after real map, combat, reward, shop, rest-site, event, treasure,
and boss-relic screenshots are labeled. It is not yet trusted to authorize a
game action by itself.
