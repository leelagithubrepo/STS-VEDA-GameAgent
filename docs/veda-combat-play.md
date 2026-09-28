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

Then capture a new game-window image and inspect it. Confirm **all** draft facts
and UI still match. If something changed, revise/validate before capturing again.

```sh
python3 scripts/veda_combat.py --draft /absolute/combat-draft.json \
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
the result image and bind it while fresh; the helper also validates internally.
If result repair consumed the window, finish validation before a replacement
capture. Do not take two result images just to follow a template:

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

For a new turn, use the actual after-state and `next_turn:{turn_number:N}`;
the helper derives the turn transition and provisional ID, then SQLite returns
canonical IDs. Never invent draws or intents to complete a turn record.

For a reward, defeat, or supported card-selection boundary, replace normal
state/UI fields with `boundary:{screen,resources,facts,choice_id,layout_id,options,focused_id}`.
Keep `schema`, `action_id`, `inventory` and `observed_result`. A combat end needs
actual `facts.combat_outcome` (`win` or `loss`); a card selection needs actual
`facts.selection_cause_card_id`. The helper produces result-only UI and existing
lifecycle records. Review the next menu separately before acting on it.

## Clear an enemy tooltip

Tooltip dismissal is an inspection, not a card or End Turn. It can preserve an
unread enemy effect as unknown while exposing the hand. Use the
[synthetic tooltip draft](examples/tooltip-draft.json), replacing its IDs and
observations. Require an actually visible tooltip, its subject, no selected
card or target, actual player resources and explicit unknowns. Do not claim
card focus while the enemy tooltip is selected.

The example declares deck coverage unknown; visible hand cards do not establish
the full deck. Use actual confirmed inventory when available and preserve its
coverage. Add `decision_policy:"learning"` to the inspection draft to retain
unknown relic/potion coverage too; strict inspection keeps its earlier
completeness requirement.

Use `veda_inspect.py` with the same draft/validate then exact-capture binding
sequence above, adding `--control-profile ps5-default-cross-confirm-v1`.
Its only command is the existing default-profile **Up** tooltip-clear rule;
the profile is not proof of a successful live transition or remapped controls.

The result is `veda.combat-inspection-result.v1` with the pending `action_id`,
actual `ui` and `observed_result`. For a cleared tooltip, UI contains
`screen:"combat"`, `phase:"hand"`, `tooltip_visible:false`, actual
`focused_card_id` or null, and null `selected_card_id`, `focused_target_id`,
`selected_target_id`, `tooltip_subject_id`. The helper preserves all prior
facts, resources, inventory and unknowns; reviewing that invariant is part of
the result declaration. Newly revealed facts belong in the **next** combat
draft, not a claim that dismissal changed the enemy.

Use `--result ... --session ... --validate`, then a fresh inspected image and
`--reviewed --output ...` as above. No result uses `--execute`. One attempted
clear consumes the unchanged-state budget, including after restart. If it did
not visibly clear, preserve the pending action and diagnose the actual result;
never send Up repeatedly or substitute Cross/End Turn.
