# Reviewed choice execution

`veda.choice_execution` plans and verifies one atomic controller tap for a reviewed choice. It does not send input, capture a screen, recognize the UI, open SQLite or grant controller authority. The caller owns per-run arming, source-byte verification, a durable pending-action journal and before/after evidence retention. An uncertain or failed step stays unresolved and must not be replayed automatically.

Supported choice types are card selection, potion slots/menus/targets, map, rewards, rest, event, shop, and the title screen's exact **Continue** option for the same run. Starting a game from a title/character menu, abandon, profile changes and arbitrary buttons have no supported path. Registering an already-open authorized attempt is a separate telemetry operation; see [Neow start and Talk](veda-neow-start.md). A supported type does not imply its current screen, controller mapping, strategy or outcome has been validated.

## API

- `plan_choice_step(observation, choice, now=..., max_age_seconds=5, action_id=None)` returns one `tap` command with one button and its source-bound expectation. It emits a focus step, a selection step, or a commit.
- `validate_choice_proposal(proposal, before, now=...)` repeats source, review, cost and freshness checks immediately before the caller sends. A modified proposal or observation is rejected.
- `verify_choice_step(proposal, before, after, now=...)` checks a later distinct source and its observed semantics. It returns `step_verified`, `choice_complete`, and `matched_outcome_id` (a derived branch ID, or null for an exact outcome or navigation). These do not authorize the next input. The next step needs fresh review.

All three raise `ChoiceError` on incomplete, stale, conflicting or unsupported evidence. Returned `runtime_authorized` and `controller_authorized` fields remain false. The proposal digest detects accidental mutation; it is not an authentication signature. The caller must retain the original proposal and prevent reused request IDs.

## Observation contract

The observation is bounded JSON with `schema: "veda.choice-observation.v1"`:

| Field | Required meaning |
|---|---|
| `frame` | `frame_id`, lowercase SHA-256 `image_sha256`, timezone-aware `observed_at`. The calling adapter verifies the actual image bytes. |
| `review` | `kind: "reviewed_choice_ui"`, named `reviewer`, `complete: true`, and that exact frame ID/hash. Completeness is an explicit reviewed declaration, not automatic recognition. |
| `context` | Exactly `run_id`, `floor_id`, `combat_id`, `turn_id`. Run/floor are named; combat/turn may explicitly be null outside combat. |
| `inventory_digest` | Reviewed semantic inventory SHA-256, supplied and verified by the adapter. Relevant categories must normally be complete. Title Continue alone may carry explicitly unknown inventory until the resumed game is inspected. It is not a screenshot digest. |
| `resources` | All choice-relevant numeric resources, such as HP/gold, with known nonnegative integer values. Missing costs/resources are not zero. |
| `facts` | Source-reviewed gameplay facts needed for this choice and invariants that must survive navigation. Unknown outcomes can be recorded explicitly, but cannot satisfy an exact known-value condition. |
| `ui` | The complete reviewed option and navigation contract below. |

`ui` contains `screen`, `phase` (`choose`, `confirm`, or `result`), stable `choice_id` and `layout_id`, `options`, matching `order`, `focused_id`, `selection_mode` (`immediate` or `toggle`), exact `required_count`, `selected_ids`, `pending_ids`, and `navigation`. These phases describe the choice flow, not the combat executor's `hand`/`targeting` phases.

Each option has an exact stable `id`, visible `label`, boolean `enabled`, explicit `costs` (an empty object means reviewed zero cost), and an `activate` binding when it can be activated. Duplicates need distinct IDs. Visible shortcuts such as Skip may supply `shortcut` instead. Active choices need a positive quota; completed result screens may have zero options/quota. Cropped or paginated grids cannot be declared complete merely because the desired card is visible.

The planner follows only supplied directional edges `{from, to, button, evidence}`. Each edge has one unambiguous destination per source/button. It does not infer controller order from spatial order, wrap at an edge, or assume Cross confirms every menu. All activation/confirmation buttons are a single PlayStation face button; navigation is a single directional button.

Every binding's `evidence` names a reviewer, its current `layout_id`, and exact `meaning`: `focus:A->B`, `activate:OPTION_ID`, or `confirm:CHOICE_ID`. It uses either:

- `kind: "visible_hint"` with current frame ID/hash and `hint_text`; or
- `kind: "reviewed_transition"` with a reviewed `reference_id` and distinct `before_sha256`/`after_sha256` source identities.

These references must be inspected and retained by the reviewed adapter. Merely constructing this JSON is not evidence that the mapping works. The module reads no referenced files. A historical screenshot alone cannot prove an unseen input or authorize a current mapping.

The additional binding kind is `documented_control_profile`. It includes the exact
Neow opening Talk rule: explicitly selected `ps5-default-cross-confirm-v1`,
rule `neow-opening-talk-confirm-v1`, current reviewed source, one free focused
Talk at Neow's act 1/floor 0 introduction. It uses the named default Cross
mapping and makes no visible-hint or hardware-transition claim. Generic events,
reward selection, paid options and custom/unknown mappings cannot use that Talk rule. The
same run/floor/resources/inventory must survive, and verification must show
actual dialogue/options advancement. `veda.neow_start` builds these ordinary
choice contracts from compact reviews; the adapter retains all normal gates.

Separate rules cover reviewed default-layout event options and card-upgrade
grids; see [menu controls](veda-menu-controls.md). Their explicit menu-family
and geometric checks supply bounded mappings without requiring an on-screen
glyph for every direction. Upgrade confirmation uses the actual newly visible
hint. Profile rules cannot impersonate historical transitions or invent costs,
cards or outcomes. All ordinary action and mutation checks remain in force.

## Planned choice and outcome

The choice names its `kind`, matching `choice_id`, exact ordered `option_ids`, and a source-bound `review` of kind `reviewed_choice`. Its selection count must equal the visible quota. Existing selected IDs must be the planned prefix; the planner never silently deselects an unexpected item. A confirmation requires exact selected and pending IDs plus a reviewed `ui.confirm` binding. Immediate actions select exactly one option.

`postconditions` declares:

- Exact resulting `screen`, `phase` and complete `context` (the run cannot change).
- A constraint for every `resources` field: an exact integer or `{min, max}`. Paid resources must have an exact debit equal to the sum of the selected options' known costs. All choices must be affordable before input.
- `inventory_digest`: `"unchanged"` or an exact reviewed resulting inventory digest. Unknown inventory mutations are not silently permitted.
- Exact `facts` plus `allow_changed_facts`, a list of specifically named fields whose new values cannot be predicted. All other existing facts must remain identical, and unlisted new fields are rejected.

This permits map entry into combat without inventing the new hand or enemy roll: screen/floor/resources/inventory remain constrained, while specifically named new-hand/roster facts are inspected afterward. It does not make those unknowns sufficient for a subsequent combat decision.

For `map`, `event`, and `reward` choices, `postconditions` may instead contain
exactly `alternatives`, a list of two to eight `{id, postconditions}` objects.
Each ID must be distinct, and each branch must contain the complete ordinary
contract above. Duplicate contracts and nested alternatives are rejected.
The original single-contract form remains valid for every supported choice.
Neow's named Talk control rule retains its exact contract and cannot use
alternatives to authorize additional effects.

For example, this synthetic structure permits two different revealed menus
without claiming which one will appear or collecting any reward:

```json
{
  "postconditions": {
    "alternatives": [
      {
        "id": "follow-up-dialogue",
        "postconditions": {
          "screen": "event",
          "phase": "choose",
          "context": {"run_id": "synthetic-run", "floor_id": "synthetic-floor", "combat_id": null, "turn_id": null},
          "resources": {"hp": 35, "gold": 99},
          "inventory_digest": "unchanged",
          "facts": {"event_stage": "follow-up"},
          "allow_changed_facts": ["dialogue_text"]
        }
      },
      {
        "id": "reward-menu",
        "postconditions": {
          "screen": "reward",
          "phase": "choose",
          "context": {"run_id": "synthetic-run", "floor_id": "synthetic-floor", "combat_id": null, "turn_id": null},
          "resources": {"hp": 35, "gold": 99},
          "inventory_digest": "unchanged",
          "facts": {"event_stage": "reward-revealed"},
          "allow_changed_facts": ["dialogue_text"]
        }
      }
    ]
  }
}
```

The caller must obtain every branch from applicable reviewed rules and include
every resource field present in its actual observation. Every branch must keep
the same run and satisfy the exact debit for each paid resource. A branch can
use a resource range already supported by the ordinary contract, but ranges
must not replace exact paid debits. Finite fact values can be represented by
separate exact branches; `allow_changed_facts` remains an explicit allowance
for named unpredictable values, not a finite-value constraint.
Exact and unchanged facts preserve JSON types, including nested values:
an observed `true` cannot satisfy an expected integer `1`.

Verification matches the complete observed result against every branch. Exactly
one must match. It never combines one branch's screen with another branch's
resources or inventory, and a caller-supplied branch name cannot select the
result. No match or multiple matches leaves the input unresolved. The adapter
then applies the usual lifecycle and inventory-evidence checks before saving
the derived ID as `choice_outcome_id` in the observed outcome. Recovery can
repeat an idempotent telemetry write, never the controller input.

Every commit also needs `after.review.outcome` containing the `action_id`, `before_frame_id`, `before_sha256`, original `choice_id`, exact `option_ids`, and a named `observed_result`. The outer review binds the after-frame. A bridge acknowledgement, changed image, renamed choice ID, different review metadata or highlighted card alone is insufficient. The named review is retained as reviewed evidence, never presented as automated recognition.

Headbutt/Warcry return selection should constrain the observed selected card's destination and preserved surrounding zone facts. Potion slot opening and Drink are separate reviewed steps; drinking Explosive Potion must also match its slot/inventory change and reviewed enemy outcome. A shop purchase checks its current price, exact gold debit and resulting inventory. A random result that matches one complete declared branch can be verified after inspection. Results outside all declared branches, overlapping branches, and unknown inventory mutations still require explicit reconciliation. Alternatives do not supply missing menu controls, recognize pixels, predict hidden rewards, or weaken the freshness and pending-action checks.

## Evidence and limits

Archived game pixels inspected during implementation show a potion Drink/Discard menu, a clipped Headbutt grid, a card reward with **Circle Skip**, a rest menu, and a shop with **Circle Leave**. They support distinct UI contracts and the need for current hints, complete grids and costs. They do not validate all navigation edges or complete end-to-end hardware flows. Private source/hash notes are in `artifacts/play-readiness-20260927/choice-source-review.json`.

Synthetic tests cover focus routing, no invented wrap, selection counts/order, confirmation, source freshness/mutation, changed context/resources/inventory, after-review correlation, unknown map outcomes, Headbutt/Warcry facts, potion stages, paid shop choices, visible shortcuts and title Continue restrictions. Alternative-outcome tests cover finite fact values, complete branch matching, rejected mixed or overlapping results, exact costs in every branch, unchanged Neow scope, and temporary SQLite persistence/recovery with a fake controller. Hardware, live recognition and full-run readiness require separate evidence.
