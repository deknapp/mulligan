"""The heuristic baseline and the arena.

The heuristic's tests are behavioural: situations with one correct play, checked
on constructed boards. The arena's tests check the statistics and the
seat-swapping, since a biased harness would make every comparison meaningless.
"""

from __future__ import annotations

from mulligan.agents import HeuristicAgent
from mulligan.arena import Entry, compare, wilson
from mulligan.engine import actions as act
from mulligan.engine.types import Step
from mulligan.engine.view import PlayerView
from mulligan.match import deck_by_name
from mulligan.scenario import Side, build_scenario


def _choose(game, agent=None):
    agent = agent or HeuristicAgent()
    view = PlayerView(game, game.state.decision_player)
    return agent.choose(view, game.legal_actions())


def test_removal_goes_at_the_biggest_threat_not_our_own_creature():
    game = build_scenario(
        Side(battlefield=["Grizzly Bears"], hand=["Lightning Bolt"], lands={"R": 1}),
        Side(battlefield=["Savannah Lions", "Wind Drake"]),
    )
    choice = _choose(game)
    assert isinstance(choice, act.CastSpell)
    target = game.object_by_id(choice.targets[0].id)
    assert target.name == "Wind Drake"


def test_burn_goes_face_when_it_is_lethal():
    game = build_scenario(
        Side(hand=["Lightning Bolt"], lands={"R": 1}),
        Side(life=3, battlefield=["Serra Angel"]),
    )
    choice = _choose(game)
    assert isinstance(choice, act.CastSpell)
    assert choice.targets[0].kind == "player" and choice.targets[0].id == 1


def test_does_not_attack_into_a_free_block():
    game = build_scenario(
        Side(battlefield=["Grizzly Bears"]),
        Side(battlefield=["Giant Spider"]),
        step=Step.BEGIN_COMBAT,
    )
    while game.state.pending != "attackers":
        game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))
    assert isinstance(_choose(game), act.FinishDeclaring)


def test_attacks_into_an_empty_board():
    game = build_scenario(Side(battlefield=["Grizzly Bears"]), Side(), step=Step.BEGIN_COMBAT)
    while game.state.pending != "attackers":
        game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))
    assert isinstance(_choose(game), act.DeclareAttacker)


def test_plays_a_land_before_anything_else():
    game = build_scenario(Side(hand=["Forest", "Grizzly Bears"], lands={"G": 1}), Side())
    assert isinstance(_choose(game), act.PlayLand)


def test_heuristic_beats_random_decisively():
    deck = tuple(deck_by_name("gruul-midrange"))
    result = compare(Entry("heuristic", "heuristic", deck), Entry("random", "random", deck),
                     games=60, workers=1)
    assert result.a_rate > 0.75, result.summary()


def test_arena_balances_seats_and_play_draw():
    deck = tuple(deck_by_name("boros-aggro"))
    result = compare(Entry("a", "random", deck), Entry("b", "random", deck), games=40,
                     workers=1)
    assert result.games == 40
    assert result.a_games_on_play == result.a_games_on_draw == 20
    assert result.a_wins + result.b_wins + result.draws == 40


def test_parallel_and_serial_arenas_agree():
    """Same seeds, same games, whatever the worker count."""
    deck_a, deck_b = tuple(deck_by_name("mono-red-burn")), tuple(deck_by_name("azorius-skies"))
    a, b = Entry("burn", "heuristic", deck_a), Entry("skies", "heuristic", deck_b)
    serial = compare(a, b, games=32, workers=1)
    parallel = compare(a, b, games=32, workers=2)
    assert (serial.a_wins, serial.b_wins, serial.draws) == (
        parallel.a_wins, parallel.b_wins, parallel.draws)


def test_wilson_interval_is_sane():
    low, high = wilson(50, 100)
    assert low < 0.5 < high
    assert high - low < 0.21
    low, high = wilson(0, 10)
    assert low == 0.0 and high < 0.35
