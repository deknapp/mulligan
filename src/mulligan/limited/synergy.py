"""Card-pair synergy: do two cards win more together than each does alone?

A card's games-in-hand win rate mixes the card's own strength with the decks
it ends up in. For a pair, the same trick 17Lands uses for one card works one
level up. Take every game played by a deck holding BOTH cards and split it by
which of the two the player actually saw:

    both      A only      B only      neither

The four groups come from the same decks, so deck quality cancels out. If the
cards simply add up, the "both" games are as far above "A only" as "B only" is
above "neither". The interaction

    WR(both) - WR(A only) - WR(B only) + WR(neither)

is what the pair adds on top of that: positive when they help each other
(an aura and a creature with hexproof, a sacrifice outlet and a death trigger),
negative when they get in each other's way (two cards that want the same slot).

It is noisy: four win rates, each from a slice of the games. So every estimate
is shrunk toward the typical pair by how much real spread there is among all
pairs (empirical Bayes), and only pairs with enough games in every cell are
kept. Each pair is reported relative to the average pair, since effects
shared by every pair (seeing both cards goes with longer games) are not
synergy. In real HOB games the true spread comes out at about 1.7 points
either way; most pairs are indistinguishable from simply adding up.

Two sources fill the same tally: simulated drafts (``limited.draft``) and
17Lands' public game data, which records each game's deck and the cards drawn.
"""

from __future__ import annotations

import csv
import gzip
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path

MIN_CELL = 20          # games needed in each of the four cells
CELLS = ("both", "a", "b", "neither")


class PairTally:
    """Per pair (a, b), a < b: [wins, games] for both / a only / b only / neither."""

    def __init__(self) -> None:
        self.cells: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0] * 8)

    def add(self, deck: list[str], seen: set[str], score: float) -> None:
        """One game from one side. ``deck`` = the distinct nonland cards, sorted."""
        for a, b in combinations(deck, 2):
            cell = self.cells[a, b]
            i = 2 * ((a not in seen) * 2 + (b not in seen))
            cell[i] += score
            cell[i + 1] += 1

    def merge(self, other: PairTally) -> None:
        for key, cell in other.cells.items():
            mine = self.cells[key]
            for i, v in enumerate(cell):
                mine[i] += v

    def to_json(self, min_cell: int = MIN_CELL) -> dict[str, list[float]]:
        return {f"{a}|{b}": [round(v, 1) for v in cell]
                for (a, b), cell in self.cells.items()
                if min(cell[1::2]) >= min_cell}


def contrast(cell: list[float]) -> tuple[float, float]:
    """(interaction, standard error) from the eight numbers of one pair."""
    rates, var = [], 0.0
    for i in range(4):
        wins, games = cell[2 * i], cell[2 * i + 1]
        p = (wins + 1) / (games + 2)            # keeps the variance off zero
        rates.append(wins / games)
        var += p * (1 - p) / games
    both, a, b, neither = rates
    return both - a - b + neither, math.sqrt(var)


def synergies(cells: dict[str, list[float]], min_cell: int = MIN_CELL) -> dict:
    """Shrunk interaction for every pair with ``min_cell`` games in each cell.

    Returns {"tau": spread of true effects, "mean": the average pair's raw
    interaction, "pairs": [{a, b, raw, se, est, n}]}; ``est`` is the shrunk
    estimate relative to the average pair and ``n`` the games with both in the
    deck.
    """
    rows = []
    for key, cell in cells.items():
        if min(cell[1::2]) < min_cell:
            continue
        a, b = key.split("|")
        raw, se = contrast(cell)
        rows.append({"a": a, "b": b, "raw": raw, "se": se, "n": int(sum(cell[1::2]))})
    if not rows:
        return {"tau": 0.0, "mean": 0.0, "pairs": []}
    # DerSimonian-Laird (random-effects meta-analysis): how much of the spread
    # is real, with each pair weighted by its precision so the noisiest pairs
    # don't drown the rest.
    w = [1 / r["se"] ** 2 for r in rows]
    mean = sum(wi * r["raw"] for wi, r in zip(w, rows, strict=True)) / sum(w)
    q = sum(wi * (r["raw"] - mean) ** 2 for wi, r in zip(w, rows, strict=True))
    tau2 = max((q - (len(rows) - 1)) / (sum(w) - sum(wi * wi for wi in w) / sum(w)), 1e-6)
    for r in rows:
        r["est"] = (r["raw"] - mean) * tau2 / (tau2 + r["se"] ** 2)
    rows.sort(key=lambda r: -r["est"])
    return {"tau": math.sqrt(tau2), "mean": mean, "pairs": rows}


def from_17lands(path: Path, keep: set[str] | None = None) -> PairTally:
    """Tally 17Lands game_data (one row per game): ``deck_<card>`` = copies in
    the deck, ``opening_hand_<card>`` / ``drawn_<card>`` = copies seen.
    ``keep`` limits the pairs to those cards (e.g. the set's nonlands)."""
    tally = PairTally()
    with gzip.open(path, "rt", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        col = {h: i for i, h in enumerate(header)}
        names = sorted(h[len("deck_"):] for h in header if h.startswith("deck_"))
        if keep is not None:
            names = [n for n in names if n in keep]
        deck_i = [col[f"deck_{n}"] for n in names]
        seen_i = [(col.get(f"opening_hand_{n}"), col.get(f"drawn_{n}")) for n in names]
        won = col["won"]
        for row in reader:
            deck, seen = [], set()
            for n, d, (o, w) in zip(names, deck_i, seen_i, strict=True):
                if row[d] not in ("0", ""):
                    deck.append(n)
                    if (o is not None and row[o] not in ("0", "")) or \
                            (w is not None and row[w] not in ("0", "")):
                        seen.add(n)
            tally.add(deck, seen, 1.0 if row[won] == "True" else 0.0)
    return tally
