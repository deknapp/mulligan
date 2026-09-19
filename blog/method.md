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
| Does its card ranking match real players' results? | Yes, moderately: Spearman correlation **+0.64** with real win rate when drawn (a rarity-only guess gets +0.29) |
| In simulated drafts, do card win rates match real drafts? | Moderately: **+0.50** over about 170 cards |
| Do color pairs finish in the real order? | Roughly: **+0.52** over the ten pairs |
| Do bots take cards in the real pick order? | Weakly: **+0.31** over 52 cards |
| Is being on the play worth about the same? | Yes: **55.5%** on the play in simulation; real HOB games work out to about 55% |
| Given two builds of one deck, does it pick the better one? | Usually: it agrees with a model fitted to real games on **77%** of three-card swaps |
| Can it rank *different* decks by how they really did? | Barely: Spearman **+0.11** over 1,000 real decks (a model fitted to real games gets +0.20, and pilot skill alone +0.34) |

It also has known biases, measured on HOB, that you should apply to every post:

- **It overrates high-toughness creatures** and decks full of them. Bots don't
  punish a wall with tricks, evasion or going wide the way people do.
- **It still underrates blue,** though by about half as much since September 18,
  when we stopped the bots casting their card draw in their own upkeep (they
  now wait for the opponent's end step, like people do). Real blue pairs won
  52–56% in HOB; simulated ones 45–49%.
- **Cards with a few hundred games are noisy.** A card win rate moves several
  points between runs. Posts only call out a card when its 95% interval clears
  the set's average.

## What the numbers are for

Card win rates and archetype records here are **predictions for a format
nobody has played yet**. They're a hypothesis for your first drafts, not a
replacement for 17Lands once real data exists.

The tools update themselves. Once a day the site fetches the set's live
17Lands numbers. When a card has a real win rate from at least 500 games,
the tools use it instead of the simulated one (shifted onto the same scale,
since 17Lands players win more than average), and they say which numbers are
real. Before release, everything is simulated, and the tools say that too.

All the code, card data and raw simulation output are in the
[repository](https://github.com/deknapp/mulligan).
