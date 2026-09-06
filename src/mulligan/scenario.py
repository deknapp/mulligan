"""Build a specific board position directly, without playing toward it.

Tests and puzzles both need to say "it is turn 6, you have these two creatures
and this card in hand, find the win". Reaching such a position by playing is
impossible to control, so positions are constructed instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cards.cube import BASICS, CUBE
from .engine.card import CardSpec
from .engine.game import Game
from .engine.types import Step


@dataclass
class Side:
    """One player's half of a constructed position."""

    life: int = 20
    battlefield: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    graveyard: list[str] = field(default_factory=list)
    library: list[str] = field(default_factory=list)
    lands: dict[str, int] = field(default_factory=dict)
    tapped: list[str] = field(default_factory=list)
    summoning_sick: list[str] = field(default_factory=list)


def build_scenario(
    you: Side,
    opponent: Side,
    step: Step = Step.PRECOMBAT_MAIN,
    turn: int = 5,
    active: int = 0,
    pool: dict[str, CardSpec] | None = None,
    filler: str = "Forest",
    library_size: int = 20,
) -> Game:
    """Construct a position. Seat 0 is ``you``.

    Libraries are padded with a filler card so that drawing does not end the
    game, unless an explicit library is given.
    """
    pool = pool or CUBE
    game = Game([[pool[filler]] * library_size, [pool[filler]] * library_size],
                names=("you", "opponent"), seed=0, max_turns=999)
    state = game.state
    for player in state.players:
        player.hand.clear()
        player.battlefield.clear()
        player.library.clear()
        player.graveyard.clear()
    state.objects.clear()
    state.stack.clear()

    for seat, side in enumerate((you, opponent)):
        player = state.players[seat]
        player.life = side.life
        player.lands_played = 0
        for name, count in side.lands.items():
            for _ in range(count):
                obj = game._new_object(BASICS[name], seat)
                game._put_onto_battlefield(obj, seat)
                obj.summoning_sick = False
        for name in side.battlefield:
            obj = game._new_object(pool[name], seat)
            game._put_onto_battlefield(obj, seat)
            obj.summoning_sick = name in side.summoning_sick
            obj.tapped = name in side.tapped
        for zone, names in (("hand", side.hand), ("graveyard", side.graveyard)):
            for name in names:
                obj = game._new_object(pool[name], seat)
                obj.zone = zone
                player.zone(zone).append(obj.id)
        library = side.library or [filler] * library_size
        for name in library:
            obj = game._new_object(pool[name], seat)
            obj.zone = "library"
            player.library.append(obj.id)

    game._trigger_queue.clear()
    state.turn = turn
    state.active = active
    state.priority = active
    state.decision_player = active
    state.pending = "priority"
    state.passes = 0
    state.step = step
    state.mulligan_decided = [True, True]
    state.blockers_done = False
    state.attackers_declared = []
    state.blocks_declared = []
    state.log.clear()
    game.advance()
    return game
