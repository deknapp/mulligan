"""Deck-level validation: does the simulator rank real decks the way real
results do?

This is the test that matters for the simulator's purpose — helping a player
choose between decks. It takes real HOB decks from 17Lands' public games, each
with its actual record, plays every deck against a field of *other real decks*
in the simulator, and correlates simulated win rate with actual win rate.

Decks are drawn only from the drafts the deck model held out, so the same
decks can score the deck model too, and the two approaches are compared on
equal footing.
"""

from __future__ import annotations

import os
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from ..cards.cube import BASICS
from ..cards.sets import load_set
from ..deckmodel import DeckModel, load_games
from .seventeen import spearman

BASIC_BY_NAME = {spec.name: spec for spec in BASICS.values()}


@dataclass
class RealDeck:
    names: dict[str, int]
    wins: float
    games: int
    skill: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.games


def real_decks(set_code: str = "hob", fmt: str = "PremierDraft", min_games: int = 5,
               holdout: float = 0.1, seed: int = 0) -> list[RealDeck]:
    """Held-out real decks (same split as ``deckmodel.fit``) with their records."""
    names, rows = load_games(set_code, fmt)
    rng = random.Random(seed)
    groups = sorted({r.group for r in rows})
    test = set(rng.sample(groups, int(len(groups) * holdout)))
    decks: dict[tuple, list] = defaultdict(lambda: [0.0, 0, 0.0, None])
    for r in rows:
        if r.group not in test:
            continue
        d = decks[(r.group, tuple(r.cards))]
        d[0] += r.won
        d[1] += 1
        d[2] += r.skill
        d[3] = {names[j]: int(x) for j, x in r.cards}
    return [RealDeck(d[3], d[0], d[1], d[2] / d[1]) for d in decks.values()
            if d[1] >= min_games and 38 <= sum(d[3].values()) <= 42]


def _specs(set_code: str, deck: dict[str, int]) -> tuple[list, int, int]:
    """CardSpecs for a real deck; unplayable cards become a basic of its main
    color. Returns (cards, replaced, approximated)."""
    data = load_set(set_code)
    cards, missing, approx = [], 0, 0
    colors: dict[str, int] = defaultdict(int)
    for name, n in deck.items():
        spec = data.playable.get(name) or BASIC_BY_NAME.get(name)
        if spec is None:
            missing += n
            continue
        cards.extend([spec] * n)
        if spec.approximations:
            approx += n
        for symbol, k in spec.cost.pips:
            for part in symbol.split("/"):
                colors[part] += k * n
    main = max(colors, key=colors.get) if colors else "G"
    cards.extend([BASICS.get(main, BASICS["G"])] * missing)
    return cards, missing, approx


def _play(set_code: str, decks: list[dict[str, int]], jobs: list[tuple[int, int, int]],
          agent: str) -> list[tuple[int, float]]:
    from ..arena import make_agent
    from ..match import play_game
    built = [_specs(set_code, d)[0] for d in decks]
    out = []
    for i, j, seed in jobs:
        for seat in (0, 1):
            pair = (built[i], built[j]) if seat == 0 else (built[j], built[i])
            result = play_game((make_agent(agent, seed), make_agent(agent, seed + 1)), pair,
                               seed=seed, on_the_play=seed % 2)
            score = 0.5 if result.winner is None else float(result.winner == seat)
            out.append((i, score))
    return out


@dataclass
class DeckValidation:
    n_decks: int
    sim_rho: float
    model_rho: float
    skill_rho: float
    sim_rho_faithful: float
    n_faithful: int
    rows: list[tuple[float, float, float]]  # (simulated, model, actual)

    def summary(self) -> str:
        return (f"{self.n_decks} real decks (≥5 games each), Spearman vs actual win rate:\n"
                f"  simulator            {self.sim_rho:+.3f}\n"
                f"  17Lands deck model   {self.model_rho:+.3f}\n"
                f"  pilot skill alone    {self.skill_rho:+.3f}\n"
                f"  simulator, decks with ≤3 simplified cards ({self.n_faithful}): "
                f"{self.sim_rho_faithful:+.3f}")


def validate_simulator(set_code: str = "hob", n_decks: int = 200, opponents: int = 30,
                       agent: str = "heuristic", seed: int = 0,
                       workers: int | None = None) -> DeckValidation:
    rng = random.Random(seed)
    pool = real_decks(set_code)
    decks = rng.sample(pool, min(n_decks, len(pool)))
    names = [d.names for d in decks]
    jobs = []
    for i in range(len(decks)):
        for _ in range(opponents):
            j = rng.randrange(len(decks) - 1)
            j = j + 1 if j >= i else j
            jobs.append((i, j, rng.randrange(1 << 30)))
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    size = max(1, len(jobs) // (workers * 4))
    parts = [jobs[k:k + size] for k in range(0, len(jobs), size)]
    totals: dict[int, list[float]] = defaultdict(list)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for result in ex.map(_play, [set_code] * len(parts), [names] * len(parts), parts,
                             [agent] * len(parts)):
            for i, score in result:
                totals[i].append(score)
    model = DeckModel.load(set_code)
    sim = [sum(totals[i]) / len(totals[i]) for i in range(len(decks))]
    mod = [model.score(d.names) for d in decks]
    actual = [d.win_rate for d in decks]
    skill = [d.skill for d in decks]
    faithful = [i for i, d in enumerate(decks)
                if sum(_specs(set_code, d.names)[1:]) <= 3]
    return DeckValidation(
        len(decks), spearman(sim, actual), spearman(mod, actual), spearman(skill, actual),
        spearman([sim[i] for i in faithful], [actual[i] for i in faithful]) if len(
            faithful) > 2 else 0.0, len(faithful), list(zip(sim, mod, actual, strict=True)))
