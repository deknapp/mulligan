# mulligan

Deck decisions for MTG Arena Limited, backed by real games and by simulation.
It reads your decks and card pools straight from Arena's log, tells you which
of two decks is better and why, suggests the best build of the pool you
drafted or opened, and can play any two decks against each other. Runs on a
laptop CPU in seconds. Currently one set: **The Hobbit (HOB)**.

```
$ mulligan decks                       # your event decks, from the Arena log
  log:2  PremierDraft_HOB_20260811  40 cards  2026-09-06 09:48

$ mulligan build log:2                 # the best build of the 42 cards you drafted
  the deck you played: 46.4% vs an average opponent
  best build found (UG): 51.8%
  suggested vs played, head to head: 55% (90% interval 52%–59%)
  changes (from real-game card values):
    no longer 3+ colors  (+4.0 pts)
    − 1× Down, Down to Goblin-town  (-1.4 pts)
    − 1× Part in Friendship  (+1.5 pts)
    ...

$ mulligan versus log:2 clipboard      # vs a deck copied with Arena's Export button
```

## What to trust, and for what

Every claim here is measured on held-out real HOB games from
[17Lands](https://www.17lands.com) (217,581 Premier Draft and 55,086 sealed
games, each with its full decklist):

| question | tool | how well it works |
|---|---|---|
| Which build of *my* deck is better? (swap cards, change lands, cut a color) | **deck model** (real games) | the core of the model: each card's and deck-shape's value, fitted to real results |
| | **simulator** (bots play both versions) | agrees with the real-game model on the direction of 77% of 3-card swaps (66 of 86), Spearman +0.34 |
| Which of two *different* decks is better? | **deck model** | held-out decks it ranks in its top fifth won 67.2%; bottom fifth 57.0%; Spearman +0.20 |
| | simulator | **does not work** (Spearman ≈ 0): bot pilots exaggerate differences between human-built decks |
| How good is each card? | simulator (no data needed) | simulated games-in-hand win rate vs real: Spearman **+0.62** (rarity alone: +0.31) |

`versus` and `build` lead with the deck model and say how sure they are (a 90%
interval from 8 bootstrap refits). The simulator is still worth having: it needs
no real data (so it works on release day), it sees matchups the model cannot
(the model rates decks against an *average* opponent), and you can watch it
play.

### The deck model

A logistic regression of win/loss on the full decklist, the pilot's skill and
the play/draw (`mulligan fit --set hob`, about 30 seconds including the
bootstrap). Besides a value for every card, it learns deck-shape effects:

| deck shape (HOB Premier Draft, vs a typical deck) | effect, log-odds |
|---|---|
| 3+ colors | −0.16 |
| one color | +0.15 |
| two or fewer cards costing 1–2 | −0.17 |
| 18 lands (vs 17) | −0.10 |
| 16 lands | +0.04 |

Treat these as associations: aggressive decks both run 16 lands and win, so
the 16-land effect is partly the archetype. It cannot see synergies between
your cards; the output says so.

### The simulator

A rules engine that plays real games: shuffles, mulligans, the stack,
triggers, combat, state-based actions. The rules are hard-coded, cards are
data, and agents only ever choose among legal actions. HOB: 178 of 193 cards
playable (all commons and uncommons), 59 with a listed simplification.

Your decks' fidelity is reported with every simulated result, and it matters:
the 4–3 deck above lost simulated games partly because its cards were
simplified (29.8% vs the field), then gained when missing text was restored
(35.4%). Making two more of its cards playable dropped it to 23.5%, because
The Notary Hobbits was only cast in half the games where it was drawn in a
three-color, 17-land deck; when it was cast the deck won 63%.

## Commands

| command | what it does |
|---|---|
| `mulligan decks` | your event decks from the MTG Arena log |
| `mulligan build log:N` | best build of your drafted/opened pool vs what you played (deck model) |
| `mulligan versus A B` | two decks (`log:N`, `clipboard`, or a file): deck model, then simulation |
| `mulligan fit --set hob` | refit the deck model from 17Lands' public games |
| `mulligan sealed --set hob --seed N` | open a random sealed pool and build it |
| `mulligan compare A B --set hob` | simulate two decks head to head and against a field |
| `mulligan rate --set hob` | card ratings from self-play |
| `mulligan validate --set hob --ratings r.json` | check card ratings against 17Lands |
| `mulligan play A B --set hob` | watch one simulated game |
| `mulligan sets` | how much of each compiled set the engine plays |
| `mulligan agents A B --deck D` | which agent plays better (seat-swapped mirrors) |
| `mulligan train --set hob` | self-play training (experimental; see below) |

Arena setup: Options → Account → "Detailed Logs (Plugin Support)". The log
holds only your own decks and pools; a friend's deck comes from their Export
text.

Reproduce the validations: `python -m mulligan.validation.swaps`;
`mulligan.validation.decks.validate_simulator()`; `mulligan rate` + `mulligan
validate`.

## Install

```
git clone https://github.com/deknapp/mulligan && cd mulligan
uv sync            # or: pip install -e .
uv run mulligan decks
```

Python 3.11+. Dependencies: `typer`, `rich`. The deck models ship in the repo
(`src/mulligan/models/`, ~50 KB per format); refitting downloads 17Lands'
public game data (~20 MB for HOB).

## How the simulator plays, and what did not work

A hand-coded heuristic player scores every legal action (curve out, point
removal at the biggest threat, attack when no block is free, block to trade
up). Attempts to improve on it, each measured against it on the same deck
pairings with seats swapped:

| approach | result vs heuristic |
|---|---|
| policy-gradient correction, temperature 0.5 / 0.15 | 47–50%, no gain |
| card values from simulated ratings | 49.0–50.3%, no gain |
| turn mana planning (cast the best combination, not the best card) | 49.9%, no gain (removed) |
| **search**: top-3 moves, 4 re-dealt playouts each | **63.0% (56.1–69.4%)**, but ~100× slower |
| linear model distilled from search | 38.6–42.2%, worse |

Search shows better play is reachable; compressing it into a small fast model
is the open problem. Design notes: [docs/DESIGN.md](docs/DESIGN.md).

17Lands data is used under their public data terms (CC BY 4.0); card data comes
from [Scryfall](https://scryfall.com). Magic: The Gathering is © Wizards of the
Coast; this is an unofficial fan project.
