"""Reality Fracture (FRA) mechanics on real cards from the spoiler:
planeswalkers and Empower Jace, prepare, surveil, prowess, stun counters,
planeswalker-conditional lands, granted loyalty abilities, behold."""

from __future__ import annotations

from mulligan.cards.sets import load_set
from mulligan.engine import actions as act
from mulligan.engine.types import CardType, Step
from mulligan.scenario import Side, build_scenario

FRA = load_set("fra").playable


def scene(you: Side, opponent: Side | None = None, **kwargs):
    return build_scenario(you, opponent or Side(), pool=FRA, **kwargs)


def options(game, kind=act.Action):
    return [o for o in game.legal_actions() if isinstance(o, kind)]


def cast(game, name: str, *, face: str = "", target: str | None = None):
    for o in options(game, act.CastSpell):
        if game.state.obj(o.card_id).name != name or o.face != face:
            continue
        if target is not None and not any(t.kind == "object" and game.object_by_id(t.id).name
                                          == target for t in o.targets):
            continue
        game.apply(o)
        return
    raise AssertionError(f"cannot cast {name} face={face!r} target={target}")


def resolve(game):
    for _ in range(60):
        if not (game.state.stack or game.state.pending == "trigger_targets"):
            return
        if game.state.pending == "trigger_targets":
            game.apply(game.legal_actions()[0])
        else:
            game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))


def jaces(game, seat=0):
    return [o for o in game.state.zone_objects(seat, "battlefield")
            if CardType.PLANESWALKER in o.spec.types and "Jace" in o.spec.subtypes]


def named(game, name, seat=None):
    return [o for o in game.battlefield() if o.name == name
            and (seat is None or o.controller == seat)]


def test_empower_jace_creates_one_token_and_then_adds_loyalty():
    game = scene(Side(hand=["Mindseeker Oculus", "Protege's Awakening"], lands={"U": 7}))
    cast(game, "Mindseeker Oculus")
    resolve(game)
    assert [j.loyalty for j in jaces(game)] == [4]
    cast(game, "Protege's Awakening")
    resolve(game)
    assert [j.loyalty for j in jaces(game)] == [10], "the same token, not a second one"


def test_jace_token_loyalty_abilities_once_per_turn_and_not_below_zero():
    game = scene(Side(hand=["Mindseeker Oculus"], lands={"U": 3}, library=["Island"] * 10))
    cast(game, "Mindseeker Oculus")
    resolve(game)
    jace = jaces(game)[0]
    abilities = [o for o in options(game, act.ActivateAbility) if o.source_id == jace.id]
    assert len(abilities) == 2  # -1 surveil and -3 draw, both affordable at 4
    hand = len(game.state.players[0].hand)
    game.apply(next(o for o in abilities if o.index == 1))  # -3: draw a card
    resolve(game)
    assert jace.loyalty == 1 and len(game.state.players[0].hand) == hand + 1
    assert not [o for o in options(game, act.ActivateAbility) if o.source_id == jace.id]


def test_damage_to_a_planeswalker_removes_loyalty_and_it_dies_at_zero():
    game = scene(Side(hand=["No Admittance"], lands={"R": 2}),
                 Side(hand=[], battlefield=[]))
    game.empower_jace(1, 3)
    game.advance()  # re-examine the board after changing it directly
    cast(game, "No Admittance", target="Jace")
    resolve(game)
    assert not jaces(game, 1)


def test_attacking_a_planeswalker_damages_it_not_the_player():
    game = scene(Side(battlefield=["Heartstring Puller"]), step=Step.BEGIN_COMBAT)
    game.empower_jace(1, 5)
    game.advance()
    jace = jaces(game, 1)[0]
    while game.state.pending != "attackers":
        game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))
    puller = named(game, "Heartstring Puller")[0]
    assert act.DeclareAttacker(puller.id, jace.id) in game.legal_actions()
    game.apply(act.DeclareAttacker(puller.id, jace.id))  # combat then resolves on its own
    assert jace.loyalty == 2 and game.state.players[1].life == 20


def test_prepared_creature_casts_a_copy_of_its_spell_once():
    game = scene(Side(hand=["Emergency Phytomedic"], lands={"G": 3}))
    cast(game, "Emergency Phytomedic")
    resolve(game)
    medic = named(game, "Emergency Phytomedic")[0]
    assert medic.prepared
    cast(game, "Emergency Phytomedic", face="prepared", target="Emergency Phytomedic")
    resolve(game)
    assert medic.zone == "battlefield" and not medic.prepared
    assert medic.counters == 1 and game.state.players[0].life == 21
    assert not [o for o in options(game, act.CastSpell) if o.face == "prepared"]


def test_upkeep_trigger_prepares_again():
    game = scene(Side(battlefield=["Stingerquill Voxmancer"]), step=Step.END_STEP)
    vox = named(game, "Stingerquill Voxmancer")[0]
    assert not vox.prepared
    for _ in range(40):
        if game.state.turn >= 7 and game.state.active == 0 and game.state.step.value in (
                "draw", "precombat_main"):
            break
        game.apply(next(o for o in game.legal_actions()
                        if isinstance(o, (act.Pass, act.FinishDeclaring))))
    assert vox.prepared


def test_surveil_and_scry_or_surveil_triggers():
    game = scene(Side(battlefield=["Saheeli, Consul of Oversight"], hand=["Unsummon"],
                      lands={"U": 4}, library=["Island"] * 10),
                 Side(battlefield=["Heartstring Puller"]))
    game.surveil_count = 0
    game.auto_surveil(0, 2)
    game.advance()
    resolve(game)
    assert game.state.players[0].surveilled_this_turn
    assert named(game, "Thopter", 0)


def test_prowess_pumps_on_noncreature_spells():
    game = scene(Side(battlefield=["Cryotheory Adept"], hand=["Unsummon"], lands={"U": 1}),
                 Side(battlefield=["Heartstring Puller"]))
    adept = named(game, "Cryotheory Adept")[0]
    cast(game, "Unsummon", target="Heartstring Puller")
    assert (game.power_of(adept), game.toughness_of(adept)) == (3, 2)


def test_stun_counter_stops_one_untap():
    game = scene(Side(battlefield=["Heartstring Puller"]))
    puller = named(game, "Heartstring Puller")[0]
    puller.tapped, puller.stun = True, 1
    game.state.turn, game.state.active = 4, 1
    game._next_step()  # into our untap soon enough: walk to our next turn
    for _ in range(80):
        if game.state.active == 0 and game.state.step.value not in ("untap",):
            break
        game.apply(next(o for o in game.legal_actions()
                        if isinstance(o, (act.Pass, act.FinishDeclaring))))
    assert puller.tapped and puller.stun == 0


def test_annex_lands_enter_untapped_only_with_a_planeswalker():
    game = scene(Side(hand=["Fatehold Annex"]))
    game.apply(options(game, act.PlayLand)[0])
    assert named(game, "Fatehold Annex")[0].tapped
    game2 = scene(Side(hand=["Fatehold Annex"]))
    game2.empower_jace(0, 2)
    game2.advance()
    game2.apply(options(game2, act.PlayLand)[0])
    assert not named(game2, "Fatehold Annex")[0].tapped


def test_way_of_the_healer_grants_a_loyalty_ability():
    game = scene(Side(hand=["Way of the Healer"], lands={"W": 4}))
    cast(game, "Way of the Healer")
    resolve(game)
    jace = jaces(game)[0]
    granted = [o for o in options(game, act.ActivateAbility)
               if o.source_id == jace.id and o.index == 2]
    assert granted, "Jace gains '[-2]: create a Cadet, surveil 1'"
    game.apply(granted[0])
    resolve(game)
    assert named(game, "Cadet", 0) and jace.loyalty == 3


def test_tetsuko_static_does_not_recurse():
    """'Creatures you control with power or toughness 1 or less can't be
    blocked' asks for power while computing power; it must not recurse."""
    game = scene(Side(battlefield=["Tetsuko Umezawa, Fugitive", "Cryotheory Adept",
                                   "Rampart Hunter"]))
    assert "unblockable" in game.flags_of(named(game, "Cryotheory Adept")[0])  # 2/1
    assert "unblockable" in game.flags_of(named(game, "Tetsuko Umezawa, Fugitive")[0])
    assert "unblockable" not in game.flags_of(named(game, "Rampart Hunter")[0])  # 3/3


def test_countersculpt_behold_or_pay_one():
    no_jace = scene(Side(hand=["Countersculpt"], lands={"U": 2}))
    no_jace.state.stack.clear()
    assert all(o.extra != 0 for o in options(no_jace, act.CastSpell))


def test_every_trigger_event_a_card_uses_can_actually_fire():
    """Regression: new events were raised but not matched, so their triggers
    silently did nothing. Every 'when' in the compiled sets must be one the
    matcher knows."""
    import inspect

    from mulligan.engine.game import Game
    matcher = inspect.getsource(Game._trigger_matches)
    for code in ("hob", "fra"):
        for spec in load_set(code).playable.values():
            for trigger in spec.triggers:
                assert f'"{trigger.when}"' in matcher, (spec.name, trigger.when)


def test_a_real_planeswalker_card_enters_with_loyalty_and_uses_abilities():
    game = scene(Side(hand=["Chandra, Torch of Defiance"], lands={"R": 4}),
                 Side(battlefield=["Heartstring Puller"]))
    cast(game, "Chandra, Torch of Defiance")
    resolve(game)
    chandra = named(game, "Chandra, Torch of Defiance")[0]
    assert chandra.loyalty == 4
    minus = [o for o in options(game, act.ActivateAbility)
             if o.source_id == chandra.id and o.targets]
    game.apply(minus[0])  # -3: 4 damage to target creature
    resolve(game)
    assert chandra.loyalty == 1 and not named(game, "Heartstring Puller")


def test_tarmogoyf_counts_card_types_in_all_graveyards():
    game = scene(Side(battlefield=["Tarmogoyf"], graveyard=["Unsummon", "Rank Rat"]),
                 Side(graveyard=["Forest"]))
    goyf = named(game, "Tarmogoyf")[0]
    assert game.power_of(goyf) == 3  # instant, creature, land


def test_check_lands_need_two_other_lands():
    game = scene(Side(hand=["Deserted Beach"], lands={"W": 1}))
    game.apply(options(game, act.PlayLand)[0])
    assert named(game, "Deserted Beach")[0].tapped
    game2 = scene(Side(hand=["Deserted Beach"], lands={"W": 2}))
    game2.apply(options(game2, act.PlayLand)[0])
    assert not named(game2, "Deserted Beach")[0].tapped


def test_unknown_condition_kinds_are_load_errors():
    import pytest

    from mulligan.cards.schema import CardDataError, card
    with pytest.raises(CardDataError):
        card({"name": "X", "types": ["Creature"], "power": 1, "toughness": 1,
              "statics": [{"affects": "self", "power": 1, "if": {"kind": "no_such_thing"}}]})


def test_cast_restrictions_hold():
    """Proft, Sinister Mastermind can't be cast without threshold: an omitted
    restriction would make it a free 5/5 menace and inflate its rating."""
    small = scene(Side(hand=["Proft, Sinister Mastermind"], lands={"B": 3},
                       graveyard=["Unsummon"] * 3))
    assert not [o for o in options(small, act.CastSpell) if o.face == ""]
    big = scene(Side(hand=["Proft, Sinister Mastermind"], lands={"B": 3},
                     graveyard=["Unsummon"] * 7))
    assert [o for o in options(big, act.CastSpell) if o.face == ""]
