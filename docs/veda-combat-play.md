# Compact combat actions and results

Use these commands instead of reading adapter source or constructing full
Reading/source/review objects during play. Every value still comes from actual
inspection and confirmed ledger context. [Timing and startup](veda-play-hot-path.md).

## Prepare before capture

The source-free `veda.combat-draft.v1` has one `state`, the four context IDs,
inventory, encounter name/type/confidence, perception confidence, explicit UI,
unknowns, one card/End Turn plan and reasoning. Start from the
[synthetic schema example](examples/combat-draft.json), replacing all game facts
and IDs; its values are not this run. Keep repeated copies as distinct card IDs.
Use actual `enemy`, `elite` or `boss` for encounter type. Optional `rules` and
`boss_manifest` retain their existing reviewed semantics. For the autonomous
learning session add `decision_policy:"learning"`; an omitted policy remains
strict for compatibility. The adapter and draft policies must match.

`plan` contains one `steps` action and may include `potion_review`,
`zero_cost_review`, `setup_reason` and `claims_lethal`. Reviews are bounded
explanations, not extra controller actions. In learning mode, missing rule,
forecast, confidence and strategy coverage produces warnings; unavailable or
unaffordable cards still require a different action. An End Turn with an
unknown survival forecast can proceed without inventing zero damage.

`state.end_turn_damage` means additional **non-attack** damage. Each enemy's
`intent_hits` contains its attack hits; do not also sum those into
`end_turn_damage`. Preserve unmodeled effects and unread values. Do not infer
zero powers from an occluded icon or identify an unread enemy from a guess.
The assessment reports what can be predicted and what remains unknown.
Learning mode does not require resolving every unknown before acting.

```sh
python3 scripts/veda_combat.py --draft /absolute/combat-draft.json --validate
```

Require `draft_valid:true`. The preview names the next atomic input; it is not
controller authority. An unread hand focus requires inspection, not assigning
the first card as focused. Fix validation errors now, before the action image.

Use the inspected settled game image. Confirm **all** draft facts and UI match.
Bind to the session/input epoch; do not recapture solely because time elapsed.
See [evidence continuity](veda-evidence-continuity.md).

```sh
python3 scripts/veda_combat.py --draft /absolute/combat-draft.json \
  --session /absolute/SESSION_DIRECTORY/state.json \
  --capture /absolute/INSPECTED.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Describe the actual inspected facts and limits.' \
  --reviewed --execute --output /absolute/new-action.json
```

Submit the returned `request_file` pointer to the armed adapter. It performs
one ordinary prepare/send cycle. Do not send again. Re-observe after focus,
selection and target confirmation; never batch across them.

## Verify the actual result

Use `veda.combat-result.v1` with the exact pending `action_id`, actual `state`,
inventory, encounter, perception confidence, UI, unknowns and `observed_result`.
See the [synthetic navigation result](examples/combat-navigation-result.json).
`state`, `inventory` and `encounter` may be `"unchanged"` only when actually
reviewed unchanged. A played card needs the entire actual new state, including
its removal, energy, Block, and affected enemies. A highlight alone is not play.

If prior `rules` or `boss_manifest` exist, explicitly supply their actual value
or `"unchanged"`. Do not silently retain mutable counters. Optional
`card_destination:"discard"` or `"exhaust"` creates a card-play zone event only
when actual resolution verifies; assert a destination only if established.

```sh
python3 scripts/veda_combat.py --result /absolute/result-draft.json \
  --session /absolute/SESSION_DIRECTORY/state.json --validate
```

Prepare the result structure before input where possible. After input, inspect
the result image and bind it to the exact pending action. Result preparation
time does not expire an inspected after-image. Do not take two result images just to follow a template:

```sh
python3 scripts/veda_combat.py --result /absolute/result-draft.json \
  --session /absolute/SESSION_DIRECTORY/state.json \
  --capture /absolute/INSPECTED_RESULT.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Describe the observed result and all changed values.' \
  --reviewed --output /absolute/new-result.json
```

Submit its pointer. Require `verified`, then use canonical `next_context`.
`logical_action_complete:false` means focus/selection only; inspect the next UI
and continue the same recommendation if it remains valid. A `recoverable_review` calls for another observation or request repair while
the session stays active. An observed HP/energy/effect difference is recorded
as a forecast mismatch in learning mode; it does not invalidate proven card
resolution. Pending delivery/result uncertainty needs inspection, never replay. A `verified_pending_log` result needs
`finalize`, not replay.

For consecutive focus/selection steps, reuse the just-inspected stable result
image for the next request with `--session` if no input or outside state change
has intervened and all next-draft facts match it. Finalize the pending outcome
first, then bind and submit the next input. This saves a redundant capture; it
does not alter the timestamp or permit a second input without observing the
first. Keep distinct output filenames for each request and result.

For a new turn, use the actual after-state and `next_turn:{turn_number:N}`;
the helper derives the turn transition and provisional ID, then SQLite returns
canonical IDs. Never invent draws or intents to complete a turn record.

For a reward, defeat, or supported card-selection boundary, replace normal
state/UI fields with `boundary:{screen,resources,facts,choice_id,layout_id,options,focused_id}`.
Keep `schema`, `action_id`, `inventory` and `observed_result`. A combat end needs
actual `facts.combat_outcome` (`win` or `loss`); a card selection needs actual
`facts.selection_cause_card_id`. The helper produces result-only UI and existing
lifecycle records. Review the next menu separately before acting on it.

## Combat focus

Record `focus_domain`, `tooltip_kind`, `focused_subject_id` and
`focus_evidence_note` from the actual image. A raised hand card showing its
Block or other keyword explanation stays `phase:"hand"`,
`focus_domain:"hand"`, `tooltip_kind:"card_keyword"`; use its real
`focused_card_id` and ordinary horizontal navigation. The keyword explanation
is not a modal to dismiss. End Turn from ordinary hand focus uses Triangle.

Read the focus markers on the exact current image before carrying forward a
card label. Yellow corner markers around Ironclad mean player inspection:
`phase:"inspect"`, `focus_domain:"player_status"`, `focused_subject_id:"player"`,
and no focused/selected card. The tooltip kind is `none` if no help box is open.
Yellow corners around an enemy mean enemy inspection when no card is selected;
a selected card with a target prompt remains `phase:"targeting"`. The playable glow around
the whole hand and a card's position in the fan do not establish focused-card
identity. In the September 29 failure, the before-image marked Ironclad, but
the request said Disarm; Right then focused Jaw Worm. Treat that as a corrected
observation, not evidence of a Disarm-to-enemy controller mapping.

Player status, relic, potion and enemy inspection use their actual domain and
subject, with no selected card. A recovery input is one observed navigation
attempt; its intended destination is not an established result. The default
Down recovery is exploratory until that transition has been observed on this
console. Do not repeatedly send Up to clear arbitrary help text: the retained
live sequence moved from hand to player status, then relic, then potion.

In a compact combat draft, add `control_profile:"ps5-default-cross-confirm-v1"`
to this nonhand `ui`. Down is the default. Only after its result is verified
and logged unchanged at that same domain and subject may the next fresh draft
set `recovery_direction:"circle"` for one exploratory cancel/back input.
Stay with the same helper while resolving a recovery sequence so its saved
attempt history supplies the next step.

Result drafts describe the actual focus even when it stayed put or moved to a
different inspection area. Such a result can finish logging a navigation
attempt without completing a card or claiming return to the hand. Follow the
returned recovery guidance; each next input needs its own fresh observation.
Actual card completion still requires the observed card effect and hand change.

This also applies when ordinary hand navigation (`card_focus`) unexpectedly
lands on player/enemy/relic/potion inspection or another hand card. In learning
mode, keep using `veda_combat.py --result` for that pending combat input, with
actual explicit focus and inspected unchanged state/inventory. Do not switch
to an inspection-result schema to close a combat request. Submit the generated
verification pointer; it supplies a valid operation UUID and accepts the exact
post-input image without an age-only recapture. The explanation may contain up
to 2048 UTF-8 bytes. A changed focus is not an unknown delivery: log the actual
navigation, then prepare recovery from its actual domain. Never resend the
pending Left/Right merely to repair verification.

## Inspect an enemy tooltip

Focus recovery is an inspection, not a card or End Turn. The
[synthetic inspection draft](examples/tooltip-draft.json) preserves an unread
enemy effect while reviewing the actual focus. Replace all IDs and facts with
observed values. Keep inventory coverage and unknowns explicit; an inspection
does not establish a complete deck or identify an unread effect.

Use `veda_inspect.py` with the same draft/validate and exact-capture binding
sequence, adding `--control-profile ps5-default-cross-confirm-v1`.
Declare `decision_policy:"learning"`. The helper proposes one exploratory Down.
After its result is verified and logged unchanged at the same focus, submit a
newly reviewed inspection draft; the adapter proposes one Circle fallback from
its saved attempt history. Neither proposal asserts that the hand will be reached. The old generic
Up rule is accepted only to reconcile an already attempted legacy input.

The result is `veda.combat-inspection-result.v1` with the pending `action_id`,
actual `ui` and `observed_result`. Include the four focus fields above and the
inspection fields `screen`, `phase`, `tooltip_visible`, `focused_card_id`,
`selected_card_id`, `focused_target_id`, `selected_target_id` and
`tooltip_subject_id`. Nonhand focus remains `phase:"inspect"` and names its
actual subject. A return to hand names the raised card; its keyword help may
still be visible. Do not call that help a blocking dialog.

Use `--result ... --session ... --validate`, then bind a fresh inspected image
and submit its pointer. No result uses `--execute`. The result preserves prior
resources, inventory, facts and unknowns; newly revealed game facts belong in
the next combat draft. Attempts are remembered across captures and restarts,
so repeated no-progress inputs cannot masquerade as successful clears. Record
the actual result before choosing another navigation or gameplay action.
