---
title: "Correction: our bots cast their card draw in their own upkeep"
summary: Two misplays were costing blue decks games. Fixing them brought blue back toward real results. Red-green still leads Reality Fracture, and Way of the Warlord still looks like a sleeper, but Way of the Mentor doesn't.
order: 1
---

Earlier today we published [Reality Fracture, drafted 480 times before
release](2026-09-18-reality-fracture-first-drafts.html). Its fine print warned
that the simulator underrates blue. That warning deserved more than a caveat,
so we went looking for the cause. We found two plain misplays, fixed them, and
reran everything. That post stays as it was. This one says what changed.

## What the bots were doing wrong

We took a real blue deck from 17Lands' Hobbit data and replayed one of its
losses, turn by turn, with its hand visible. Two things jumped out.

**Card draw in the upkeep.** Confusticate and Bebother is an instant: counter
a spell unless its controller pays four, or draw two and discard one. The bot
cast the draw mode the first moment it could, which was the start of its own
turn. That was before it had drawn or played a land. Three mana gone, and a
four-drop stuck in hand for another turn. It did the same with a five-mana
removal spell. People cast those at the end of the opponent's turn, with mana
they didn't need.

**A fetch land left in play.** Hobbit Hole makes no mana. You sacrifice it to
put a basic land onto the battlefield. The bot scored that as a small gain
minus the cost of sacrificing a land, which came out negative until it had six
lands. It sat on Hobbit Hole until its seventh turn, a land short all game.

Neither is a blue-only mistake, but blue decks are full of instant-speed card
draw, so they paid the most. The fix: nothing gets cast in your own upkeep or
draw step unless it's a response, card draw waits for the opponent's end step,
and fetch lands get cracked right away. **The fixed bots beat the old ones
54.5% to 45.5%** over 2,000 games with real Hobbit decks (±2.2 points). That's
the first heuristic change today that measurably played better.

## Checked against real drafts

The Hobbit has months of real games, so it's where we can check. We drafted it
60 times with the old bots and 60 times with the fixed ones, and compared both
with 17Lands:

{{figure 2026-09-18b-hob-pairs}}

*Win rate by color pair: real 17Lands Premier Draft games, and simulated drafts
before and after the fix, with 95% intervals. 17Lands users win more than half
their games, so compare the order, not the level.*

Every pair containing blue moved toward the real result. Blue-red went from
37% to 45%, and blue-green from 39% to 47%. The one exception is white-blue,
which the old bots almost never drafted: two decks in 480, so its old number
meant nothing. Blue is still underrated: real blue pairs won 52–56%, the fixed
bots' 45–49%. But the gap is about half what it was.

The rest of the scorecard, all Spearman rank correlations with real results:

| On The Hobbit | Old bots | Fixed bots |
|---|---|---|
| Card win rate when drawn, sealed ratings (111 cards) | +0.61 | **+0.64** |
| Card win rate when drawn, simulated drafts (about 170 cards) | +0.52 | +0.50 |
| Color-pair win rate (10 pairs) | +0.50 | +0.52 |
| Real decks' records (1,000 decks) | +0.10 | **+0.11** |
| How much blue decks are underrated (0 = none) | −0.28 | **−0.06** |

The draft-level numbers barely moved. The blue gap closed most of the way, and
ranking whole decks improved a little (within noise).

## What changed for Reality Fracture

{{figure 2026-09-18b-fra-pairs}}

*Reality Fracture color pairs in the first post's run and with the fixed bots,
60 pods and 60,000 games each.*

**What held up:**

- **Red-green is still the top pair** (53.7%, was 55.1%), with white-red and
  black-red next. Red is still the best color (52.6%).
- **White is still the most drafted color** (56% of decks) and still only
  average (49.9%).
- **Way of the Warlord is still a sleeper:** taken around pick 11 and winning
  55.5% (was 55.8%).
- **Proft, Sinister Mastermind is still overdrafted:** taken around pick 4 and
  winning 43.8%.
- **The best commons haven't moved:** Blossom-Blessed Angel 60.9% and Bestial
  Incursion 61.3%.

**What changed:**

- **Blue isn't the problem color any more.** White-blue, blue-green and
  blue-red now sit in the middle, at 50–51%. Blue-black is last at 45.0%, with
  black-green just above it at 46.6%.
- **We're retracting the "Way of the ..." tiers.** The first post said the
  three-mana empower 5 versions (the Warlord, the Mentor, the Deathbringer)
  were a tier above the rest. Only the Warlord held up. Way of the Mentor went
  from 55.6% to **41.2%**. Its first number came from only 270 games, and the
  new one from 854. The Deathbringer dropped from 54.2% to 48.5%.

{{figure 2026-09-18b-ways}}

*The cycle with the fixed bots. The intervals are wide: several of these saw
only a few hundred games.*

The lesson for us: a card win rate from a few hundred games moves by several
points from run to run. From now on, a card only gets called out if its 95%
interval clears the set's average.

*Raw data for both sets and both bot versions is in
[blog/data](https://github.com/deknapp/mulligan/tree/main/blog/data). The fix
is commit [9dc58d9](https://github.com/deknapp/mulligan/commit/9dc58d9).*
