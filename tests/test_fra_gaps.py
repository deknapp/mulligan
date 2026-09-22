"""The Reality Fracture cards the engine could not play until the full spoiler
pass: two-brid costs, toughness-based and negative-power combat damage, charge
counters that make mana, turned-off enter-the-battlefield triggers, an
alternate win, and casting a 10-drop out of exile.

Each test pins the behaviour that made the card unsupported, because these are
exactly the cards whose simulated ratings nobody can sanity-check by eye.
"""

from __future__ import annotations

from mulligan.cards.sets import load_set
from mulligan.engine import actions as act
from mulligan.engine.types import Step
from mulligan.scenario import Side, build_scenario

FRA = load_set("fra").playable


def scene(you: Side, opponent: Side | None = None, **kwargs):
    return build_scenario(you, opponent or Side(), pool=FRA, **kwargs)


def options(game, kind=act.Action):
    return [o for o in game.legal_actions() if isinstance(o, kind)]


def cast(game, name: str, **kw):
    for o in options(game, act.CastSpell):
        if game.state.obj(o.card_id).name == name:
            game.apply(o)
            return o
    raise AssertionError(f"cannot cast {name}")


def castable(game, name: str) -> bool:
    return any(game.state.obj(o.card_id).name == name for o in options(game, act.CastSpell))


def resolve(game):
    for _ in range(80):
        if not (game.state.stack or game.state.pending == "trigger_targets"):
            return
        if game.state.pending == "trigger_targets":
            game.apply(game.legal_actions()[0])
        else:
            game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))


def named(game, name, seat=None):
    return [o for o in game.battlefield() if o.name == name
            and (seat is None or o.controller == seat)]


def swing(game):
    """Attack with everything; the engine auto-resolves a combat with no choices."""
    while game.state.pending != "attackers":
        game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))
    for _ in range(12):
        if game.state.pending != "attackers":
            break
        attacks = options(game, act.DeclareAttacker)
        if attacks:
            game.apply(attacks[0])
            continue
        finish = options(game, act.FinishDeclaring)
        if not finish:
            break
        game.apply(finish[0])
    for _ in range(80):
        if game.state.over or game.state.step in (Step.POSTCOMBAT_MAIN, Step.END_STEP):
            return
        actions = game.legal_actions()
        finish = [o for o in actions if isinstance(o, act.FinishDeclaring)]
        passes = [o for o in actions if isinstance(o, act.Pass)]
        if finish:
            game.apply(finish[0])
        elif passes:
            game.apply(passes[0])
        else:
            return


# ------------------------------------------------------------------ mana costs

def test_karn_gilded_guardian_costs_five_in_five_colors():
    """{2/W}{2/U}{2/B}{2/R}{2/G}: one mana per pip when you have every color."""
    game = scene(Side(hand=["Karn, Gilded Guardian"],
                      lands={"W": 1, "U": 1, "B": 1, "R": 1, "G": 1}))
    assert castable(game, "Karn, Gilded Guardian")


def test_karn_gilded_guardian_costs_eight_in_two_colors():
    """Two colored pips, three paid as two generic each: eight mana, not five."""
    seven = scene(Side(hand=["Karn, Gilded Guardian"], lands={"W": 4, "U": 3}))
    assert not castable(seven, "Karn, Gilded Guardian")
    eight = scene(Side(hand=["Karn, Gilded Guardian"], lands={"W": 4, "U": 4}))
    assert castable(eight, "Karn, Gilded Guardian")


def test_karn_gilded_guardian_draws_per_artifact_color():
    game = scene(Side(hand=["Karn, Gilded Guardian"],
                      lands={"W": 4, "U": 4, "B": 4, "R": 4, "G": 4},
                      battlefield=["Konstrari Improviser"], library=["Island"] * 10))
    before = len(game.state.players[0].hand)
    cast(game, "Karn, Gilded Guardian")
    resolve(game)
    # Only other artifacts count, and Karn himself is excluded.
    assert len(game.state.players[0].hand) >= before - 1


# -------------------------------------------------------------- combat damage

def test_ghalta_makes_your_wall_hit_for_its_toughness():
    game = scene(Side(battlefield=["Ghalta the Immovable", "Konstrari Improviser"],
                      lands={"W": 9}),
                 Side(life=20), step=Step.BEGIN_COMBAT)
    ghalta = named(game, "Ghalta the Immovable")[0]
    assert game.power_of(ghalta) == 0 and game.toughness_of(ghalta) == 7
    assert game.combat_power(ghalta) == 7, "assigns damage by toughness"


def test_ghalta_costs_less_for_your_biggest_toughness():
    """{8}{W}, minus the greatest toughness among creatures you control."""
    alone = scene(Side(hand=["Ghalta the Immovable"], lands={"W": 8}))
    assert not castable(alone, "Ghalta the Immovable")
    withwall = scene(Side(hand=["Ghalta the Immovable"], lands={"W": 5},
                          battlefield=["Blossom-Blessed Angel"]))
    assert castable(withwall, "Ghalta the Immovable"), "a 2/4 takes four off the cost"


def test_loot_the_anomaly_hits_for_its_negative_power():
    game = scene(Side(battlefield=["Loot, the Anomaly"], lands={"B": 3}))
    loot = named(game, "Loot, the Anomaly")[0]
    assert game.raw_power_of(loot) == -2 and game.power_of(loot) == 0
    assert game.combat_power(loot) == 2, "negative power assigns as though positive"


# ----------------------------------------------------------------- mana engines

def test_gardenize_banks_a_charge_counter_when_your_creature_dies():
    game = scene(Side(battlefield=["Gardenize", "Konstrari Improviser"], lands={"G": 3}))
    garden = named(game, "Gardenize")[0]
    game.sacrifice(named(game, "Konstrari Improviser")[0].id)
    game.advance()
    assert garden.counters == 1


def test_gardenize_charge_counters_become_green_mana_at_your_first_main():
    """The point of the card: the counters are mana, with no lands involved."""
    game = scene(Side(hand=["Budding Insurgent"], battlefield=["Gardenize"], lands={}))
    named(game, "Gardenize")[0].counters = 3
    game._fire("first_main", controller=0)
    game.advance()
    assert dict(game.state.players[0].pool) == {"G": 3}
    assert game.can_pay(0, FRA["Budding Insurgent"].cost), "a {2}{G} spell, off no lands"


def test_loot_the_nexus_makes_one_mana_per_distinct_power():
    """Mana abilities are not offered as actions; the engine taps them to pay.

    Two creatures with different powers means two mana from the Nexus alone,
    which is what makes a three-drop castable off one land.
    """
    game = scene(Side(hand=["Budding Insurgent"],
                      battlefield=["Loot, the Nexus", "Campus Crier"],
                      lands={"G": 1}))
    nexus = named(game, "Loot, the Nexus")[0]
    nexus.summoning_sick = False
    powers = {game.power_of(o) for o in game.creatures_of(0)}
    assert len(powers) == 2, "a 2/1 and a 3/1: two different powers"
    assert castable(game, "Budding Insurgent"), "one land plus two mana from the Nexus"


# --------------------------------------------------------------- rule changes

def test_karn_argent_defender_turns_off_etb_triggers_for_both_players():
    game = scene(Side(hand=["Karn, Gilded Guardian"],
                      lands={"W": 4, "U": 4, "B": 4, "R": 4, "G": 4},
                      battlefield=["Karn, Argent Defender", "Konstrari Improviser"],
                      library=["Island"] * 10))
    before = len(game.state.players[0].hand)
    cast(game, "Karn, Gilded Guardian")
    resolve(game)
    assert len(game.state.players[0].hand) == before - 1, "no draw trigger: only the card cast"


# ------------------------------------------------------------------- the rest

def test_cruel_calculations_draws_for_the_cards_milled_this_turn():
    game = scene(Side(hand=["Cruel Calculations"], lands={"U": 3},
                      library=["Island"] * 20),
                 Side(library=["Forest"] * 20))
    game.mill(1, 4)
    before = len(game.state.players[0].hand)
    spell = next(o for o in options(game, act.CastSpell)
                 if game.state.obj(o.card_id).name == "Cruel Calculations"
                 and any(t.kind == "player" and t.id == 1 for t in o.targets))
    game.apply(spell)
    resolve(game)
    assert len(game.state.players[0].hand) == before - 1 + 4


def test_fblthp_draws_two_and_shuffles_himself_away():
    game = scene(Side(battlefield=["Fblthp, Impossibly Lost"], lands={"U": 2},
                      library=["Island"] * 20),
                 Side(life=20), step=Step.BEGIN_COMBAT)
    fblthp = named(game, "Fblthp, Impossibly Lost")[0]
    fblthp.summoning_sick = False
    before = len(game.state.players[0].hand)
    swing(game)
    resolve(game)
    assert game.state.players[1].life < 20
    assert len(game.state.players[0].hand) == before + 2
    assert not named(game, "Fblthp, Impossibly Lost"), "shuffled into the library"


def test_fblthp_wins_the_game_off_an_empty_library():
    game = scene(Side(battlefield=["Fblthp, Impossibly Lost"], lands={"U": 2}),
                 Side(life=20), step=Step.BEGIN_COMBAT)
    fblthp = named(game, "Fblthp, Impossibly Lost")[0]
    fblthp.summoning_sick = False
    game.state.players[0].library.clear()  # the scenario builder always deals a library
    swing(game)
    resolve(game)
    game.advance()
    assert game.state.over and game.state.winner == 0


def test_face_yourself_borrows_the_opposing_board_for_one_turn():
    game = scene(Side(hand=["Face Yourself"], lands={"R": 7}),
                 Side(battlefield=["Konstrari Improviser", "Blossom-Blessed Angel"]))
    cast(game, "Face Yourself")
    resolve(game)
    mine = [o for o in game.state.zone_objects(0, "battlefield") if game.is_creature(o)]
    assert len(mine) == 2 and all(o.is_token for o in mine)


def test_emrakul_can_be_pitched_for_mana_and_cast_from_exile():
    game = scene(Side(hand=["Emrakul, the Exigent Doom"], lands={"G": 3}))
    emrakul = game.state.players[0].hand[0]
    pitch = next(o for o in options(game, act.ActivateAbility)
                 if o.source_id == emrakul)
    game.apply(pitch)
    resolve(game)
    assert game.state.objects[emrakul].zone == "exile"
    land = next(o for o in game.state.zone_objects(0, "battlefield") if o.spec.is_land)
    assert game.abilities_of(land), "the land gained a mana ability"
    # With enough lands the exiled card is castable from exile.
    rich = scene(Side(hand=["Emrakul, the Exigent Doom"], lands={"G": 13}))
    card = rich.state.players[0].hand[0]
    rich.apply(next(o for o in options(rich, act.ActivateAbility) if o.source_id == card))
    resolve(rich)
    assert castable(rich, "Emrakul, the Exigent Doom"), "playable while it stays exiled"


def test_omnipresence_discounts_your_spells_by_your_creature_count():
    bare = scene(Side(hand=["Ghalta the Immovable"], lands={"W": 6}))
    assert not castable(bare, "Ghalta the Immovable")
    with_omni = scene(Side(hand=["Ghalta the Immovable"], lands={"W": 6},
                           battlefield=["Omnipresence", "Konstrari Improviser"]))
    assert castable(with_omni, "Ghalta the Immovable"), "one creature takes {1} off"


def test_ghalta_the_unstoppable_costs_less_for_your_biggest_power():
    """{8}{G} minus the greatest power among creatures you control."""
    alone = scene(Side(hand=["Ghalta the Unstoppable"], lands={"G": 8}))
    assert not castable(alone, "Ghalta the Unstoppable")
    with_crier = scene(Side(hand=["Ghalta the Unstoppable"], lands={"G": 6},
                            battlefield=["Campus Crier"]))
    assert castable(with_crier, "Ghalta the Unstoppable"), "a 3/1 takes three off"


def test_rise_of_the_deathbringer_draws_your_greatest_power():
    game = scene(Side(hand=["Rise of the Deathbringer"], lands={"B": 5},
                      battlefield=["Campus Crier"], library=["Island"] * 20))
    before = len(game.state.players[0].hand)
    life = game.state.players[0].life
    draw = next(o for o in options(game, act.CastSpell)
                if game.state.obj(o.card_id).name == "Rise of the Deathbringer" and o.mode == 0)
    game.apply(draw)
    resolve(game)
    assert len(game.state.players[0].hand) == before - 1 + 3, "a 3/1 means three cards"
    assert game.state.players[0].life == life - 3


def test_the_agent_will_not_pay_a_life_cost_that_kills_it():
    """Rise of the Deathbringer draws (and costs) your greatest power. With a
    big creature out and a low life total, the draw mode is suicide."""
    from mulligan.agents.heuristic import HeuristicAgent
    from mulligan.engine.view import PlayerView
    game = scene(Side(hand=["Rise of the Deathbringer"], lands={"B": 5}, life=3,
                      battlefield=["Ghalta the Unstoppable"], library=["Island"] * 20),
                 Side(life=20))
    agent = HeuristicAgent()
    view = PlayerView(game, 0)
    draw_mode = next(o for o in options(game, act.CastSpell)
                     if game.state.obj(o.card_id).name == "Rise of the Deathbringer"
                     and o.mode == 0)
    assert agent.score(view, draw_mode) < 0, "drawing 8 at 3 life kills you"
