# VEDA — Slay the Spire 1 Player Manual

## Scope

This knowledge applies to **Slay the Spire 1**, initially:

- Character: Ironclad
- Difficulty: Ascension 0
- Platform: PlayStation 5
- Objective: learn to complete full runs consistently

Do not use Slay the Spire 2 mechanics unless explicitly researching STS2 in a separate knowledge domain.

---

# 1. What the Game Is

Slay the Spire is a turn-based deck-building roguelike.

A run consists of climbing through a sequence of floors.

During the run VEDA must continuously manage:

- HP
- cards/deck quality
- energy
- gold
- potions
- relics
- upgrades
- pathing
- immediate combat survival
- preparation for future encounters

The objective is not to maximize damage on every turn.

The objective is to make a sequence of decisions that maximizes the probability of winning the entire run.

A locally optimal decision may be globally bad.

---

# 2. Basic Run Flow

The normal high-level flow is:

Start Run

→ choose character

→ Neow/start-of-run choice when presented

→ inspect Act map

→ choose reachable node

→ resolve room

→ receive rewards when applicable

→ choose next reachable node

→ continue climbing

→ Rest Site before boss

→ Boss

→ boss rewards

→ next Act

The standard game contains three main Acts.

An optional Act 4 can eventually become available through additional progression requirements.

For initial VEDA development, focus on learning to complete ordinary Ascension 0 runs before optimizing optional Act 4 play.

---

# 3. The Map

Each Act is a branching graph.

VEDA cannot simply choose any visible room.

She may choose only a node connected to her current position.

The map should therefore be represented as a graph rather than a flat collection of icons.

Important room types include:

## Monster / Normal Combat

Fight normal enemies.

Typical rewards include:

- gold
- card reward
- possible potion

Normal fights are the primary mechanism for improving the deck through card rewards.

## Elite

Much harder combat.

Important because winning an Elite gives a relic in addition to the normal types of combat rewards.

Elites are therefore high-risk/high-reward encounters.

Do not treat an Elite as simply another normal enemy.

Before entering one, evaluate:

- current HP
- deck damage
- defensive capability
- potions
- upgrades
- which elites are possible
- what comes immediately afterward
- escape/alternative paths

## Rest Site / Campfire

Common choices:

REST:
heal HP.

SMITH:
upgrade one card.

Other options can become available through relics or later game mechanics.

Resting solves an immediate HP problem.

Upgrading increases future power.

Therefore do not automatically Rest whenever HP is missing.

## Merchant / Shop

Spend gold.

Possible purchases include:

- cards
- relics
- potions
- card removal

Do not spend gold merely because it is available.

Evaluate purchases relative to the deck's current problems and future threats.

## Unknown / ? Room

Can produce an event or sometimes another room type such as combat, shop or treasure.

Treat '?' as uncertain, not as a guaranteed safe event.

## Treasure

Contains a chest and generally provides a relic, sometimes with gold depending on chest.

## Boss

The final major encounter of the Act.

The boss is visible on the map before reaching it.

This matters strategically.

VEDA should know which boss she is preparing for and make earlier deck, upgrade and path decisions with that boss in mind.

---

# 4. Ironclad Starting State

At Ascension 0, Ironclad begins with:

80 maximum HP

Starting deck:

- Strike ×5
- Defend ×4
- Bash ×1

Starting relic:

Burning Blood

Burning Blood heals 6 HP at the end of combat.

This means HP behaves somewhat differently for Ironclad than for characters without comparable sustain.

Small amounts of combat damage can sometimes be acceptable because part of the damage will be recovered after combat.

However:

DO NOT interpret Burning Blood as permission to take unnecessary damage.

HP is a strategic resource.

---

# 5. Combat Loop

Combat is turn based.

General sequence:

START PLAYER TURN

→ refresh/gain Energy

→ draw cards

→ observe enemies and intents

→ evaluate state

→ play zero or more cards

→ possibly use potion

→ End Turn

→ end-of-turn effects resolve

→ enemies act

→ next player turn

Combat continues until all enemies are defeated or the player dies.

At base values, the player normally begins each turn with:

3 Energy

and draws:

5 cards.

Relics, cards, statuses and other effects can change these values.

Never assume 3 Energy or 5 cards when the visible state proves otherwise.

---

# 6. Card Piles

VEDA must understand four important card locations.

## Draw pile

Cards waiting to be drawn.

## Hand

Cards currently available to play.

Normal maximum hand size is 10.

## Discard pile

Cards already used or discarded.

When the draw pile becomes empty and another card must be drawn, the discard pile is shuffled to form a new draw pile.

## Exhaust pile

Cards that have been Exhausted are normally removed from the current combat.

Exhaust is NOT equivalent to ordinary discard.

This distinction is strategically important for Ironclad.

---

# 7. Card Types

## Attacks

Usually deal damage.

Examples:

Strike
Bash

## Skills

Usually provide block, draw, manipulation, debuffs or other utility.

Example:

Defend

## Powers

Usually create persistent effects for the remainder of the combat once played.

## Status cards

Negative or disruptive cards commonly introduced during combat.

## Curse cards

Usually harmful cards that can remain in the permanent deck unless removed or otherwise handled.

VEDA should inspect the actual card text rather than infer behavior purely from card category.

---

# 8. Energy

Cards usually cost Energy.

VEDA must calculate before committing:

current Energy

minus card cost

equals remaining Energy.

Do not plan a sequence whose later cards cannot legally be played.

Card ordering therefore matters.

A valid turn is not merely a collection of good cards.

It is an ordered sequence constrained by available Energy and changing game state.

---

# 9. Block

Block absorbs incoming attack damage before HP is lost.

Example:

Enemy attacks for 12.

Player has 7 Block.

Result:

7 damage absorbed

5 HP lost.

Block normally does not behave like permanent health.

VEDA must understand when Block expires and account for effects that modify ordinary Block behavior.

Enemy Block matters too.

If VEDA predicts:

"Strike deals 6, enemy has 6 HP, therefore lethal"

but the enemy gains 3 Block before the damage resolves, the prediction is wrong.

This is exactly the type of state transition VEDA must learn to model.

---

# 10. Enemy Intent

One of the most important mechanics in Slay the Spire.

Enemies telegraph what they intend to do.

Intent may indicate things such as:

- attack
- attack multiple times
- defend
- buff
- debuff
- inflict status
- sleep
- escape
- other special behavior

Attack intents usually expose expected attack damage.

VEDA should NEVER make an ordinary combat decision without first attempting to determine every living enemy's current intent.

If a critical intent cannot be determined confidently:

DO NOT GUESS.

Re-observe.

Use alternate extraction.

Consult known enemy behavior if appropriate.

If uncertainty remains and the consequence could be significant, require supervision.

---

# 11. Damage Is Not the Same as HP Loss

VEDA must distinguish:

raw attack damage

→ offensive/defensive modifiers

→ Block

→ HP loss.

Effects such as Strength, Weak, Vulnerable and enemy/player Block can alter expected results.

Therefore VEDA should eventually calculate expected combat transitions rather than relying purely on language-model intuition.

---

# 12. Buffs and Debuffs

Important examples include:

## Strength

Changes attack damage.

## Weak

Reduces attack damage dealt.

## Vulnerable

Causes affected targets to receive increased attack damage.

Bash is important partly because it applies Vulnerable.

## Frail

Reduces Block generated from cards.

There are many additional buffs, debuffs and special effects.

VEDA should not treat unfamiliar icons as decorative information.

Unknown status:

→ identify

→ retrieve game knowledge

→ incorporate effect

→ then reason.

---

# 13. Sequencing

Card order can change the result dramatically.

Example concept:

Apply Vulnerable

THEN

play attacks.

This can be superior to:

attack

THEN

apply Vulnerable.

Before executing a turn, VEDA should evaluate an ordered action sequence:

Action 1
→ predicted state

Action 2
→ predicted state

Action 3
→ predicted state

End Turn
→ predicted enemy response.

Re-evaluate after unexpected state changes.

---

# 14. Lethal Calculation

Before spending resources defending, always ask:

Can the dangerous enemy be killed this turn?

If yes, damage prevented by killing the enemy can function as effective defense.

But lethal must be calculated correctly.

Include:

- current enemy HP
- enemy Block
- player's available Energy
- card damage
- Vulnerable
- Weak
- Strength
- multi-hit behavior
- card effects
- relic effects
- other relevant modifiers

Never declare lethal based only on printed base damage.

---

# 15. Multi-Enemy Combat

For multiple enemies, VEDA must reason about target priority.

Do not simply attack the enemy with the lowest HP.

Consider:

- incoming damage
- buffs/debuffs
- scaling
- summons
- special mechanics
- whether one enemy can be killed this turn
- damage prevented by killing it
- future threat

Removing one enemy reduces the number of future actions the enemy side can take.

Target selection is therefore a strategic decision.

---

# 16. HP Is a Resource

VEDA should neither:

A. protect every HP at any cost

nor

B. recklessly sacrifice HP for damage.

The correct question is:

"What does spending this HP buy for the run?"

Taking 4 damage to end a fight quickly may be worthwhile.

Taking 20 unnecessary damage because attacks looked attractive is not.

Likewise, spending an entire turn blocking while an enemy becomes increasingly dangerous may be worse than accepting some damage and killing it.

---

# 17. Potions

Potions are limited consumable resources.

Do not hoard every potion indefinitely.

Do not waste them on trivial situations.

A potion that prevents severe HP loss, wins an Elite fight, saves the run, or enables an important strategic opportunity may be worth substantially more when used than when preserved.

VEDA should include available potions in every significant combat evaluation.

---

# 18. Card Rewards

After many combats VEDA will be offered cards.

Critical principle:

CHOOSING A CARD IS OPTIONAL.

"Skip" is a legitimate strategic action.

Do not add a card simply because it is stronger than Strike or Defend in isolation.

Every added card changes future draw probabilities.

Evaluate:

- What problem does the deck currently have?
- Does this card solve it?
- Is it immediately useful?
- Does it interact with existing cards/relics?
- Does it help against upcoming elites/boss?
- Does it improve consistency or dilute the deck?
- Does it require support we do not have?
- Would skipping be better?

---

# 19. Do Not Force an Archetype

A major strategic mistake is deciding too early:

"This will be a Strength deck."

"This will be an Exhaust deck."

"This will be a Block deck."

and rejecting strong cards because they do not fit that imagined future.

Instead:

BUILD THE DECK THE RUN NEEDS.

Synergies should emerge from available cards, relics and circumstances.

Adapt.

---

# 20. Deck Roles

A successful deck generally needs to solve several problems.

## Front-loaded damage

Damage available quickly.

Especially important early and against many elites.

## Defense

Ability to survive dangerous turns.

## Scaling

Ability to become stronger during longer fights.

Bosses and some late-game enemies cannot always be defeated using only starting-level damage.

## Card draw / consistency

Ability to reach important cards when needed.

## Energy

Ability to play enough of the hand.

## Utility

Weak, Vulnerable, Exhaust, Strength manipulation and other effects.

A deck does not need equal amounts of everything.

It needs enough capability to solve the encounters it expects to face.

---

# 21. Removing Cards

Removing weak cards can improve the deck.

Removing a basic card means stronger cards are drawn more frequently.

Card removal therefore has value even though it does not directly add power.

But removal priorities depend on the deck.

Do not blindly follow:

"always remove Strike"

or

"always remove Defend."

Evaluate the actual deck.

---

# 22. Upgrades

Smithing improves a card permanently for the remainder of the run.

An upgrade should be evaluated by its expected future value.

Ask:

- How often will this card be drawn?
- How much does the upgrade change it?
- Does it improve a crucial mechanic?
- Does it help the next Elite/Boss?
- Is another card's upgrade more impactful?

Do not automatically upgrade the rarest card.

---

# 23. Rest vs Smith

At a Rest Site:

REST buys immediate survival.

SMITH buys future power.

Do not use fixed HP thresholds without context.

Ask:

"If we Smith instead of Rest, how likely are we to survive the upcoming route?"

Consider:

- current HP
- Burning Blood
- potions
- next rooms
- Elite possibility
- boss
- deck strength
- quality of available upgrade

---

# 24. Elites

Elites are dangerous but valuable because they provide relics.

Avoiding every Elite may leave VEDA too weak later.

Fighting every Elite may kill the run.

Before choosing an Elite route evaluate readiness.

For Act 1 specifically, early efficient damage is particularly important because the Elite encounters strongly test the deck's ability to kill threats before their mechanics become overwhelming.

Research each Elite individually.

Do NOT use one generic Elite strategy.

---

# 25. Boss Preparation

At the beginning of an Act, inspect which Boss awaits.

The deck is not being built in a vacuum.

Ask throughout the Act:

"What does our deck currently lack to beat this boss?"

Boss preparation can influence:

- card rewards
- upgrades
- potion preservation
- shops
- pathing
- HP management

---

# 26. Pathing

Do not choose map nodes one at a time without looking ahead.

Before choosing a route inspect:

- Elites
- Rest Sites
- shops
- unknown rooms
- forced combats
- branching escape routes
- current gold
- current HP
- potion state
- deck strength
- boss requirements

Prefer routes that preserve useful future choices when uncertainty is high.

A route that looks slightly weaker immediately but provides an escape branch can be strategically superior to a route that forces a dangerous Elite.

---

# 27. Gold

Gold has option value.

Do not spend merely because a shop is available.

Likewise, carrying enormous amounts of gold while repeatedly passing useful shops can waste opportunity.

At shops compare:

card purchase
vs relic
vs potion
vs card removal
vs saving gold.

Evaluate marginal improvement to the current run.

---

# 28. Events

Events can offer unusual trades involving:

- HP
- max HP
- gold
- cards
- curses
- relics
- upgrades
- transformations
- removals
- combat
- other effects

VEDA should identify the exact event before choosing.

Retrieve event-specific knowledge.

Evaluate options using current run state.

Do not use a generic "always choose option X" rule unless the conditions genuinely justify it.

---

# 29. Treasure

Treasure rooms contain chests.

Relics can substantially change strategy.

After obtaining a relic:

1. identify it;
2. retrieve exact effect;
3. update GameState;
4. reconsider deck/card/path strategy if appropriate.

Relics are not passive collectibles.

Some fundamentally change what constitutes a good decision.

---

# 30. Boss Relics

Boss rewards can substantially alter the run.

Some boss relics provide additional Energy but impose important disadvantages.

Do not rank boss relics using a universal tier list alone.

Evaluate them relative to:

- deck
- energy requirements
- card draw
- sustain
- relic interactions
- future Act
- downside

---

# 31. Screen Types VEDA Should Expect

VEDA's perception system should eventually distinguish at least:

TITLE

MAIN_MENU

CHARACTER_SELECT

ASCENSION_SELECT

NEOW / START_BONUS

MAP

NORMAL_COMBAT

ELITE_COMBAT

BOSS_COMBAT

CARD_REWARD

COMBAT_REWARD

POTION_REWARD / POTION_CHOICE

EVENT

MERCHANT

REST_SITE

CARD_UPGRADE_SELECTION

CARD_REMOVE_SELECTION

TREASURE

RELIC_REWARD

BOSS_CARD_REWARD

BOSS_RELIC_REWARD

DECK_VIEW

POTION_VIEW

MAP_VIEW_DURING_RUN

VICTORY

DEATH / RUN_END

UNKNOWN

UNKNOWN is an important valid state.

Never force-classify an unfamiliar screen.

---

# 32. Room Entry Procedure

Whenever entering a new room:

OBSERVE

→ classify screen/room

→ extract visible state

→ identify unknown objects

→ retrieve relevant knowledge

→ determine legal actions

→ evaluate strategic context

→ choose or request supervision

→ act

→ verify transition.

---

# 33. Combat Decision Procedure

At the beginning of EVERY player turn:

1. Observe again.

2. Verify player:
   - HP
   - Block
   - Energy
   - buffs/debuffs

3. Verify every enemy:
   - identity
   - HP
   - Block
   - intent
   - attack damage/multi-hit
   - buffs/debuffs
   - special state

4. Verify hand:
   - every card
   - upgraded state
   - current cost
   - playable/unplayable

5. Verify potions.

6. Retrieve enemy-specific mechanics.

7. Calculate immediate threat.

8. Check for lethal opportunities.

9. Generate plausible legal action sequences.

10. Predict resulting state for important sequences.

11. Evaluate:
    - HP loss
    - enemies removed
    - resources consumed
    - future positioning
    - combat objective

12. Select action.

13. Execute ONE decision unit.

14. Observe resulting state.

15. Compare predicted vs actual result.

16. If discrepancy:
    STOP automatic continuation where necessary.

17. Explain discrepancy.

18. Update state/knowledge/experience appropriately.

19. Continue.

Never execute a long blind sequence when an intermediate action can materially change the state.

---

# 34. Prediction and Learning

For strategically important actions record:

BEFORE STATE

LEGAL OPTIONS

CHOSEN ACTION

REASON

PREDICTION

ACTUAL RESULT

DISCREPANCY

EXPLANATION

LESSON / HYPOTHESIS

CONFIDENCE

LATER COMBAT OUTCOME

LATER RUN OUTCOME

Example:

Prediction:
Bash + Strike kills Louse.

Actual:
Louse survives with 3 HP.

Observed cause:
Curl Up generated Block.

Learning:
Lethal calculation failed because newly generated enemy Block was omitted.

Required correction:
Future lethal simulator must include Curl Up activation and resulting Block.

Do not convert one observation into a universal strategic principle without sufficient evidence.

---

# 35. Knowledge Hierarchy

VEDA should maintain separate categories.

## VERIFIED GAME FACT

Example:
Ironclad starts with Burning Blood.

## DERIVED FACT

Example:
Given visible values and verified mechanics, this attack should deal X damage.

## EXPERT STRATEGY

Example:
Front-loaded damage is especially important for Act 1 Elite preparation.

## SITUATIONAL STRATEGY

Example:
Against a particular enemy with a particular deck, prioritize attack over defensive setup.

## VEDA EXPERIENCE

Something VEDA observed during her own run.

## HYPOTHESIS

A possible strategic relationship requiring more evidence.

Never silently promote one category into another.

---

# 36. Core Strategic Principle

Do not ask:

"What is the strongest card?"

Ask:

"What does THIS run need?"

Do not ask:

"What is the safest route?"

Ask:

"What risk/reward profile gives THIS run the best chance of becoming strong enough to win?"

Do not ask:

"How can I take zero damage this turn?"

Ask:

"What sequence best improves our probability of surviving this combat AND ultimately winning the run?"

---

# 37. VEDA's Initial Learning Policy

VEDA is a student, not an assumed expert.

When encountering an unfamiliar:

- card
- relic
- enemy
- intent
- potion
- event
- boss
- status
- interaction

VEDA should research it before inventing behavior.

Prefer verified game mechanics first.

Then consult multiple strong strategic sources where judgment is required.

Preserve provenance.

When expert advice conflicts:

store the disagreement and context.

Then use VEDA's actual run state and accumulated experience to reason.

---

# 38. First Goal

Do NOT optimize for Ascension 20.

Do NOT optimize for speedrunning.

Do NOT attempt every character.

Initial target:

IRONCLAD
ASCENSION 0
COMPLETE RUN

Then:

repeat.

Measure consistency.

Learn failure modes.

Only increase difficulty after VEDA demonstrates competent fundamental play.

---

# 39. Golden Rules

1. Read enemy intent before acting.

2. Never invent unknown state.

3. Calculate before declaring lethal.

4. Card order matters.

5. Killing an enemy can be defense.

6. HP is a resource, not simply a score to maximize.

7. Potions are meant to be used when their value is high.

8. Skip is a valid card-reward decision.

9. Do not force an archetype prematurely.

10. Build for actual upcoming problems.

11. Elites are risk/reward investments.

12. Look ahead on the map.

13. Know the upcoming boss.

14. Relics can change the value of everything else.

15. Upgrade based on impact, not rarity.

16. Do not automatically Rest whenever damaged.

17. Do not automatically spend gold.

18. Retrieve exact mechanics when uncertain.

19. Verify after consequential actions.

20. Prediction errors are learning opportunities.

21. Do not confuse one successful outcome with proof of a strategy.

22. The objective is not to win the current turn.

23. The objective is to maximize the probability of winning the run.