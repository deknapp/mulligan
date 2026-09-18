"""A search agent: try the most promising moves in sampled futures.

For each of the heuristic's top few candidate actions, the agent copies the
game, re-deals everything it cannot see (``determinize``: the opponent's hand
and library, its own library order), plays the candidate, and plays the game
out with the heuristic on both sides. The candidate with the best average
result is chosen.

This is far slower than the heuristic and is not meant to be shipped as a
player. It is a *teacher*: its choices are better-informed than the
heuristic's, and a lightweight learned correction can be trained to imitate
them (expert iteration). It reads the underlying game only to copy it; every
hidden card is re-dealt before anything is simulated, so it does not use
information its seat could not have. (It does know which cards remain in the
opponent's deck, which a real player would not: a documented simplification.)
"""

from __future__ import annotations

import random

from ..engine import actions as act
from ..engine.game import clone_game, determinize
from ..engine.view import PlayerView
from .base import Agent
from .heuristic import HeuristicAgent

HEURISTIC_ONLY = ("mulligan", "bottom", "discard")


def playout(game, seat: int, max_decisions: int = 3000) -> float:
    agents = (HeuristicAgent(), HeuristicAgent())
    for _ in range(max_decisions):
        if game.is_over:
            break
        who = game.state.decision_player
        game.apply(agents[who].choose(PlayerView(game, who), game.legal_actions()))
    if not game.is_over or game.state.winner is None:
        return 0.5
    return float(game.state.winner == seat)


class SearchAgent(Agent):
    def __init__(self, rollouts: int = 6, candidates: int = 3, seed: int = 0,
                 name: str = "search", record: bool = False):
        self.name = name
        self.rollouts = rollouts
        self.candidates = candidates
        self.rng = random.Random(seed)
        self.heuristic = HeuristicAgent()
        self.record = record
        # Per searched decision: (option features, heuristic scores, chosen index).
        self.trace: list[tuple[list[dict], list[float], int]] = []

    def evaluate(self, view: PlayerView, options: list[act.Action]) -> list[tuple[int, float]]:
        """(option index, mean playout result) for the top candidates."""
        if view.pending == "attackers":
            self.heuristic._plan = self.heuristic._plan_attacks(view)
        elif view.pending == "blockers":
            self.heuristic._plan = self.heuristic._plan_blocks(view)
        scores = [self.heuristic.score(view, a) for a in options]
        order = sorted(range(len(options)), key=lambda i: -scores[i])[:self.candidates]
        results = []
        game = view._game  # copied, then re-dealt: see the module docstring
        for i in order:
            total = 0.0
            for _ in range(self.rollouts):
                sim = clone_game(game)
                determinize(sim, view.seat, self.rng)
                sim.apply(options[i])
                total += playout(sim, view.seat)
            results.append((i, total / self.rollouts))
        return results

    def choose(self, view: PlayerView, options: list[act.Action]) -> act.Action:
        if view.pending in HEURISTIC_ONLY or len(options) == 1:
            return self.heuristic.choose(view, options)
        results = self.evaluate(view, options)
        # Ties (and near-ties, within one rollout's worth) go to the heuristic's pick.
        best_i, best_v = results[0]
        for i, v in results[1:]:
            if v > best_v + 1.0 / self.rollouts:
                best_i, best_v = i, v
        if self.record:
            from .features import action_features, situation
            sit = situation(view)
            self.trace.append(([action_features(view, a, sit) for a in options],
                               [self.heuristic.score(view, a) for a in options], best_i))
        return options[best_i]
