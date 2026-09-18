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

So far the heuristic's play is the ceiling. Next ideas: a per-decision credit signal (rollouts from determinized
states) instead of whole-game win/loss, and fixing the heuristic's known
blind spots (big bodies, amass synergy) directly.

Also next: compiling Reality Fracture (FRA) when it releases on 2026-10-02.

17Lands data is used under their public data terms; card data comes from
[Scryfall](https://scryfall.com). Magic: The Gathering is © Wizards of the
Coast; this is an unofficial fan project.
