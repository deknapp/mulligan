"""Running a game to completion with two agents."""

from __future__ import annotations

from dataclasses import dataclass, field

from .agents.base import Agent
from .cards.cube import DECKS
from .engine.card import CardSpec
from .engine.game import Game


@dataclass
class MatchResult:
    winner: int | None
    reason: str
    turns: int
    decisions: int
    life: list[int]
    seed: int
    on_the_play: int
    agent_names: tuple[str, str]
    log: list[str] = field(default_factory=list)

    @property
    def was_draw(self) -> bool:
        return self.winner is None


def play_game(
    agents: tuple[Agent, Agent],
    decks: tuple[list[CardSpec], list[CardSpec]],
    seed: int = 0,
    on_the_play: int = 0,
    max_turns: int = 60,
    keep_log: bool = False,
) -> MatchResult:
    game = Game(list(decks), names=(agents[0].name, agents[1].name), seed=seed,
                max_turns=max_turns, on_the_play=on_the_play)
    while not game.is_over:
        options = game.legal_actions()
        seat = game.state.decision_player
        choice = agents[seat].choose(game, options, seat)
        game.apply(choice)
    for seat, agent in enumerate(agents):
        agent.game_over(game, seat)
    result = game.result()
    return MatchResult(
        winner=result["winner"], reason=result["reason"], turns=result["turns"],
        decisions=result["decisions"], life=result["life"], seed=seed,
        on_the_play=on_the_play, agent_names=(agents[0].name, agents[1].name),
        log=list(game.state.log) if keep_log else [],
    )


def deck_by_name(name: str) -> list[CardSpec]:
    if name not in DECKS:
        raise KeyError(f"unknown deck {name!r}; known: {', '.join(sorted(DECKS))}")
    return DECKS[name]
