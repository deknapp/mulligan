# Design

mulligan helps MTG Arena Limited players make deck decisions. It has two
engines that answer the same questions from different evidence:

1. **The deck model** (`deckmodel.py`): a logistic regression fitted to
   17Lands' public game logs — every card's value plus a few deck-shape
   effects, controlling for pilot skill and play/draw, with bootstrap
   uncertainty. Needs real data; cannot see synergies or specific matchups.
2. **The simulator** (everything below): a rules engine where agents play
   decks against each other. Needs no data; sees matchups; its bots are
   weaker than people.

What each is good for was measured, not assumed (`validation/`, HOB):

| question | deck model | simulator |
|---|---|---|
| which card is better (card ratings vs real GIH WR) | n/a (it is fitted to them) | Spearman +0.62 |
| which build of one deck is better (3-card swaps) | the reference | agrees on direction 77% (66/86), Spearman +0.34 |
| which of two different decks is better (real decks' records) | Spearman +0.20 | ≈ 0 |

Attempts that did *not* make the simulator rank different decks better: a turn
mana planner, rating-derived card values, and card values from the deck model
itself (all Spearman ≈ 0 on 300 real decks). Search play beats the heuristic
63% but is ~100× slower; a linear model distilled from it played worse. The
likely bottleneck is tactical play, not card valuation.

Arena integration (`arena_log.py`): event decks and card pools are read from
`Player.log` by Arena id; Export text and the clipboard are also accepted.
`build` searches two- and three-color builds of a pool with the deck model;
`versus` compares two decks, and when they are builds of one deck (30+ shared
cards) simulates both against a shipped field of real decks — the validated
mode.

The rest of this document describes the simulator.

## The three layers

1. **Rules — hard-coded** (`engine/`). Turn structure, priority, the stack,
   combat, state-based actions, triggered/activated/static abilities. The engine
   lists only legal actions and applies only an action it offered, so an agent
   can play badly but can never cheat or misread a card. Games are seeded and
   reproducible.
2. **Strategy — hard-coded** (`agents/heuristic.py`). A general Limited player:
   curve out, point removal at the biggest threat, attack when no block is free,
   block to trade up, chump only to survive. It knows nothing about any set. It
   scores every legal action and plays the best one.
3. **The set's particulars — learned** (`agents/learned.py`, `learn.py`). A
   sparse linear correction on top of the heuristic's score, trained by
   self-play. Per-card features (`cast:<card>`, `attack:<card>`, …) are where
   set knowledge lives. With zero weights it *is* the heuristic, so training
   starts from a competent player and only learns what differs.

Splitting it this way is what keeps it lean: the model never has to learn the
rules or basic strategy, only corrections, so it trains on a laptop.

## Cards are data, compiled once per set

A set is `cards/data/<code>.json`: every card in a declarative vocabulary
(`cards/schema.py`). Compilation (`cards/compiler.py`) is split so a language
model only does the part that needs reading:

- **Skeleton — deterministic.** Cost, types, power/toughness, evergreen
  keywords, ward, adventure faces, basic-land mana: straight from Scryfall's
  structured fields. Nothing here can be hallucinated.
- **Semantics — an LLM (or a person).** Triggers, statics, abilities, effects.
  The loader is strict: an unknown key or effect name is a load error.

Each card is either exact, approximate (with a list of what it leaves out), or
unsupported (with a reason). `mulligan sets` reports the coverage. The compiled
file is committed, so nobody else needs an API key.

HOB (The Hobbit) and FRA (Reality Fracture, from its spoiler) were compiled in
Claude Code sessions: HOB 180/193 playable, FRA 269/283 of the cards spoiled so
far. `mulligan sets` prints the current counts.

## Engine vocabulary

- **Filters** (`engine/filters.py`): one grammar for "which objects" —
  `creature:yours,other,subtype=Dwarf`, `artifact|enchantment`,
  `creature:zone=graveyard,yours,mv<=3`. Used by targets, triggers, statics and
  counts.
- **Effects** (`engine/effects.py`): damage, destroy, exile, bounce, pump,
  counters, tokens (incl. Treasure, Food, equipment tokens), amass, draw, loot,
  recruit, mill, scry, search, look-at-top, impulse draw, sacrifice, counter,
  fight/bite, attach, tap/untap, flicker, token copies of itself, reveal-until,
  optional payments, conditionals, delayed triggers.
- **Triggers**: etb, dies, other_etb, landfall, attacks, you_attack, combat
  damage to a player, upkeep, beginning of combat, first main, end step, cast
  (creature / noncreature / any, yours or an opponent's), draw, second draw,
  counters placed, a creature card leaving your graveyard. Triggers can be
  modal ("choose one") and can work from the graveyard.
- **Statics**: stat changes, keywords, restriction flags (`cant_block`,
  `unblockable`, `doesnt_untap`, `loses_abilities`, `cant_be_blocked_by:<f>`),
  ward; on self, the equipped/enchanted creature, or everything matching a
  filter; optionally conditional.
- **Casting**: modal spells, auras, adventures, prepare, flashback, kicker,
  additional costs, X costs, convoke, conditional cost reductions, spell taxes
  and discounts, flash, hybrid mana, ward as a tax, copying spells.
- **Permanents**: planeswalkers (loyalty abilities, attacking them), vehicles
  and crew, permanents that become creatures, sagas, exhaust abilities,
  finality counters, "choose a creature type".

## Automated choices (a deliberate simplification)

Some choices inside an effect are made by the engine with a fixed policy rather
than asked of the agent: which card to discard (to a loot, recruit, or an
opponent's discard effect), what to scry to the bottom, which land to search
for, what to take from a look-at-top, what to sacrifice to an edict or a cost,
which color a Treasure makes, and whether to pay a "counter unless you pay"
tax. Asking would multiply the number of decisions for little strategic
content. The policies are in `Game.auto_*`.

Mana abilities are not agent actions either: the engine taps for mana, saving
the colors the rest of the hand needs.

## Not modelled

Damage prevention, general replacement effects, extra combats,
control-changing, casting spells from a graveyard other than by flashback,
"outside the game", and free casting (activated and triggered abilities that
work from the graveyard are supported). Cards that need these are unsupported
or list the omission as an approximation. An approximation may only make a
card weaker than printed, never stronger, so a simplified card can't win the
simulator games its real version would lose.

## Information

Agents get a `PlayerView`: public zones, life totals, hand sizes, their own
hand. Never the opponent's hand or either library's order. A learning agent
rewarded for winning would otherwise find and exploit the leak.

## Statistics

Every comparison reports a 95% interval. Head-to-head games are seat-swapped
pairs on shared seeds. Deck-vs-field comparisons put both decks against the
same field decks on the same seeds and report the *paired* difference, which is
far tighter than two independent runs.
