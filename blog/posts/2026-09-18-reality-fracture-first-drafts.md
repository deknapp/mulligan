---
title: Reality Fracture, drafted 480 times before release
summary: 60 simulated pods and 60,000 games. Red-green leads, black-green trails, and the Way of the ... cycle splits in two even though bots take every one around pick 10.
---

Reality Fracture releases on October 2. Two weeks out, 283 of its cards have
been revealed, including 278 of the 289 that open in packs. That's enough to
draft it. So we did, 480 times: **60 pods of eight bots, three packs each**.
Each bot built a 40-card deck from its picks, and the decks played **60,000
games** against each other under the full rules.

A few ground rules before the findings:

- **The 11 unrevealed cards aren't in the packs yet.** Each pack slot draws from
  the revealed cards of that rarity. There are so few gaps that we didn't
  invent stand-ins.
- **Some cards are simplified.** 268 of the 283 revealed cards are playable in
  the simulator; bots pass the other 15. Of the 229 that saw at least 300 games, 90 have some part left
  out (marked † below). The rule is that a simplification can only make a card
  weaker than printed, so a simplified card is, if anything, underrated.
- **The bots have known biases.** Measured on The Hobbit, where real data
  exists, the simulator overrates big creatures and underrates blue. More in
  [How it works](../method.html).

## Red-green is the deck to be

{{figure 2026-09-18-archetypes}}

*Win rate by two-color pair across all 60,000 games, with 95% intervals.*

Red-green won **55.1%** of its games, the best of the ten pairs and clear of
the rest. Next came white-red (52.6%) and black-red (52.4%): red is the color
doing the work. At the other end, black-green won just **43.9%**. Red-green and
black-green share green, and they are the best and worst decks in the format.
The partner color decides it.

Look at the colors one at a time and the picture sharpens:

{{figure 2026-09-18-colors}}

*Win rate of every deck playing each color, and the share of the 480 drafted
decks that played it.*

Red decks won 52.9% and black decks 47.6%, yet almost as many bots ended up
in black (39% of decks) as in red (37%). **White was the most drafted color
(55% of decks) but only average (50.0%).** White has several of the set's best
early picks, which pulls bots into it, but white decks as a whole don't win
more than anyone else. For your own first drafts, that suggests taking red
early and being willing to leave white.

Blue is the exception to trust least. Only 25% of drafted decks played it, and
blue pairs sit near the bottom of the first chart. On The Hobbit, the simulator
measurably underrated blue, so treat "blue is weak" as unproven.

## Cards that beat their pick

Each dot is a card. Left to right is how early the bots took it on average;
bottom to top is how often its owner won when it was drawn. Most cards follow
the trend: the earlier they go, the more they win. The interesting ones are
far above or below it.

{{figure 2026-09-18-pick-vs-win}}

*Average pick against win rate when drawn, for cards with at least 300 games.
Hover a dot for the card.*

These won the most relative to where they were taken:

{{figure 2026-09-18-underdrafted}}

**Way of the Warlord is the standout sleeper.** Bots took it with the 11th pick
on average, and it won 55.8% of the games it was drawn in, well above the set's
average of 51.9%. For three mana it creates a Jace with five loyalty. Then it
gives your planeswalkers "−4: 2 damage to up to one creature and 2 damage to a
player": a removal spell and a burn spell from a card the bots treat as
filler. More on the cycle it belongs to below.

Tetsuko Umezawa, Pursuer (a double-striking 2/4 with prowess) and Draconic
Visitor were already early picks, and they won even more than early picks
usually do.

These won the least relative to where they were taken:

{{figure 2026-09-18-overdrafted}}

**Proft, Sinister Mastermind looks like a three-mana 5/5 with menace.** It isn't
one. It can't be cast until your graveyard holds seven cards, which in a
typical draft deck happens late in the game, if at all. Bots took it around
pick 3 and won 43.9% of the games it was drawn in. Its discard ability (−3/−1 to a
creature for {B}) is the part that actually gets used.

Rise of the Deathbringer and Ghalta the Unstoppable also land here, with
caveats. Ghalta's cost reduction is simplified away, so in the simulator it's a
nine-mana 8/8: that one says more about the simulator than the card.

## One cycle, two tiers

Reality Fracture has a cycle of uncommon enchantments named "Way of the ...".
Each empowers Jace (creating a Jace token, or adding loyalty to one you have)
and gives your planeswalkers a new ability. The bots took every one of them
around pick 9 to 12. They didn't play alike:

{{figure 2026-09-18-ways}}

*Each card's win rate when drawn, with 95% intervals. The dashed line is the
set's average card.*

The split tracks two things:

- **Empower 5 beats empower 2.** The top three (Way of the Warlord, the Mentor
  and the Deathbringer) each cost three and start Jace at five loyalty. That's
  enough for the Warlord's −4 or the Deathbringer's −2 the turn it comes down,
  with loyalty to spare. Only the Warlord is clearly above average; the Mentor
  and the Deathbringer saw fewer games, and their intervals are wide. Way of the Pyromancer and Way of the Necromancer cost
  two but start Jace at only two, and they're near the bottom.
- **The blue ones are last.** Way of the Mind Sculptor and Way of the
  Cryomancer won about 41%. Some of that is the simulator's blue bias. But
  both are in the color whose decks were drafted least, so they usually got
  played as a splash or in a weak deck.

If you're drafting and see Way of the Warlord in your colors at pick 9, it's
probably better than whatever else is left.

## Which mechanics pull their weight

For each mechanic, we averaged the win rate of the cards that have it,
relative to the set's average card:

{{figure 2026-09-18-mechanics}}

*Average win-rate difference from the set's average card, in percentage
points, with 95% intervals from resampling the cards. The number of cards is in
parentheses.*

Flying (+3.4 points), prowess (+2.7) and prepare (+2.1) are pulling their
weight. Prepare is the set's new mechanic: creatures that come with a spell you
can cast from them. **Empower Jace averages −1.7 points, but the average hides
the story.** Its 32 cards range from −12 to +8 points. It's the payload on the
card, not the mechanic, that decides it. The same goes for surveil.

## The best common in each color

{{figure 2026-09-18-commons}}

*Top three commons by win rate when drawn, in each color.*

Blossom-Blessed Angel (white, 60.9%) and Bestial Incursion (green, 60.9%) are
the best commons in the set by a distance. Bestial Incursion is a 4/4 trampler
for four that you can cast again from the graveyard. Blossom-Blessed Angel is a
2/4 flier with vigilance that comes with a one-mana +1/+1 counter spell. Both went in the first
two or three picks, so the bots already knew.

## What's next

When the last cards are revealed, we'll draft the complete set. After October 2,
17Lands will have real games, and we'll check these predictions against them in
a new post, whichever way it goes. This post will stay as written.

*Raw data: [fra-draft-2026-09-18.json](https://github.com/deknapp/mulligan/blob/main/blog/data/fra-draft-2026-09-18.json).
Reproduce with `mulligan draft --set fra --pods 60 --games 60000`.*
