# How it works

## The pipeline

1. **Cards become data.** Each card in a set is translated once into a small
   declarative format: costs, types and stats straight from Scryfall, and the
   rules text as triggers, abilities and effects. A card is either exact,
   *approximated* (with a list of what's left out), or unsupported. One rule:
   **an approximation may only make a card weaker than printed**, never
   stronger. A simplified card can't win games its real version would lose.
2. **A rules engine plays real games.** Turns, priority, the stack, combat,
   state-based actions, planeswalkers, the works. The bots only ever see what a
   player could see: their own hand, never the opponent's hand or either
   library.
3. **Bots draft.** A pod is eight bots. Each opens three packs and passes them
   left, right, left. A bot takes the card with the best card rating plus a
   bonus for staying in its colors. The bonus is zero at the first pick and
   grows through the draft. Each bot also has a small random lean toward some
   colors, so eight bots don't all fight over the same one.
4. **Bots build and play.** Each drafted pool becomes a 40-card deck: the best
   two colors, 23 spells, 17 lands. Then decks from all the pods play each
   other, tens of thousands of games at a time.
5. **We count, 17Lands-style.** The headline card number is *win rate when
   drawn*: the share of games won in which the card reached its owner's hand.
   That's the same statistic as 17Lands' "GIH WR", so the two can be compared.

## How far to trust it

The simulator was checked against a set that humans have played: **The Hobbit
(HOB)**, using 17Lands' public game data.

| Question | How the simulator did |
|---|---|
| Does its card ranking match real players' results? | Yes, moderately: Spearman correlation **+0.62** with real win rate when drawn (a rarity-only guess gets +0.31) |
| Is being on the play worth about the same? | Yes: **55.5%** on the play in simulation; real HOB games work out to about 55% |
| Given two builds of one deck, does it pick the better one? | Usually: it agrees with a model fitted to real games on **77%** of three-card swaps |
| Can it rank *different* decks by how they really did? | Barely: Spearman **+0.10** over 1,000 real decks (a model fitted to real games gets +0.20, and pilot skill alone +0.34) |

It also has known biases, measured on HOB, that you should apply to every post:

- **It overrates big, high-toughness creatures** and decks full of them.
- **It underrates blue**, especially small value creatures and instants. Bots
  get less out of card advantage, tricks and counterspells than people do.

These biases survived two rounds of smarter bot tactics, so they are more
likely about what bots can't see than about any one play they make.

## What the numbers are for

Card win rates and archetype records here are **predictions for a format
nobody has played yet**. They're a hypothesis for your first drafts, not a
replacement for 17Lands once real data exists. Each post that compares against
real data does so in a new post, rather than editing the old one.

All the code, card data and raw simulation output are in the
[repository](https://github.com/deknapp/mulligan).
