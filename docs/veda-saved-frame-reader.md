# Reading a saved Slay the Spire image

The saved-frame reader extracts partial HP, energy, card, and combat-number evidence from an
archived PNG. It uses macOS text recognition and image pixels without a language
model. It does not capture the screen, send controller input, update a run in
SQLite, or grant permission to play. It is separate from the execution runtime.

The current implementation needs an inspected game viewport. It automatically
reads a dedicated energy-display crop, then selects card-header regions for
another OCR pass. A separate combat batch reads player block, enemy health, and
attack numerals. The energy crop uses a fixed viewport-relative region, rounded
outward to whole pixels. It runs even when the initial energy reading is present
or no card candidates were found. Unknown green titles may also
receive a contrast-filtered title pass, and separate small crops inspect displayed
cost symbols even when the title is unreadable. A wider hand crop looks for
additional exact titles. All these crops share the same
card-region native invocation; that batch is limited to 20 regions. One slot is
reserved for the wider hand crop, including when the first pass found no card
candidates. The other slots serve headers, contrast titles, and then costs.
Omitted header, title, and cost regions are reported; reserved discovery can
therefore reduce the number of individual refinements in a crowded image.
Card discovery is experimental: titles can be missed
or misread, descriptions can become false candidates, and enlarged cards can
duplicate or obscure hand cards. A successful
command is not a complete or actionable game-state reading.

## Setup and use

Use Python 3.11 or newer. Card and combat pixel analysis also need Pillow (the
project's optional `vision` dependency). Without Pillow, HP and energy can still
be read; unavailable pixel analyses are reported. No dependencies or
helpers are installed or built automatically.

On macOS with Apple's command-line developer tools, explicitly build the native
helper from the repository root. Rebuild it after native reader changes:

```sh
mkdir -p artifacts/native-ocr/module-cache
xcrun swiftc -module-cache-path artifacts/native-ocr/module-cache \
  scripts/native_ocr.swift -o artifacts/native-ocr/native_ocr
```

Inspect the archived image and identify the game rectangle in original image
pixels. Supply left, top, right, and bottom bounds; right and bottom are
exclusive. The example below applies only to that particular archived image:

```sh
python3 scripts/read_saved_frame.py \
  artifacts/observations/ps5_observation_20260926T135731Z.png \
  --native-helper artifacts/native-ocr/native_ocr \
  --viewport 0 282 2145 1492 \
  --expected-sha256 17942aa48650061198aef5368346acaab741961e9839970364a0ea86c8788e63
```

The command prints JSON. Redirect it to a local file if you want to keep the
evidence. The JSON retains raw OCR from the entire image, including visible
text outside the game rectangle. The viewport limits field extraction; it does
not redact other desktop text from the saved result.

The native process is limited to five seconds and bounded output by
default per invocation. The default reader may invoke it up to four times: once
for the image, once for energy, once for card regions, and once for combat crops.
Card/combat stages need Pillow. Add `--single-pass` to skip all focused OCR stages
when comparing the baseline. Combat and attack pixel checks still run on the
whole-image text in that mode. Rebuild the helper for the new `white_text`
preprocessing mode; an older helper may reject the focused combat request.
macOS Vision may be unavailable inside a restricted sandbox; a
`native_reader_failed:helper_failed` result leaves every derived reading unknown.
The tool does not retry outside the sandbox itself.

## Reading the result

- `ok: true` means offline processing finished. Missing or conflicting fields
  can still be `null`; check top-level `issues`, `coverage`, `hud.errors`, each
  card's `field_issues`, and `cards.issues`. For combat, also inspect
  `combat_evidence.issues`, `combat_evidence.refinement.conflicts`, and
  `combat_evidence.intent_evidence.unconfirmed_attack_candidates`.
- `partial` is always true. `runtime_authorized`, `controller_authorized`, and
  `runtime_authorization_eligible` are always false. This output is not an
  execution-runtime interpreter or calibration report.
- `processed_at` records analysis time. `captured_at` stays unknown: processing
  an old image does not make it a fresh observation.
- `image_sha256`, dimensions, and the caller-supplied viewport identify the
  evidence. Source mismatches clear all derived readings. The viewport is not
  located or verified automatically.
- HP and energy require literal fraction text inside predefined HUD regions.
  Conflicting readings remain unknown. A missing energy orb is not zero energy;
  an observed `0/3` is zero.
- In the default refined mode, energy requires one complete literal fraction as the top OCR
  reading, with confidence at least 0.8. Exact alternatives can veto but cannot
  repair it. Both the whole image and focused crop are parsed under these rules.
  Fragments such as `1` and `/3` are not joined, and no prior turn or expected
  energy value is substituted. Conflicting fractions, duplicate fraction rows,
  or a zero denominator leave both energy components unknown. Energy above its
  displayed denominator is allowed. `--single-pass` uses the older HUD parser:
  it can accept a lower-confidence or alternative fraction and does not apply
  the refinement's top-reading and duplicate-row checks. It is a baseline
  comparison mode, not an equivalent verification policy.
- `energy_refinement` retains both passes, source identity, the actual crop, and
  per-region outcomes/timing. A missing or unavailable crop may retain a valid
  fraction from this same image. An invalid source or response clears all derived
  readings. The additional crop has its own bounded native invocation; it is
  not repeated until a preferred value appears.
- `card_candidates` are potential depictions, sorted by screen position. They
  are not a unique, ordered hand. Raw OCR is retained; unknown names are not
  repaired from the deck or a similar spelling. The display-title catalog is
  separate from the combat-effect calculator and records its local references.
  Matching ignores letter case only; it does not change punctuation or add `+`.
  The wider hand crop adds only exact, color-confirmed titles at distinct
  positions. It cannot resolve a covered card from a nearby popup or certify
  that every card was found.
- Upgrade evidence compares a literal title plus with independently sampled
  title color in the original pixels. Exact variant names and upgrades require
  that evidence; missing color or disagreement leaves them unknown. The contrast
  pass may confirm an upgraded title only when OCR reads a literal `+` and the
  original pixels independently show green lettering. It does not repair a
  spelling from the deck or a similar card name.
- Cost requires a unique visible numeral with nearby cost-orb evidence.
  Conflicting numeric alternatives, conflicting readings, and shared numeral
  pixels remain unknown; a missing numeral is not a standard or zero cost.
  Dedicated cost crops use the original automatic geometry and test the original
  orb pixels independently of title recognition. A conflicting reading remains
  unknown even if a later crop repeats an earlier value. A recognized title does
  not establish its effects.
- `cards.refinement` and each candidate's `refinement` retain region outcomes,
  original candidates, and focused OCR evidence. Region coordinates map back
  to the original image. An unavailable, timed-out, or output-limited focused
  process retains the original partial reading with an explicit issue; it
  cannot certify new information. Source or response-protocol failures clear
  all derived readings. If a
  focused title is unreadable, a previously confirmed identity may remain;
  `refinement.original_candidate` shows the evidence it came from.
- When the native engine estimates a text box beyond a crop's 0.01-pixel numerical
  tolerance, that entire crop
  reports `native_ocr_outside_region` with no observations. Other independently
  verified crops can still contribute. Boxes are never clipped to make a reading
  pass. An unexpected invalid box in a supposedly successful response still
  clears all derived readings.
- The card-cost reader uses that same observation-boundary tolerance. It retains
  raw coordinates without clipping; viewport bounds, requested crop identity and
  cost-symbol checks remain unchanged. This prevents tiny rounding differences
  from rejecting an otherwise valid native response.
- `cost_refinement` records dedicated cost observations, possible numeral boxes,
  and ambiguity reasons. `cards.refinement.hand_discovery_evidence` records the
  wider crop when it was used. Additional depictions are still subject to the
  20-candidate bound and shared-cost checks. A wide crop with no recognized
  titles does not establish an empty hand.
- `combat_evidence` contains tentative enemy-health locations and a nullable
  player-block reading. Health requires an exact confident fraction in the
  enemy area with independently sampled red health-bar pixels. Block requires
  a literal numeral inside a blue/cyan shield region beside a player health row.
  These are bounded visual heuristics, not enemy identities or a certified roster.
  Missing block is unknown, never zero. Conflicting readings remain unknown.
  Numeric intent text alone cannot establish attack icons or a hit count, so
  `incoming_damage` remains null.
- Combat refinement uses fixed block/health crops plus a bounded set of small
  attack-number crops located from original red weapon pixels. A bounded check
  compares those pixels with six saved-image templates: two sword shapes, a
  cleaver, curved-blade variants, and an axe. Template matches retain their source-image hashes
  and pixel bounds; they do not establish enemy identity or companion effects.
  Other shapes can remain unreadable. A separate bounded check
  of neutral-white components can tighten each crop around the numeral row;
  it checks component height, alignment, spacing and crop edges before narrowing.
  Ambiguous geometry keeps the wider crop. This step chooses pixels, never a
  digit value. With no located
  weapon, it uses a fixed wider intent region. Each intent region is read in
  both original and `white_text` form, with at most 20 crops in this batch.
  The primary regions stay present when bounded padded/fixed numeral-row
  alternatives are added; disagreements remain vetoes. Up to six primary
  regions and a total of nine primary/alternative regions fit the same native
  call, alongside the Block and health crops. Omitted alternatives are reported.
  A tested tighter crop that confused 6 and 9 is excluded.
  For `white_text`,
  original pixels with every RGB channel at least 170 and a channel range at most
  45 are rendered black on white before scaling. This separates pale digits from
  colored symbols; the original pixels must still corroborate the weapon shape.
  The conversion never supplies a missing digit or multiplier.
- `combat_evidence.refinement` retains source identity, crop requests and
  per-region outcomes. A failed crop may preserve valid evidence from the same
  image. Conflicting values remain unknown; source/protocol failures clear the
  entire reading. A shield-backed block numeral requires an adjacent horizontal
  health row. Its search starts at the estimated edge of the corroborated shield region so that a
  wider numeral does not clip additional health-bar evidence. Aligned segments
  separated by overlaid text may corroborate that
  row, but unrelated colored patches do not.
- `combat_evidence.intent_evidence` contains individual attack-number candidates.
  Publication requires matching usable readings from original and filtered OCR.
  A reading found in only one mode stays in `unconfirmed_attack_candidates`;
  it does not contribute an attack number or subtotal. This agreement check can
  withhold correct numbers, and agreement is not a guarantee of accuracy.
  The single-pass option cannot establish this two-mode agreement.
  A recognized weapon shape does not identify companion effects, an enemy, or a
  complete roster. `hits` and `attack_total` require an explicit literal multiplier;
  no visible multiplier is not proof of one hit. Any supported attack subtotal
  covers only those explicit-multiplier candidates, never all incoming damage.
  Unknown/obscured numbers and contradictory passes remain unresolved.
- `coverage` explains what was read and what is missing. Its card counts are
  counts of depictions, including repeated names; hand count, membership, order,
  and completeness remain unknown. Popups, menus, unresolved names/costs, and
  omitted refinements appear as issues. `combat_ready` remains false even when
  all visible numerals are read.
- `hand_complete` never becomes true. Enemy identities, intents, statuses,
  piles, relics, potions, UI phase, and controller focus remain unavailable.
  Existing `unavailable_fields` describe complete unsupported capabilities;
  a partial block reading does not make `player_block_and_statuses` complete.

Exit status 0 means processing completed. Status 1 includes processing/source
failures and invalid viewport or expected-hash values in the structured result.
Status 2 means a command-line syntax or option error. These codes are not
confidence scores.

## Validation scope

Fixture tests cover missing/conflicting readings, zero energy, image replacement,
identity mismatches, invalid bounds, subprocess limits, upgrade disagreement,
and repeated/unknown card titles. Real-image evidence remains a small private
offline sample; shared card names or cropped copies do not count as new
independent examples. Broader validation and the missing state fields are still
required before connecting any reader to live execution.
