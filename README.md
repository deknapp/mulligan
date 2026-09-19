# mulligan

A Magic: The Gathering simulator where agents play decks from a real set
against each other, so you can find out which deck is better, and how good each
card is, before anyone has played the set. It runs on a laptop CPU in seconds
to minutes.

```
$ mulligan sealed --set hob --seed 7 --out a.txt           # open a pool, build a deck
$ mulligan sealed --set hob --seed 7 --lands 16 --out b.txt
$ mulligan compare a.txt b.txt --set hob
Head to head
a.txt vs b.txt: 1000 games
  a.txt wins 52.3%  (95% CI 49.2%–55.4%)
  verdict: no clear difference yet (more games would narrow it)

Against the field (24 sealed decks of HOB, 20 games each)
  a.txt: 55.4% vs the field (95% CI 50.9%–59.8%, 480 games)
  b.txt: 55.8% vs the field (95% CI 51.4%–60.2%, 480 games)
  difference a.txt − b.txt: -0.4% (95% CI -5.5% to +4.7%)
5.5s
```

## Your Arena decks

```
$ mulligan decks                      # event decks found in your MTG Arena log
  log:2  PremierDraft_HOB_20260811  40 cards  2026-09-06 09:48
$ mulligan versus log:2 clipboard     # vs a deck copied with Arena's Export button
$ mulligan build log:2                # the best build of the pool you drafted
```

`build` reads the whole pool you drafted or opened from the log and searches
two- and three-color builds using real-game card values. For the 4–3 deck
below it suggests dropping the black splash for a two-color blue-green build
(model: 55% head to head against the deck played, 90% interval 51–58%). The
card values rate each card against an average opponent and cannot see
synergies between your cards, and the output says so.

Decks come from Arena's `Player.log` (turn on Options → Account → "Detailed
Logs (Plugin Support)") or from Export text (a file, or `clipboard`). The log
only holds your own decks; Arena never logs an opponent's list, so a friend's
deck comes from their Export. Cards the engine cannot play yet are replaced by
a basic land and reported, and every result says how much of each deck is
modelled exactly:

```
fidelity: log:2: 12 of 23 spells modelled exactly, 9 approximated, 2 replaced — more
than a quarter of this deck is simplified, so treat its result as unreliable
```

That warning is earned. The deck above went 4–3 in a real Premier Draft. How
the simulator's view of it changed as the engine improved (against the same
field of 24 sealed decks):

| engine state | deck vs field |
|---|---|
| a third of its cards missing one ability, 2 cards replaced by Forests | 29.8% |
| Woodland Weavemaster's Elf mana, Silvan Reveler's land recursion, modal and graveyard triggers restored | 35.4% |
| The Notary Hobbits and Part in Friendship playable instead of Forests | 23.5% |

The last step made it *worse*, and the 17Lands model says it should not have
(those two cards are roughly average in real games). The rules are right; the
pilot is not: The Notary Hobbits is a ramp card (each Halfling taps for mana)
and the heuristic never taps creatures for mana it does not need that moment.
Better rules exposed the next bottleneck, which is play skill on unusual cards.

So `versus` also answers from **real games**, when 17Lands has data for the set:
a logistic regression of win/loss on the full decklist, the pilot's skill and
the play/draw, fitted to 17Lands' public game logs (`mulligan fit --set hob`,
about 10 seconds). It needs no rules engine, so no card is ever simplified, but
it rates decks against an average opponent and so misses specific matchups.

| HOB Premier Draft deck model (217,581 games) | held-out result |
|---|---|
| decks it ranks in its top fifth | actually won 67.2% |
| decks it ranks in its bottom fifth | actually won 57.0% |
| rank correlation, deck score vs actual win rate (2,627 decks, ≥5 games) | +0.20 (pilot skill alone: +0.35) |
| the 4–3 deck above | predicted 47% vs an average opponent |

### Which answer to trust (tested)

The real test of a deck tool is whether it ranks *real* decks the way their
real results do. `validation/decks.py` takes held-out real HOB decks with at
least 5 games each and scores them every way:

| predictor | Spearman vs the decks' actual win rates |
|---|---|
| pilot skill alone (not a deck property) | +0.30 to +0.35 |
| **17Lands deck model** | **+0.16 to +0.26** |
| average real 17Lands GIH WR of the deck's cards | +0.20 |
| **simulator** (each deck vs 30–100 other real decks) | **−0.01 to −0.02** |
| average *simulated* card rating (release-day proxy) | +0.06 |

So for deck decisions on a set with data, **use the deck model**. The
simulator's card ratings track reality (+0.62 above), but its deck-vs-deck
verdicts on human-built decks currently do not: the heuristic pilots
exaggerate differences between decks (simulated win rates spread 21–73%) and
rank them by what bots are good at. Better pilots may change that; until they
are shown to, `versus` leads with the data model.

## Does it mean anything?

The simulator's card ratings — each card's win rate in games where it was
drawn, from 20,000 self-play games of sealed HOB (about a minute) — against
the same statistic from real players on
[17Lands](https://www.17lands.com) (644,000 sealed games in hand):

| rating source | Spearman vs 17Lands HOB Sealed GIH WR (108 cards) |
|---|---|
| **simulated self-play** | **+0.62** |
| card rarity alone | +0.31 |
| a rating from the card's text | +0.10 |

Within commons alone it is +0.52, within uncommons +0.47, so it is not just
"rares are better." Where it disagrees, it disagrees in the direction you would
predict from bots: it overrates big, simple creatures (bots handle them badly)
and underrates the set's Goblin synergy and cards whose text the engine had to
simplify. Reproduce with:

```
$ mulligan rate --set hob --out hob.json
$ mulligan validate --set hob --ratings hob.json
```

## How it works

- **Rules are hard-coded.** The engine enumerates only legal actions, so an
  agent can play badly but never cheat or misread a card.
- **General strategy is hard-coded.** A heuristic agent plays sound Limited
  (curve out, remove the biggest threat, attack when no block is free, block to
  trade up). It knows nothing about any particular set.
- **The set's particulars are meant to be learned.** A small learned correction
  on top of the heuristic, trained by self-play against a league of its own
  earlier versions (`mulligan train --set hob`). Plain Python, no GPU. It trains,
  but has not yet beaten the heuristic — see Status.
- **Cards are data.** A set is compiled once into a JSON vocabulary — the
  mechanical fields straight from Scryfall, the rules text by a language model —
  and committed, so running the simulator needs no API key. HOB: 176/193 cards
  playable, all commons and uncommons.

More in [docs/DESIGN.md](docs/DESIGN.md).

## Install

```
git clone https://github.com/deknapp/mulligan && cd mulligan
uv sync            # or: pip install -e .
uv run mulligan sets
```

Python 3.11+. Dependencies: `typer`, `rich`. That's all.

## Commands

| command | what it does |
|---|---|
| `mulligan decks` | your event decks from the MTG Arena log |
| `mulligan build log:N` | the best build of your drafted/opened pool, by real-game card values, vs what you played |
| `mulligan versus A B` | which of two Arena decks would win (`log:N`, `clipboard`, or a file): from real 17Lands games and by simulation |
| `mulligan fit --set hob` | fit the deck model to 17Lands' public games for a set |
| `mulligan sets` | compiled sets and how much of each the engine can play |
| `mulligan sealed --set hob --seed N` | open a sealed pool and build the best deck |
| `mulligan compare A B --set hob` | which deck is better: head to head, and against the set's field |
| `mulligan rate --set hob` | card ratings from self-play |
| `mulligan validate --set hob --ratings r.json` | check ratings against 17Lands |
| `mulligan train --set hob` | train the learned agent for a set (experimental; see Status) |
| `mulligan agents A B --deck D` | which agent is better (seat-swapped mirror matches) |
| `mulligan play A B --set hob` | watch one game |
| `mulligan ingest <set>` | fetch a new set from Scryfall to compile it |

Decklists use the Arena export format (`2 Grizzly Bears`, set codes ignored).

## Status

Working: the engine, HOB, sealed deckbuilding, deck comparison, simulated
ratings and their validation.

Not working yet: learning. Two approaches were tried on HOB, each evaluated
against the heuristic on the same deck pairings with seats swapped:

| approach | result vs heuristic |
|---|---|
| policy-gradient correction, temperature 0.5 (7 iterations × 2000 games) | 50.1% at iteration 3, 47.3% at 6 — exploration noise hurt |
| policy-gradient correction, temperature 0.15 (12 × 2000) | 48.3–50.1% (±3%), no gain |
| heuristic with card values from simulated ratings (3 strengths) | 49.0–50.3% (±2.2%), no gain |
| **search**: top-3 moves, 4 re-dealt playouts each (not a learned model) | **63.0% (56.1–69.4%, 200 games)** — but ~100× slower |
| linear correction distilled from search's decisions (14,899) | 38.6–42.2% — worse; a linear model can't hold situational choices |

Search clearly beats the heuristic, so better play is reachable; the open
problem is compressing it into a small, fast model. Next ideas: a per-decision credit signal (rollouts from determinized
states) instead of whole-game win/loss, and fixing the heuristic's known
blind spots (big bodies, amass synergy) directly.

Also next: compiling Reality Fracture (FRA) when it releases on 2026-10-02.

17Lands data is used under their public data terms; card data comes from
[Scryfall](https://scryfall.com). Magic: The Gathering is © Wizards of the
Coast; this is an unofficial fan project.
