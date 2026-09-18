"""Engine invariants: the properties the whole benchmark depends on."""

from __future__ import annotations

import pytest

from mulligan.agents import RandomAgent
from mulligan.engine import actions as act
from mulligan.engine.game import Game, IllegalAction
from mulligan.engine.view import PlayerView
from mulligan.match import deck_by_name, play_game

DECK_NAMES = ["boros-aggro", "dimir-control", "gruul-midrange", "azorius-skies",
              "golgari-grind", "mono-red-burn"]


@pytest.mark.parametrize("seed", range(30))
def test_random_games_terminate(seed: int):
    """The engine always reaches a result, and never offers an empty choice."""
    left = deck_by_name(DECK_NAMES[seed % len(DECK_NAMES)])
    right = deck_by_name(DECK_NAMES[(seed + 3) % len(DECK_NAMES)])
    result = play_game((RandomAgent(seed, "A"), RandomAgent(seed + 5000, "B")),
                       (left, right), seed=seed, on_the_play=seed % 2)
    assert result.turns >= 1
    assert result.winner in (0, 1, None)


def test_games_are_deterministic():
    """Same seed, same agents, same game — otherwise nothing is reproducible.

    Agents carry their own RNG state across games on purpose, so a replay has
    to rebuild them; reusing one agent object would replay a different game and
    that is the correct behaviour, not a bug.
    """

    def run():
        return play_game((RandomAgent(1, "A"), RandomAgent(2, "B")),
                         (deck_by_name("boros-aggro"), deck_by_name("golgari-grind")),
                         seed=42, keep_log=True)

    first, second = run(), run()
    assert first.log == second.log
    assert (first.winner, first.turns, first.life) == (second.winner, second.turns, second.life)


def test_engine_rejects_actions_it_did_not_offer():
    """An agent cannot invent a move. This is what makes a win mean something."""
    game = Game([deck_by_name("boros-aggro"), deck_by_name("dimir-control")], seed=3)
    offered = game.legal_actions()
    bogus = act.CastSpell(card_id=99999)
    assert bogus not in offered
    with pytest.raises(IllegalAction):
        game.apply(bogus)


@pytest.mark.parametrize("seed", range(10))
def test_never_offers_a_dead_choice(seed: int):
    """Every offered action is applicable, and a live game always has one."""
    game = Game([deck_by_name("gruul-midrange"), deck_by_name("azorius-skies")], seed=seed)
    agent = RandomAgent(seed)
    while not game.is_over:
        options = game.legal_actions()
        assert options, "a running game must offer at least one action"
        assert len(set(options)) == len(options), "duplicate actions in the choice list"
        game.apply(agent.choose(PlayerView(game, game.state.decision_player), options))


def test_life_totals_are_consistent_with_the_result():
    for seed in range(20):
        result = play_game((RandomAgent(seed), RandomAgent(seed + 1)),
                           (deck_by_name("mono-red-burn"), deck_by_name("dimir-control")),
                           seed=seed)
        if result.winner is not None:
            assert result.life[1 - result.winner] <= 0 or "empty library" in result.reason


def test_a_cloned_game_is_independent_of_the_original():
    from mulligan.engine.game import clone_game
    game = Game([deck_by_name("boros-aggro"), deck_by_name("golgari-grind")], seed=4)
    agent = RandomAgent(1)
    for _ in range(60):
        game.apply(agent.choose(PlayerView(game, game.state.decision_player),
                                game.legal_actions()))
    before = (game.state.turn, [p.life for p in game.state.players],
              [list(p.hand) for p in game.state.players], game.legal_actions())
    copy = clone_game(game)
    for _ in range(40):
        if copy.is_over:
            break
        copy.apply(agent.choose(PlayerView(copy, copy.state.decision_player),
                                copy.legal_actions()))
    after = (game.state.turn, [p.life for p in game.state.players],
             [list(p.hand) for p in game.state.players], game.legal_actions())
    assert before == after


def test_determinize_only_reshuffles_what_the_viewer_cannot_see():
    import random

    from mulligan.engine.game import clone_game, determinize
    game = Game([deck_by_name("boros-aggro"), deck_by_name("golgari-grind")], seed=9)
    copy = clone_game(game)
    determinize(copy, 0, random.Random(3))
    mine, theirs = copy.state.players
    assert mine.hand == game.state.players[0].hand
    assert sorted(mine.library) == sorted(game.state.players[0].library)
    assert len(theirs.hand) == len(game.state.players[1].hand)
    assert sorted(theirs.hand + theirs.library) == sorted(
        game.state.players[1].hand + game.state.players[1].library)
    assert all(copy.state.objects[i].zone == "hand" for i in theirs.hand)
