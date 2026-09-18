"""Many games between two (agent, deck) entries, with honest statistics.

``compare`` answers "which is better?" for either decks or agents:

* Decks: the same agent pilots both decks, and each deck is on the play in
  exactly half the games, so play/draw advantage cancels.
* Agents: give both entries the same deck. Games come in seat-swapped pairs on
  the same seed, so each agent sees the identical shuffles from both seats and
  luck cancels as far as it can.

Every result carries a Wilson score interval. A 54% win rate over 200 games is
not evidence of anything, and the report says so rather than leaving the reader
to guess.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from .agents import HeuristicAgent, RandomAgent
from .agents.base import Agent
from .engine.card import CardSpec
from .match import play_game

AGENTS = {
    "random": lambda seed: RandomAgent(seed, "random"),
    "heuristic": lambda seed: HeuristicAgent("heuristic"),
}
"""Agents by name. Workers build their own agents from a name and seed, so
nothing stateful crosses a process boundary."""


def register_agent(name: str, factory) -> None:
    AGENTS[name] = factory


_LEARNED_CACHE: dict[str, tuple[dict, dict]] = {}


def make_agent(name: str, seed: int) -> Agent:
    """Build an agent by name. ``learned:<set or path>`` loads a trained model."""
    if name.startswith("learned:"):
        from .agents.learned import LearnedAgent
        ref = name.split(":", 1)[1]
        if ref not in _LEARNED_CACHE:
            _LEARNED_CACHE[ref] = LearnedAgent.load_weights(ref)
        weights, value = _LEARNED_CACHE[ref]
        return LearnedAgent(weights, name=name, value_weights=value)
    if name not in AGENTS:
        raise KeyError(f"unknown agent {name!r}; known: {', '.join(sorted(AGENTS))}")
    return AGENTS[name](seed)


@dataclass(frozen=True)
class Entry:
    """One side of a comparison: who plays, and with what."""

    label: str
    agent: str
    deck: tuple[CardSpec, ...]


def wilson(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a win rate. Draws count as half a win."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class ArenaResult:
    a: str
    b: str
    games: int = 0
    a_wins: int = 0
    b_wins: int = 0
    draws: int = 0
    a_wins_on_play: int = 0
    a_games_on_play: int = 0
    a_wins_on_draw: int = 0
    a_games_on_draw: int = 0
    turns: list[int] = field(default_factory=list)

    @property
    def a_score(self) -> float:
        return self.a_wins + 0.5 * self.draws

    @property
    def a_rate(self) -> float:
        return self.a_score / self.games if self.games else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.a_score, self.games)

    @property
    def verdict(self) -> str:
        low, high = self.interval
        if low > 0.5:
            return f"{self.a} is better"
        if high < 0.5:
            return f"{self.b} is better"
        return "no clear difference yet (more games would narrow it)"

    def merge(self, other: ArenaResult) -> None:
        for name in ("games", "a_wins", "b_wins", "draws", "a_wins_on_play",
                     "a_games_on_play", "a_wins_on_draw", "a_games_on_draw"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.turns.extend(other.turns)

    def summary(self) -> str:
        low, high = self.interval
        play = (self.a_wins_on_play / self.a_games_on_play) if self.a_games_on_play else 0
        draw = (self.a_wins_on_draw / self.a_games_on_draw) if self.a_games_on_draw else 0
        mean_turns = sum(self.turns) / len(self.turns) if self.turns else 0
        return (
            f"{self.a} vs {self.b}: {self.games} games\n"
            f"  {self.a} wins {self.a_rate:.1%}  (95% CI {low:.1%}–{high:.1%})"
            f"  [{self.a_wins}-{self.b_wins}-{self.draws} W-L-D]\n"
            f"  {self.a} on the play {play:.1%}, on the draw {draw:.1%};"
            f" mean game length {mean_turns:.1f} turns\n"
            f"  verdict: {self.verdict}"
        )


def _play_block(a: Entry, b: Entry, seeds: list[int], max_turns: int) -> ArenaResult:
    """Each seed is played twice, seats swapped, so both entries see both seats
    and both sides of the play/draw coin."""
    result = ArenaResult(a.label, b.label)
    for seed in seeds:
        for a_seat in (0, 1):
            entries = (a, b) if a_seat == 0 else (b, a)
            agents = (make_agent(entries[0].agent, seed * 2),
                      make_agent(entries[1].agent, seed * 2 + 1))
            on_the_play = seed % 2
            game = play_game(agents, (list(entries[0].deck), list(entries[1].deck)),
                             seed=seed, on_the_play=on_the_play, max_turns=max_turns)
            result.games += 1
            result.turns.append(game.turns)
            a_on_play = on_the_play == a_seat
            if a_on_play:
                result.a_games_on_play += 1
            else:
                result.a_games_on_draw += 1
            if game.winner is None:
                result.draws += 1
            elif game.winner == a_seat:
                result.a_wins += 1
                if a_on_play:
                    result.a_wins_on_play += 1
                else:
                    result.a_wins_on_draw += 1
            else:
                result.b_wins += 1
    return result


def compare(a: Entry, b: Entry, games: int = 1000, seed: int = 0,
            workers: int | None = None, max_turns: int = 60) -> ArenaResult:
    """Play ``games`` games (rounded up to an even number) between two entries."""
    pairs = max(1, math.ceil(games / 2))
    seeds = list(range(seed, seed + pairs))
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    total = ArenaResult(a.label, b.label)
    if workers <= 1 or pairs < 8:
        total.merge(_play_block(a, b, seeds, max_turns))
        return total
    chunk = max(1, math.ceil(pairs / (workers * 4)))
    blocks = [seeds[i:i + chunk] for i in range(0, len(seeds), chunk)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_play_block, a, b, block, max_turns) for block in blocks]
        for future in futures:
            total.merge(future.result())
    return total


# ------------------------------------------------------------------ gauntlet


@dataclass
class GauntletResult:
    """Several candidate decks, each played against the same field."""

    labels: list[str]
    games: list[int]
    wins: list[float]
    # outcomes[i][k]: candidate i's score in the k-th game slot. Slot k is the
    # same field deck, seed and seat for every candidate, which is what makes
    # the paired comparison valid.
    outcomes: list[list[float]]

    def rate(self, i: int) -> float:
        return self.wins[i] / self.games[i] if self.games[i] else 0.0

    def interval(self, i: int) -> tuple[float, float]:
        return wilson(self.wins[i], self.games[i])

    def paired_difference(self, i: int, j: int) -> tuple[float, float, float]:
        """Mean of (i's score - j's score) over shared slots, with a 95% CI."""
        diffs = [a - b for a, b in zip(self.outcomes[i], self.outcomes[j], strict=True)]
        n = len(diffs)
        mean = sum(diffs) / n
        var = sum((d - mean) ** 2 for d in diffs) / max(1, n - 1)
        half = 1.96 * math.sqrt(var / n)
        return mean, mean - half, mean + half

    def summary(self) -> str:
        lines = []
        for i, label in enumerate(self.labels):
            low, high = self.interval(i)
            lines.append(f"  {label}: {self.rate(i):.1%} vs the field "
                         f"(95% CI {low:.1%}–{high:.1%}, {self.games[i]} games)")
        if len(self.labels) >= 2:
            mean, low, high = self.paired_difference(0, 1)
            if low > 0:
                verdict = f"{self.labels[0]} is better"
            elif high < 0:
                verdict = f"{self.labels[1]} is better"
            else:
                verdict = "no clear difference yet (more games would narrow it)"
            lines.append(f"  difference {self.labels[0]} − {self.labels[1]}: {mean:+.1%} "
                         f"(95% CI {low:+.1%} to {high:+.1%})  → {verdict}")
        return "\n".join(lines)


def _gauntlet_block(candidate: Entry, field: list[Entry], seeds: list[int],
                    max_turns: int) -> list[float]:
    scores = []
    for opponent in field:
        for seed in seeds:
            for seat in (0, 1):
                entries = (candidate, opponent) if seat == 0 else (opponent, candidate)
                agents = (make_agent(entries[0].agent, seed * 2),
                          make_agent(entries[1].agent, seed * 2 + 1))
                game = play_game(agents, (list(entries[0].deck), list(entries[1].deck)),
                                 seed=seed, on_the_play=seed % 2, max_turns=max_turns)
                scores.append(0.5 if game.winner is None else float(game.winner == seat))
    return scores


def gauntlet(candidates: list[Entry], field: list[Entry], games_per_opponent: int = 10,
             seed: int = 0, workers: int | None = None, max_turns: int = 60) -> GauntletResult:
    """Every candidate plays every field deck ``games_per_opponent`` times
    (rounded up to even, seats swapped), on shared seeds."""
    seeds = list(range(seed, seed + max(1, math.ceil(games_per_opponent / 2))))
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    jobs = [(i, j) for i in range(len(candidates)) for j in range(len(field))]
    results: dict[tuple[int, int], list[float]] = {}
    if workers <= 1:
        for i, j in jobs:
            results[(i, j)] = _gauntlet_block(candidates[i], [field[j]], seeds, max_turns)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {(i, j): pool.submit(_gauntlet_block, candidates[i], [field[j]], seeds,
                                           max_turns) for i, j in jobs}
            for key, future in futures.items():
                results[key] = future.result()
    outcomes = [[s for j in range(len(field)) for s in results[(i, j)]]
                for i in range(len(candidates))]
    return GauntletResult(labels=[c.label for c in candidates],
                          games=[len(o) for o in outcomes],
                          wins=[sum(o) for o in outcomes], outcomes=outcomes)
