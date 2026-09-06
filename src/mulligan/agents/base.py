"""The agent interface, and the random baseline.

An agent is handed the game and the list of actions the engine has already
verified as legal. It returns one of them. It cannot do anything else — no
mutating the state, no inventing moves — which is what makes a win by an agent
mean something.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from ..engine import actions as act

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..engine.game import Game


class Agent:
    """Base class. Subclasses implement ``choose``."""

    name = "agent"

    def choose(self, game: Game, options: list[act.Action], seat: int) -> act.Action:
        raise NotImplementedError  # pragma: no cover - abstract

    def game_over(self, game: Game, seat: int) -> None:
        """Optional hook, for agents that keep state across a game."""


class RandomAgent(Agent):
    """Uniformly random legal play.

    This is the floor. Any agent that cannot beat it is not playing Magic, and
    every reported win rate is quoted against it as well as against the
    heuristic, because "beat random" and "beat something that curves out" are
    very different bars.
    """

    def __init__(self, seed: int | None = None, name: str = "random"):
        self.rng = random.Random(seed)
        self.name = name

    def choose(self, game: Game, options: list[act.Action], seat: int) -> act.Action:
        return self.rng.choice(options)
