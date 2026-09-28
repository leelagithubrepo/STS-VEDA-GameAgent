# Quick combat loot

Inspect the reward screen once and describe its visible rows in a
`veda.loot-snapshot.v1`. Use the same context, inventory, resources and facts as
the compact menu draft. Set `facts.reward_source:"combat"` and use
`ui.menu_family:"loot_rewards"` for loot rows or `"loot_cards"` for card offers.
Keep actual focus, option IDs and visible grid geometry; do not assume focus
remains in the same position after a row disappears.

[The synthetic example](examples/loot-snapshot.json) is schema guidance only.
It is not the current run. Row `role` describes gold, potion, card_reward, relic
or proceed. A card offer has role card, and its skip control has role skip.
Gold has `reward:{amount:N}`; items have `reward:{name:"Actual item name"}`.
Record visible costs. On each applicable option, supply an actual `activate_hint:{button,hint_text}` when
needed; otherwise the scoped default profile selects the focused row with Cross
and navigates adjacent observed grid cells. These default controls still need
live outcome verification; tests are not evidence of this console's layout.

```sh
python3 scripts/veda_loot.py --snapshot /absolute/loot.json
```

Routine decisions require no model strategy call:

- Collect free ordinary gold and verify the total changed.
- Collect a potion into a confirmed empty slot. A full or unknown belt needs
  inspection/replacement strategy; never automatically discard another potion.
- Open the card reward to inspect its actual offers.
- Proceed when no enabled reward remains.

The helper returns `strategy_required` for cards and other real tradeoffs. This
is a request for Orchestrator's own decision, not a player approval or a reason
to stop. Choose a card or skip based on the deck once. Save
`{option_id,reason,decision_key}` using the returned key; use `--decision` to
reuse that choice across focus taps. The helper invalidates it if the offered
rewards, inventory, resources or run/floor change. A focus change alone does
not require another strategic analysis. Relics with tradeoffs and potion
replacement stay in the normal strategic menu flow.

To bind an inspected settled image and prepare one input:

```sh
python3 scripts/veda_loot.py --snapshot /absolute/loot.json \
  --session /absolute/SESSION_DIRECTORY/state.json \
  --capture /absolute/INSPECTED.png --reviewer 'Codex Orchestrator' \
  --evidence-note 'Actual inspected reward rows, focus and gold total.' \
  --reviewed --execute --output /absolute/new-loot-request.json
```

Pass `--decision /absolute/chosen-card.json` for the already-selected card or
skip. Submit the returned file pointer unchanged. This helper never sends input
itself. It uses the ordinary armed adapter, one observed input at a time. Reuse
the choice while navigating; do not retrieve boss rules or reconsider the deck
just to move focus or collect gold.

Verify with the ordinary `veda_menu.py --result` helper. Supply the actual gold
total, inventory, remaining options and focus. Loot inventory changes are
recorded from the reviewed actual inventory, never merely from the predicted
reward. A highlight is navigation, not collection. Complete the result record
then continue to the next reward. Timing improvements still require live
measurement; offline tests do not establish a seconds-per-floor claim.
