"""Hidden information stays hidden.

A learning agent rewarded for winning will exploit any leak it can reach, so the
view's public surface is checked directly: the opponent's hand and both
libraries must be unreachable through it.
"""

from __future__ import annotations

from mulligan.agents import RandomAgent
from mulligan.engine.game import Game
from mulligan.engine.view import CardView, PermanentView, PlayerView
from mulligan.match import deck_by_name


def _game(seed: int = 7) -> Game:
    game = Game([deck_by_name("boros-aggro"), deck_by_name("golgari-grind")], seed=seed)
    agent = RandomAgent(seed)
    for _ in range(40):  # into the midgame, with cards in hands and on the battlefield
        if game.is_over:
            break
        seat = game.state.decision_player
        game.apply(agent.choose(PlayerView(game, seat), game.legal_actions()))
    return game


def test_view_shows_own_hand_but_not_the_opponents():
    game = _game()
    view = PlayerView(game, 0)
    mine = {c.id for c in view.hand()}
    assert mine == set(game.state.players[0].hand)
    for obj_id in game.state.players[1].hand:
        assert view.card(obj_id) is None
    assert view.hand_size(1) == len(game.state.players[1].hand)


def test_libraries_are_hidden_from_both_players():
    game = _game()
    for seat in (0, 1):
        view = PlayerView(game, seat)
        for owner in (0, 1):
            for obj_id in game.state.players[owner].library:
                assert view.card(obj_id) is None
            assert view.library_size(owner) == len(game.state.players[owner].library)


def test_no_public_method_returns_a_hidden_card():
    """Walk every public zone accessor and make sure only visible objects come back."""
    for seed in range(5):
        game = _game(seed)
        for seat in (0, 1):
            view = PlayerView(game, seat)
            opponent_hand = set(game.state.players[1 - seat].hand)
            libraries = set(game.state.players[0].library) | set(game.state.players[1].library)
            hidden = opponent_hand | libraries
            seen: list = view.hand()
            for owner in (0, 1):
                seen += view.battlefield(owner) + view.graveyard(owner) + view.exile(owner)
            assert all(isinstance(c, (CardView, PermanentView)) for c in seen)
            assert not {c.id for c in seen} & hidden


def test_view_reflects_the_live_game():
    """The view is a proxy, not a snapshot: it never goes stale mid-game."""
    game = Game([deck_by_name("mono-red-burn"), deck_by_name("dimir-control")], seed=1)
    view = PlayerView(game, 0)
    before = view.hand_size(0)
    game.state.players[0].life = 13
    assert view.life(0) == 13
    assert view.hand_size(0) == before
