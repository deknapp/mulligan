"""Card ratings from self-play: "games in hand" win rates, before any human
has played the set.

Open many sealed pools, build each into a deck, and play them against each
other. For every card, count the games in which it reached its owner's hand
(opening hand or drawn) and how many of those its owner won. That is the same
statistic as 17Lands' GIH WR, which is what makes the two comparable (see
``validation.seventeen``).
"""

from __future__ import annotations

import math
import os
import random
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from ..arena import make_agent
from ..cards.sets import load_set
from ..match import play_game
from .build import build_deck
from .pools import sealed_pool

POOL_SEED_BASE = 2_000_000


@dataclass
class CardStats:
    games: int = 0
    wins: float = 0.0
    decks: int = 0

    @property
    def rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def _play_chunk(set_code: str, decks: list[list[str]], pairings: list[tuple[int, int, int]],
                agent: str) -> tuple[Counter, Counter]:
    data = load_set(set_code)
    built = [[data.playable[n] for n in names] for names in decks]
    games: Counter = Counter()
    wins: Counter = Counter()
    for a, b, seed in pairings:
        result = play_game((make_agent(agent, seed * 2), make_agent(agent, seed * 2 + 1)),
                           (built[a], built[b]), seed=seed, on_the_play=seed % 2)
        for seat in (0, 1):
            score = 0.5 if result.winner is None else float(result.winner == seat)
            for name in set(result.seen[seat]):
                games[name] += 1
                wins[name] += score
    return games, wins


def simulate_ratings(set_code: str, n_decks: int = 200, n_games: int = 20000, seed: int = 0,
                     agent: str = "heuristic", workers: int | None = None,
                     ratings: dict[str, float] | None = None) -> dict[str, CardStats]:
    data = load_set(set_code)
    decks = []
    for i in range(n_decks):
        deck = build_deck(sealed_pool(data, POOL_SEED_BASE + seed + i), data, ratings)
        decks.append([c.name for c in deck.cards])
    rng = random.Random(seed)
    pairings = []
    for k in range(n_games):
        a, b = rng.sample(range(n_decks), 2)
        pairings.append((a, b, seed * 1_000_003 + k))
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    chunk = max(1, math.ceil(len(pairings) / (workers * 4)))
    chunks = [pairings[i:i + chunk] for i in range(0, len(pairings), chunk)]
    games: Counter = Counter()
    wins: Counter = Counter()
    if workers <= 1:
        for part in chunks:
            g, w = _play_chunk(set_code, decks, part, agent)
            games.update(g)
            wins.update(w)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for g, w in pool.map(_play_chunk, [set_code] * len(chunks), [decks] * len(chunks),
                                 chunks, [agent] * len(chunks)):
                games.update(g)
                wins.update(w)
    in_decks = Counter(name for deck in decks for name in set(deck))
    return {name: CardStats(games[name], wins[name], in_decks[name]) for name in games}
