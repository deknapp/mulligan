"""Running a game to completion with two agents."""

from __future__ import annotations

from dataclasses import dataclass, field

from .agents.base import Agent
from .cards.cube import DECKS
from .engine.card import CardSpec
from .engine.game import Game
from .engine.view import PlayerView


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
    # Names of the cards each seat had in hand at some point (opening hand or drawn).
    seen: tuple[list[str], list[str]] = ((), ())

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
    views = (PlayerView(game, 0), PlayerView(game, 1))
    while not game.is_over:
        options = game.legal_actions()
        seat = game.state.decision_player
        choice = agents[seat].choose(views[seat], options)
        game.apply(choice)
    for seat, agent in enumerate(agents):
        agent.game_over(views[seat], game.state.winner)
    result = game.result()
    return MatchResult(
        winner=result["winner"], reason=result["reason"], turns=result["turns"],
        decisions=result["decisions"], life=result["life"], seed=seed,
        on_the_play=on_the_play, agent_names=(agents[0].name, agents[1].name),
        log=list(game.state.log) if keep_log else [],
        seen=tuple([game.state.objects[i].name for i in p.seen]  # type: ignore[arg-type]
                   for p in game.state.players),
    )


def deck_by_name(name: str) -> list[CardSpec]:
    if name not in DECKS:
        raise KeyError(f"unknown deck {name!r}; known: {', '.join(sorted(DECKS))}")
    return DECKS[name]
