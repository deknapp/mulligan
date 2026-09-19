---
title: "Reality Fracture draft primer"
summary: What 800 simulated drafts and 100,000 games say about the format before release. Draft the open lane, since no pair dominates. Play 16 or more creatures and choose to play first. Green commons run deepest, and the Way of the Warlord and Wildspeaker are late-pick gems.
---

Reality Fracture comes out on Arena on October 2. We had bots draft it 800
times (100 pods of eight). Each bot built a deck from its picks, and the decks
played **100,000 games** against each other under the full rules. This primer
is what those games say, written as advice for your first drafts.

Each claim is tagged with how much to trust it:

- **Solid**: a big effect, clear of the noise, and the kind of thing the
  simulator gets right on sets humans have played.
- **Lean**: real in the simulation, but small or in an area where the simulator
  is weaker.
- **Unproven**: likely to be wrong; see why.

Want a specific card or pack? The [card ratings](../tools/cards.html),
[pick helper](../tools/pick.html) and [color pairs](../tools/pairs.html) tools
use the same data. Once the set is out, they switch to real 17Lands results by
themselves.

## The short version

1. **Draft the open lane.** Nine of the ten color pairs finish within 3.5
   points of each other. Being in the colors nobody else is in will
   matter more than picking the "right" pair. *Solid.*
2. **Play 16 or more creatures.** Decks with 16–17 creatures won 52.6%. Decks
   with 13 or fewer won 46.6%. *Solid, with a caveat below.*
3. **Choose to play first.** The player on the play won **53.7%**. *Solid.*
4. **Green has the deepest commons.** Red, green and white are slightly the
   best colors. Black is the weakest. *Lean.*
5. **Judge Empower Jace cards by what they do besides Jace.** Some of the best
   and worst uncommons share the mechanic. *Solid.*
6. **Late picks worth watching for:** Way of the Warlord, Way of the
   Wildspeaker and Tam's Resistance. *Solid.*

## Colors and pairs

{{figure 2026-09-19-pairs}}

*Each pair's win rate against the whole field, with 95% intervals.*

The spread is narrow. Red-green (51.8%) and white-green (51.7%) are on top,
and seven pairs sit between 48.5% and 51%. **Blue-black is the one pair to
avoid (46.2%)**. It's the only one clearly below the pack. Real formats usually
spread wider than this once people learn them. A flat field before release means
there's no pair to force, and being in the open lane is what matters.

By color, red (50.8%), green (50.5%) and white (50.4%) are roughly even. Blue
(49.4%) and black (48.6%) trail. Half of all bot decks played white, so expect
white to be contested.

- **Black is shallow.** Its best cards are rares: Sanctum Lurker, Curse-Marred
  Demon, Garruk, Veiled Butcher and Overwrite the Multiverse. At common,
  Extended Absence (exile a creature for four) and Screeching Soulbreaker are
  good, and the rest is thin. If you're the second black drafter at the table,
  get out. *Lean.*
- **Blue may be better than it looks.** On The Hobbit, where real results
  exist, this simulator underrated blue decks. So "blue is middling" here
  probably means "blue is fine". Blue-black's last place is the weakest call
  in this primer. *Unproven.*

The [color pairs tool](../tools/pairs.html) shows each pair's best commons and
uncommons, measured in that pair's decks, and a sample deck.

## What to take

### Commons

{{figure 2026-09-19-commons}}

*The three best commons in each color by win rate when drawn.*

**Bestial Incursion** (a 4/4 trampler for four that comes back with flashback)
and **Blossom-Blessed Angel** (a 2/4 flier with vigilance plus a one-mana +1/+1
counter) are the best commons in the set. Take either over most uncommons.

Green runs deepest. After Bestial Incursion come Wrecking Gecko (a 5/5 ward
for five), Arcane Amphisbaena (a two-mana deathtouch body plus a Jace) and
Greenhouse Propagator. In red, Tether Technician (a 4/5 with reach that can
deal 2 damage when it enters) and Heartstring Puller (a 3/1 trampler that
brings a 2/2) are the picks.

### Uncommons

{{figure 2026-09-19-uncommons}}

**Thalia, the Survivor** (a 3/4 lifelinker that taxes their spells) and
**Tetsuko Umezawa, Pursuer** (a 2/4 double striker) are the best uncommons,
and **Kiora of Fire and Ashes** (six mana for a 2/2 and a 5/5 flying dragon)
isn't far behind. Your Fate Ends Here is the best removal spell at uncommon.

### Sleepers

These won far more than their pick position says, and their 95% intervals
clear the set's average. Bots took them around pick 10 or later.

{{figure 2026-09-19-sleepers}}

The two Ways are the standouts. Each creates a Jace planeswalker with enough
loyalty to use its new −4 the turn it comes down: Warlord's is 2 damage to a
creature and 2 to a player, and Wildspeaker's makes a 4/4 trampler. Both
leave a Jace behind that your opponent has to deal with. **Tam's Resistance**
is a two-mana hybrid green or blue common: a +1/+1 counter and a four-loyalty
Jace.

The two Garruks and Overwrite the Multiverse are mythics, so you'll rarely see
them late. When you do, take them.

### Traps

These go early and don't win.

{{figure 2026-09-19-traps}}

**Proft, Sinister Mastermind** reads like a three-mana 5/5 menace. But you
can't cast it until your graveyard holds seven cards, which is late or never
in Limited. **Germinate Recruits** does nothing unless you gained life that
turn. **Loyal Tutor** needs planeswalker *cards* in your deck (Jace tokens
don't count). Ghalta is marked † because the simulator leaves out its cost
reduction, so ignore its number.

## Empower Jace and the Way cycle

Empower Jace is the set's main mechanic. It puts loyalty counters on your Jace
token, creating one if you don't have one. Across its 32 cards it averages
about zero: neither good nor bad on its own. **What decides a card is its
payload.** The clearest case is the uncommon Way cycle:

{{figure 2026-09-19-ways}}

*Win rate when drawn for each Way, with 95% intervals. The dashed line is the
set's average card.*

Warlord and Wildspeaker are well above average, and Deathbringer and Healer
are slightly above. The rest are below average, and Necromancer is the lowest
of those. Way of the Mind Sculptor looks worst of all, but
part of its text is left out in the simulator, so don't read much into it.

Two other patterns: **flying** creatures average **+2.6 points** over the set's
average card, and cards with **prepare** (creatures that carry a spell you can
cast from them) sit at about average.

## Building your deck

We compared the 800 decks' records against what was in them. We also held
each deck's average card quality fixed, so "more creatures" doesn't just mean
"better cards".

{{figure 2026-09-19-creatures}}

*Deck win rate by number of creatures, with 95% intervals.*

- **Creatures matter most.** With card quality held fixed, each extra creature
  is worth **+0.65 points** of win rate (±0.20). The average bot deck ran 15.
  Aim for 16–17. *Solid, but see the caveat.*
- **Removal counts, too:** about **+0.6 points** per removal spell (±0.25),
  quality held fixed. *Lean.*
- **Two-drops don't matter by themselves.** Decks with more two-drops win more,
  but only because they have more creatures. With the creature count held
  fixed, the extra two-drops add nothing. Don't take a weak two-drop over a
  strong four-drop to fix your curve. *Lean.*
- **Choose to play first:** 53.7% on the play (±0.3). *Solid.*

The caveat: on The Hobbit, the simulator overrated decks full of big-bodied
creatures. Bots don't punish a board of large creatures with tricks and
evasion the way people do. The direction here is right, but the size of the
creature effect is probably a bit high.

## What the simulator can't tell you (yet)

- **Combat tricks look terrible here** (Blazing Crescendo, Charge the Sanctum
  and Academic Ascent are near the bottom). Bots use tricks worse than people,
  so these are underrated. Don't write them off.
- **Cards marked † are simplified.** A simplification only ever makes a card
  weaker, so treat their numbers as floors. Ferocity of the Hunt is the
  clearest case: without its return-from-death half, it's just a deathtouch
  aura.
- **Not every card is revealed yet.** 285 of the set's 460 cards are out
  (many of the rest never open in draft packs). Only revealed cards are in
  the simulated packs, and 270 of the 285 are playable here.
- **Nothing here has met a real draft deck.** The bots have never played an
  Arena deck from this set, because none exist yet.

## How this updates

The tools refresh on their own. Once a day, they fetch Reality Fracture's live
17Lands data. When real players have enough games with a card, its real win
rate replaces the simulated one, and the tools say which numbers are real.
We'll also rerun the drafts when the full card list is out. After release,
we'll check this primer against 17Lands in a new post.

*Raw data: [fra-draft-2026-09-19.json](https://github.com/deknapp/mulligan/blob/main/blog/data/fra-draft-2026-09-19.json).
Reproduce with `mulligan draft --set fra --pods 100 --games 100000`. How far
to trust the simulator in general: [How it works](../method.html).*
