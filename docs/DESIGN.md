# Design

mulligan is a Magic: The Gathering simulator for Limited. It reads in a set,
builds sealed decks from it, and has agents play those decks against each other
to answer questions like *which of these two decks is better?* and *how good is
this card?* It is meant to run on an ordinary laptop, on the CPU, in minutes.

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

HOB (The Hobbit) was compiled in a Claude Code session: 176/193 cards playable,
100% of commons and uncommons, 66 with noted approximations, 17 unsupported.

## Engine vocabulary

- **Filters** (`engine/filters.py`): one grammar for "which objects" —
  `creature:yours,other,subtype=Dwarf`, `artifact|enchantment`,
  `creature:zone=graveyard,yours,mv<=3`. Used by targets, triggers, statics and
  counts.
- **Effects** (`engine/effects.py`): damage, destroy, exile, bounce, pump,
  counters, tokens (incl. Treasure, Food, equipment tokens), amass, draw, loot,
  recruit, mill, scry, search, look-at-top, impulse draw, sacrifice, counter,
  fight/bite, attach, tap/untap, conditionals, delayed triggers.
- **Triggers**: etb, dies, other_etb, landfall, attacks, you_attack, combat
  damage to a player, upkeep, beginning of combat, first main, end step, cast
  (creature / noncreature / any, yours or an opponent's), draw, second draw,
  counters placed.
- **Statics**: stat changes, keywords, restriction flags (`cant_block`,
  `unblockable`, `doesnt_untap`, `loses_abilities`, `cant_be_blocked_by:<f>`),
  ward; on self, the equipped/enchanted creature, or everything matching a
  filter; optionally conditional.
- **Casting**: modal spells, auras, adventures, flashback, kicker, additional
  costs, conditional cost reductions, flash, hybrid mana, ward as a tax.

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

Vehicles, planeswalkers, X costs, copying, "choose a creature type", damage
prevention, replacement effects, extra combats, control-changing, casting from
graveyards other than flashback. Cards that need these are unsupported or list
the omission as an approximation.

## Information

Agents get a `PlayerView`: public zones, life totals, hand sizes, their own
hand. Never the opponent's hand or either library's order. A learning agent
rewarded for winning would otherwise find and exploit the leak.

## Statistics

Every comparison reports a 95% interval. Head-to-head games are seat-swapped
pairs on shared seeds. Deck-vs-field comparisons put both decks against the
same field decks on the same seeds and report the *paired* difference, which is
far tighter than two independent runs.
