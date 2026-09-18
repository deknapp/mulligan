"""Self-play training of the learned agent's per-set correction.

The loop, each iteration:

1. Play ``games`` games in parallel. The learner samples its moves
   (``temperature`` > 0) and faces a league: the heuristic, or a frozen earlier
   version of itself. Decks come from a field of sealed decks of the set, so it
   learns the set, not one deck.
2. Each worker turns its games into a policy-gradient step (REINFORCE with a
   learned logistic value baseline) and returns only the summed gradient — a
   sparse dict — so nothing large crosses process boundaries.
3. Every ``eval_every`` iterations the greedy learner plays the heuristic on a
   fixed set of deck pairings, seats swapped so deck strength cancels. A new
   best is saved and joins the league.

Everything is plain Python on the CPU. A useful model for a set takes minutes,
not GPU-hours, because the heuristic already plays sound Magic and the model
only learns a correction.
"""

from __future__ import annotations

import math
import os
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .agents.heuristic import HeuristicAgent
from .agents.learned import MODEL_DIR, LearnedAgent, dot
from .arena import wilson
from .cards.sets import load_set
from .limited.build import build_deck
from .limited.pools import sealed_pool
from .match import play_game

TRAIN_POOL_SEED = 3_000_000
EVAL_POOL_SEED = 4_000_000


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _decks(set_code: str, n: int, base: int) -> list[list[str]]:
    data = load_set(set_code)
    return [[c.name for c in build_deck(sealed_pool(data, base + i), data).cards]
            for i in range(n)]


def _selfplay_chunk(set_code: str, decks: list[list[str]], w: dict, v: dict,
                    temperature: float, games: list[tuple], league: list[dict]):
    data = load_set(set_code)
    built = [[data.playable[n] for n in names] for names in decks]
    grad_w: dict[str, float] = defaultdict(float)
    grad_v: dict[str, float] = defaultdict(float)
    score = 0.0
    decisions = 0
    for deck_a, deck_b, seed, seat, opponent in games:
        learner = LearnedAgent(w, temperature=temperature, seed=seed, record=True,
                               value_weights=v)
        rival = (HeuristicAgent() if opponent < 0
                 else LearnedAgent(league[opponent], name="league"))
        agents = (learner, rival) if seat == 0 else (rival, learner)
        result = play_game(agents, (built[deck_a], built[deck_b]), seed=seed,
                           on_the_play=seed % 2)
        z = 0.5 if result.winner is None else float(result.winner == seat)
        score += z
        for feats, probs, chosen, sit in learner.trace:
            value = _sigmoid(dot(v, sit))
            advantage = z - value
            for k, x in sit.items():
                grad_v[k] += (z - value) * x
            scale = advantage / temperature
            for k, x in feats[chosen].items():
                grad_w[k] += scale * x
            for p, f in zip(probs, feats, strict=True):
                if p < 1e-4:
                    continue
                for k, x in f.items():
                    grad_w[k] -= scale * p * x
            decisions += 1
    return dict(grad_w), dict(grad_v), score, len(games), decisions


def _eval_chunk(set_code: str, decks: list[list[str]], w: dict, games: list[tuple]):
    """The greedy learner vs the heuristic. Each (deck pairing, seed) is played
    with the learner on each deck, so deck strength cancels."""
    data = load_set(set_code)
    built = [[data.playable[n] for n in names] for names in decks]
    scores = []
    for deck_a, deck_b, seed in games:
        for learner_deck in (0, 1):
            learner = LearnedAgent(w)
            agents = (learner, HeuristicAgent()) if learner_deck == 0 else (
                HeuristicAgent(), learner)
            result = play_game(agents, (built[deck_a], built[deck_b]), seed=seed,
                               on_the_play=seed % 2)
            scores.append(0.5 if result.winner is None else float(result.winner == learner_deck))
    return scores


@dataclass
class TrainLog:
    iterations: list[dict] = field(default_factory=list)
    best_rate: float = 0.5
    best_interval: tuple[float, float] = (0.0, 1.0)


def _split(items: list, parts: int) -> list[list]:
    size = max(1, math.ceil(len(items) / parts))
    return [items[i:i + size] for i in range(0, len(items), size)]


def train(set_code: str, iterations: int = 20, games: int = 2000, eval_games: int = 1000,
          eval_every: int = 2, lr: float = 0.5, lr_value: float = 0.05,
          temperature: float = 0.5, l2: float = 1e-4, n_decks: int = 150, seed: int = 0,
          workers: int | None = None, out: Path | None = None, log=print) -> TrainLog:
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    rng = random.Random(seed)
    train_decks = _decks(set_code, n_decks, TRAIN_POOL_SEED)
    eval_decks = _decks(set_code, 60, EVAL_POOL_SEED)
    eval_pairings = []
    erng = random.Random(12345)
    for k in range(max(1, eval_games // 2)):
        a, b = erng.sample(range(len(eval_decks)), 2)
        eval_pairings.append((a, b, 7_000_000 + k))
    w: dict[str, float] = {}
    v: dict[str, float] = {}
    league: list[dict] = []
    best_w: dict[str, float] = {}
    history = TrainLog()
    out = out or MODEL_DIR / f"{set_code.lower()}.json"
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for it in range(1, iterations + 1):
            batch = []
            for _ in range(games):
                a, b = rng.sample(range(n_decks), 2)
                opponent = -1 if not league or rng.random() < 0.5 else rng.randrange(len(league))
                batch.append((a, b, rng.randrange(1 << 30), rng.randrange(2), opponent))
            chunks = _split(batch, workers * 2)
            futures = [pool.submit(_selfplay_chunk, set_code, train_decks, w, v, temperature,
                                   chunk, league) for chunk in chunks]
            grad_w: dict[str, float] = defaultdict(float)
            grad_v: dict[str, float] = defaultdict(float)
            score = n = decisions = 0
            for future in futures:
                gw, gv, s, g, d = future.result()
                for k, x in gw.items():
                    grad_w[k] += x
                for k, x in gv.items():
                    grad_v[k] += x
                score += s
                n += g
                decisions += d
            for k, x in grad_w.items():
                w[k] = w.get(k, 0.0) * (1 - l2) + lr * x / n
            for k, x in grad_v.items():
                v[k] = v.get(k, 0.0) + lr_value * x / max(1, decisions)
            entry = {"iteration": it, "train_score": score / n, "features": len(w)}
            if it % eval_every == 0 or it == iterations:
                parts = _split(eval_pairings, workers * 2)
                scores = [s for f in [pool.submit(_eval_chunk, set_code, eval_decks, dict(w), p)
                                      for p in parts] for s in f.result()]
                rate = sum(scores) / len(scores)
                low, high = wilson(sum(scores), len(scores))
                entry.update(eval_rate=rate, eval_low=low, eval_high=high)
                if rate > history.best_rate:
                    history.best_rate, history.best_interval = rate, (low, high)
                    best_w = dict(w)
                    league.append(dict(w))
                    LearnedAgent(best_w, value_weights=v).save(out, meta={
                        "set": set_code, "iteration": it, "vs_heuristic": round(rate, 4),
                        "ci95": [round(low, 4), round(high, 4)], "eval_games": len(scores)})
            history.iterations.append(entry)
            log(entry)
    return history


# ------------------------------------------------ per-card value learning


def values_from_ratings(stats: dict, scale: float = 3.0, shrink: int = 400,
                        exclude_lands: bool = True, set_code: str | None = None
                        ) -> dict[str, float]:
    """Per-card value adjustments from simulated games-in-hand win rates.

    ``adjust = scale * z * n / (n + shrink)``: a card's win rate as a z-score
    across the set, shrunk toward zero when few games back it. ``stats`` maps
    name -> object with ``rate`` and ``games``.
    """
    data = load_set(set_code) if set_code else None
    rows = {n: s for n, s in stats.items() if s.games > 0 and not (
        exclude_lands and data is not None and data.playable[n].is_land)}
    total = sum(s.games for s in rows.values())
    mean = sum(s.rate * s.games for s in rows.values()) / total
    var = sum(s.games * (s.rate - mean) ** 2 for s in rows.values()) / total
    sd = math.sqrt(var) or 1.0
    return {n: round(scale * (s.rate - mean) / sd * s.games / (s.games + shrink), 3)
            for n, s in rows.items()}


def _values_eval_chunk(set_code: str, decks: list[list[str]], values_a: dict, values_b: dict,
                       games: list[tuple]) -> list[float]:
    data = load_set(set_code)
    built = [[data.playable[n] for n in names] for names in decks]
    scores = []
    for deck_a, deck_b, seed in games:
        for a_deck in (0, 1):
            a = HeuristicAgent("a", card_values=values_a)
            b = HeuristicAgent("b", card_values=values_b)
            agents = (a, b) if a_deck == 0 else (b, a)
            result = play_game(agents, (built[deck_a], built[deck_b]), seed=seed,
                               on_the_play=seed % 2)
            scores.append(0.5 if result.winner is None else float(result.winner == a_deck))
    return scores


def compare_values(set_code: str, values_a: dict, values_b: dict, pairings: int = 1000,
                   workers: int | None = None, seed: int = 0) -> tuple[float, float, float, int]:
    """Agent A (values_a) vs agent B (values_b), each on both decks of every
    pairing. Returns (A's score, CI low, CI high, games)."""
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    decks = _decks(set_code, 60, EVAL_POOL_SEED)
    rng = random.Random(9000 + seed)
    games = []
    for k in range(pairings):
        a, b = rng.sample(range(len(decks)), 2)
        games.append((a, b, 8_000_000 + seed * 100_000 + k))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_values_eval_chunk, set_code, decks, values_a, values_b, part)
                   for part in _split(games, workers * 3)]
        scores = [s for f in futures for s in f.result()]
    low, high = wilson(sum(scores), len(scores))
    return sum(scores) / len(scores), low, high, len(scores)


# ------------------------------------------- expert iteration (distillation)


def _search_chunk(set_code: str, decks: list[list[str]], games: list[tuple], rollouts: int,
                  candidates: int):
    """Games where a search agent plays the heuristic; returns the search
    agent's recorded decisions (features, heuristic scores, chosen index)."""
    from .agents.search import SearchAgent
    data = load_set(set_code)
    built = [[data.playable[n] for n in names] for names in decks]
    examples = []
    for deck_a, deck_b, seed, seat in games:
        searcher = SearchAgent(rollouts, candidates, seed=seed, record=True)
        agents = (searcher, HeuristicAgent()) if seat == 0 else (HeuristicAgent(), searcher)
        play_game(agents, (built[deck_a], built[deck_b]), seed=seed, on_the_play=seed % 2)
        examples.extend(searcher.trace)
    return examples


def generate_search_data(set_code: str, games: int = 400, rollouts: int = 4,
                         candidates: int = 3, seed: int = 0, workers: int | None = None,
                         n_decks: int = 150) -> list:
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    decks = _decks(set_code, n_decks, TRAIN_POOL_SEED)
    rng = random.Random(seed)
    jobs = [(*rng.sample(range(n_decks), 2), rng.randrange(1 << 30), rng.randrange(2))
            for _ in range(games)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_search_chunk, set_code, decks, part, rollouts, candidates)
                   for part in _split(jobs, workers * 3)]
        return [ex for f in futures for ex in f.result()]


def distill(examples: list, epochs: int = 20, lr: float = 0.05, l2: float = 1e-3,
            seed: int = 0) -> dict[str, float]:
    """Fit the linear correction so that squash(heuristic) + w . f picks what
    the search agent picked (softmax cross-entropy, plain SGD)."""
    from .agents.learned import squash
    w: dict[str, float] = {}
    rng = random.Random(seed)
    data = list(examples)
    for _ in range(epochs):
        rng.shuffle(data)
        for feats, scores, chosen in data:
            logits = [squash(h) + dot(w, f) for h, f in zip(scores, feats, strict=True)]
            top = max(logits)
            exps = [math.exp(x - top) for x in logits]
            total = sum(exps)
            grad: dict[str, float] = defaultdict(float)
            for k, x in feats[chosen].items():
                grad[k] += x
            for e, f in zip(exps, feats, strict=True):
                p = e / total
                if p < 1e-4:
                    continue
                for k, x in f.items():
                    grad[k] -= p * x
            for k, g in grad.items():
                w[k] = w.get(k, 0.0) * (1 - l2) + lr * g
    return {k: v for k, v in w.items() if abs(v) > 1e-4}


def evaluate_weights(set_code: str, w: dict, pairings: int = 1000,
                     workers: int | None = None) -> tuple[float, float, float, int]:
    """Greedy learned agent with weights ``w`` vs the heuristic, paired decks."""
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    decks = _decks(set_code, 60, EVAL_POOL_SEED)
    rng = random.Random(4242)
    games = [(*rng.sample(range(len(decks)), 2), 6_000_000 + k) for k in range(pairings)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_eval_chunk, set_code, decks, w, part)
                   for part in _split(games, workers * 3)]
        scores = [s for f in futures for s in f.result()]
    low, high = wilson(sum(scores), len(scores))
    return sum(scores) / len(scores), low, high, len(scores)
