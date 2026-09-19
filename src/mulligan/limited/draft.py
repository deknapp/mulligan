"""Draft: eight bots pass packs, build what they drafted, and play it.

A pod opens three Play Boosters per drafter and passes them left, right, left.
Each bot takes the card with the best

    rating + commitment bonus + personal taste

where the commitment bonus grows with the share of the bot's picks so far in
the card's colors, and grows as the draft goes on (early picks take power,
late picks stay in lane). Personal taste is a small random per-bot, per-color
lean fixed for the draft, so eight bots don't all fight over the same color
and different drafts come out differently, as they do with people.

The drafted pools are built with the same deckbuilder as sealed and played
against each other. What comes out are 17Lands-shaped numbers for a set nobody
has played yet: games-in-hand win rate per card, average pick taken at, and
records by color pair.
"""

from __future__ import annotations

import math
import os
import random
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from ..cards.sets import SetData, load_set
from .build import build_deck
from .pools import booster
from .rating import static_rating

DRAFTERS = 8
PACKS = 3
COLORS = "WUBRG"
POD_SEED_BASE = 3_000_000


def card_colors(data: SetData, name: str) -> frozenset[str]:
    spec = data.playable.get(name)
    if spec is None:
        return frozenset(data.entries.get(name, {}).get("color_identity", ()))
    colors: set[str] = set()
    for symbol, _ in spec.cost.pips:
        parts = [p for p in symbol.split("/") if p in COLORS]
        if len(parts) == 1:
            colors.add(parts[0])
    if not colors:  # hybrid-only costs: either color works; count the first
        for symbol, _ in spec.cost.pips:
            parts = [p for p in symbol.split("/") if p in COLORS]
            if parts:
                colors.add(parts[0])
                break
    return frozenset(colors)


@dataclass
class Drafter:
    taste: dict[str, float]
    picks: list[str] = field(default_factory=list)
    weight: Counter = field(default_factory=Counter)  # rating drafted per color

    def value(self, name: str, colors: frozenset[str], rating: float, pick: int,
              total: int) -> float:
        if not colors:
            return rating
        committed = sum(self.weight.values())
        share = (sum(self.weight[c] for c in colors) / len(colors) / committed
                 if committed else 0.5)
        # 0 at the first pick, ~6 rating points by the last: late picks stay in lane.
        pull = 6.0 * min(1.0, pick / (0.6 * total))
        return rating + pull * (share - 0.35) * 2 + sum(self.taste[c] for c in colors)

    def take(self, name: str, colors: frozenset[str], rating: float) -> None:
        self.picks.append(name)
        for c in colors:
            self.weight[c] += max(0.5, rating) / len(colors)


def draft_pod(data: SetData, ratings: dict[str, float], seed: int,
              drafters: int = DRAFTERS, packs: int = PACKS,
              taste: float = 1.5) -> tuple[list[list[str]], dict[str, list[int]]]:
    """Run one pod. Returns each drafter's picks and, per card, the pick
    numbers (1-based within its pack) at which it was taken."""
    rng = random.Random(seed)
    bots = [Drafter({c: rng.gauss(0, taste) for c in COLORS}) for _ in range(drafters)]
    colors_of: dict[str, frozenset[str]] = {}
    taken_at: dict[str, list[int]] = defaultdict(list)
    for round_ in range(packs):
        opened = [booster(data, rng) for _ in range(drafters)]
        size = len(opened[0])
        direction = 1 if round_ % 2 == 0 else -1
        for pick in range(size):
            for i, bot in enumerate(bots):
                pack = opened[i]
                if not pack:
                    continue
                total = packs * size
                done = round_ * size + pick

                def score(name: str) -> float:
                    if name not in colors_of:
                        colors_of[name] = card_colors(data, name)
                    r = ratings.get(name, -5.0)  # unplayable cards: last picks
                    return bot.value(name, colors_of[name], r, done, total)

                choice = max(pack, key=score)
                pack.remove(choice)
                bot.take(choice, colors_of[choice], ratings.get(choice, -5.0))
                taken_at[choice].append(pick + 1)
            opened = [opened[(i - direction) % drafters] for i in range(drafters)]
    return [b.picks for b in bots], taken_at


@dataclass
class DraftStats:
    set_code: str
    pods: int
    games: int
    card_games: Counter
    card_wins: Counter
    taken_at: dict[str, list[int]]
    decks: list[tuple[str, list[str]]]      # (color pair, 40 card names)
    records: dict[str, list[float]]         # color pair -> [wins, games]

    def gih(self, name: str) -> float:
        return self.card_wins[name] / self.card_games[name] if self.card_games[name] else 0.0

    def ata(self, name: str) -> float:
        picks = self.taken_at.get(name, [])
        return sum(picks) / len(picks) if picks else 0.0


def _decks_from_pods(set_code: str, pods: list[int],
                     ratings: dict[str, float]) -> tuple[list, dict]:
    data = load_set(set_code)
    decks, taken = [], defaultdict(list)
    for seed in pods:
        pools, taken_at = draft_pod(data, ratings, seed)
        for name, picks in taken_at.items():
            taken[name].extend(picks)
        for pool in pools:
            deck = build_deck(pool, data, ratings)
            decks.append((deck.colors, [c.name for c in deck.cards]))
    return decks, dict(taken)


def simulate_draft(set_code: str, pods: int = 25, games: int = 20000, seed: int = 0,
                   agent: str = "heuristic", workers: int | None = None,
                   ratings: dict[str, float] | None = None) -> DraftStats:
    """Draft ``pods`` pods of eight, build every pool, and play ``games``
    games between random pairs of the drafted decks."""
    data = load_set(set_code)
    if ratings is None:
        from .release_day import rating_points, sim_ratings
        ratings = rating_points(data, sim_ratings(set_code))
    ratings = {**{n: static_rating(s) for n, s in data.playable.items()}, **ratings}
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    seeds = [POD_SEED_BASE + seed * 10_000 + i for i in range(pods)]
    decks: list = []
    taken: dict[str, list[int]] = defaultdict(list)
    parts = [seeds[i::workers] for i in range(workers) if seeds[i::workers]]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for d, t in ex.map(_decks_from_pods, [set_code] * len(parts), parts,
                           [ratings] * len(parts)):
            decks.extend(d)
            for name, picks in t.items():
                taken[name].extend(picks)
    rng = random.Random(seed)
    pairings = [(*rng.sample(range(len(decks)), 2), seed * 1_000_003 + k)
                for k in range(games)]
    chunk = max(1, math.ceil(len(pairings) / (workers * 4)))
    chunks = [pairings[i:i + chunk] for i in range(0, len(pairings), chunk)]
    names = [d[1] for d in decks]
    card_games: Counter = Counter()
    card_wins: Counter = Counter()
    records: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for g, w, res in ex.map(_play_with_results, [set_code] * len(chunks),
                                [names] * len(chunks), chunks, [agent] * len(chunks)):
            card_games.update(g)
            card_wins.update(w)
            for a, b, score in res:
                for idx, s in ((a, score), (b, 1 - score)):
                    rec = records[decks[idx][0]]
                    rec[0] += s
                    rec[1] += 1
    return DraftStats(set_code, pods, games, card_games, card_wins, dict(taken), decks,
                      dict(records))


def _play_with_results(set_code: str, decks: list[list[str]],
                       pairings: list[tuple[int, int, int]], agent: str):
    """``_play_chunk`` plus each game's result, for per-archetype records."""
    from ..arena import make_agent
    from ..match import play_game
    data = load_set(set_code)
    built = [[data.playable[n] for n in names] for names in decks]
    games: Counter = Counter()
    wins: Counter = Counter()
    results = []
    for a, b, seed in pairings:
        result = play_game((make_agent(agent, seed * 2), make_agent(agent, seed * 2 + 1)),
                           (built[a], built[b]), seed=seed, on_the_play=seed % 2)
        score = 0.5 if result.winner is None else float(result.winner == 0)
        results.append((a, b, score))
        for seat, s in ((0, score), (1, 1 - score)):
            for name in set(result.seen[seat]):
                games[name] += 1
                wins[name] += s
    return games, wins, results

