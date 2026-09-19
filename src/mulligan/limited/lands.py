"""How many lands? Build each drafted pool with 16, 17 and 18 lands and play
every version against the same opponents on the same shuffles.

Because the versions of one deck meet identical opponents and seeds, the
difference between them is measured per deck and averaged: far tighter than
comparing separate runs. The builder decides what the extra or missing slot
is (its next-best spell, or one more basic).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..arena import Entry, gauntlet
from ..cards.sets import load_set
from .build import build_deck
from .draft import POD_SEED_BASE, draft_pod


@dataclass
class LandResult:
    counts: tuple[int, ...]
    win: dict[int, float]                               # mean win rate per land count
    diff: dict[int, tuple[float, float, float]]         # vs the baseline: mean, low, high
    decks: int
    games_per_version: int


def land_counts(set_code: str, ratings: dict[str, float], counts=(16, 17, 18),
                baseline: int = 17, decks: int = 48, opponents: int = 16,
                games_per_opponent: int = 4, seed: int = 0,
                workers: int | None = None) -> LandResult:
    data = load_set(set_code)
    pools = []
    pod = 0
    while len(pools) < decks + opponents:
        picks, _ = draft_pod(data, ratings, POD_SEED_BASE + 50_000 + seed * 1000 + pod)
        pools.extend(picks)
        pod += 1
    rng = random.Random(seed)
    rng.shuffle(pools)
    field_pools, test_pools = pools[:opponents], pools[opponents:opponents + decks]
    field = [Entry(f"f{i}", "heuristic", tuple(build_deck(p, data, ratings).cards))
             for i, p in enumerate(field_pools)]
    per_deck: dict[int, list[float]] = {c: [] for c in counts}
    games = 0
    for i, pool in enumerate(test_pools):
        variants = [Entry(f"{c}", "heuristic", tuple(build_deck(pool, data, ratings,
                                                                lands=c).cards))
                    for c in counts]
        result = gauntlet(variants, field, games_per_opponent=games_per_opponent,
                          seed=seed * 100_000 + i * 97, workers=workers)
        games = result.games[0]
        for c, wins, n in zip(counts, result.wins, result.games, strict=True):
            per_deck[c].append(wins / n)
    win = {c: sum(v) / len(v) for c, v in per_deck.items()}
    diff = {}
    for c in counts:
        d = [a - b for a, b in zip(per_deck[c], per_deck[baseline], strict=True)]
        mean = sum(d) / len(d)
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / max(1, len(d) - 1))
        half = 1.96 * sd / math.sqrt(len(d))
        diff[c] = (mean, mean - half, mean + half)
    return LandResult(tuple(counts), win, diff, len(test_pools), games)
