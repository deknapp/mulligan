"""Real-set mechanics, checked on real cards from The Hobbit (HOB).

Each test is a situation any Magic player would adjudicate the same way. They
exercise the engine features a current Limited set needs — Treasure, amass,
equipment, auras, adventures, flashback, modes, sagas, landfall, recruit,
storied, ward, hexproof, the legend rule, hybrid mana — through the compiled
card data, so a regression in either the engine or the compiled set shows up.
"""

from __future__ import annotations

import pytest

from mulligan.cards.sets import load_set
from mulligan.engine import actions as act
from mulligan.engine.types import Keyword, Step
from mulligan.scenario import Side, build_scenario

HOB = load_set("hob").playable


def scene(you: Side, opponent: Side | None = None, **kwargs):
    return build_scenario(you, opponent or Side(), pool=HOB, **kwargs)


def options(game, kind=act.Action):
    return [o for o in game.legal_actions() if isinstance(o, kind)]


def cast(game, name: str, *, face: str = "", mode: int = -1, target: str | None = None,
         extra: int = -1):
    found = []
    for o in options(game, act.CastSpell):
        if game.state.obj(o.card_id).name != name or o.face != face or o.mode != mode:
            continue
        if extra != -1 and o.extra != extra:
            continue
        if target is not None and not any(
                t.kind == "object" and game.object_by_id(t.id).name == target
                for t in o.targets):
            continue
        found.append(o)
    assert found, f"cannot cast {name} face={face!r} mode={mode} target={target}"
    game.apply(found[0])


def resolve(game):
    """Pass priority until the stack is empty (choosing the first target for
    any trigger that asks)."""
    guard = 0
    while game.state.stack or game.state.pending == "trigger_targets":
        guard += 1
        assert guard < 50
        if game.state.pending == "trigger_targets":
            game.apply(game.legal_actions()[0])
        else:
            game.apply(next(o for o in game.legal_actions() if isinstance(o, act.Pass)))


def named(game, name: str, seat: int | None = None):
    return [o for o in game.state.objects.values() if o.name == name and o.zone == "battlefield"
            and (seat is None or o.controller == seat)]


def test_the_whole_set_loads_and_commons_and_uncommons_are_all_playable():
    hob = load_set("hob")
    assert not hob.errors
    rarities = [hob.rarity(n) for n in hob.playable]
    assert rarities.count("common") == sum(1 for e in hob.entries.values()
                                           if e["rarity"] == "common")
    assert rarities.count("uncommon") == sum(1 for e in hob.entries.values()
                                             if e["rarity"] == "uncommon")
    for name, reason in hob.unsupported.items():
        assert reason, f"{name} is unsupported without a reason"


def test_treasure_is_created_and_pays_for_a_spell():
    game = scene(Side(hand=["Dori, Bearer of Friends", "Bilbo's Deadly Slice"],
                      lands={"R": 3, "B": 2}),
                 Side(battlefield=["Ordinary Bear"]))
    cast(game, "Dori, Bearer of Friends")
    resolve(game)
    assert len(named(game, "Treasure", 0)) == 1
    # Dori must be paid with the Mountains (the hand still needs {B}{B}); then
    # two Swamps + the Treasure pay {1}{B}{B}, sacrificing the Treasure.
    assert [o.name for o in game.state.zone_objects(0, "battlefield") if o.tapped] == [
        "Mountain"] * 3
    cast(game, "Bilbo's Deadly Slice", target="Ordinary Bear")
    resolve(game)
    assert not named(game, "Treasure", 0)
    assert not named(game, "Ordinary Bear")


def test_amass_makes_one_army_and_grows_it():
    game = scene(Side(hand=["Goblin-town Flunkies", "Rage into the Valley"],
                      lands={"R": 2, "B": 3}))
    cast(game, "Goblin-town Flunkies")
    resolve(game)
    armies = [o for o in game.battlefield() if "Army" in o.spec.subtypes]
    assert len(armies) == 1 and game.power_of(armies[0]) == 1
    cast(game, "Rage into the Valley")
    resolve(game)
    armies = [o for o in game.battlefield() if "Army" in o.spec.subtypes]
    assert len(armies) == 1 and game.power_of(armies[0]) == 3
    assert "Goblin" in armies[0].spec.subtypes


def test_equipment_buffs_what_it_is_attached_to_and_equip_is_sorcery_speed():
    game = scene(Side(battlefield=["Well-Worn Spatula", "Ordinary Bear"], lands={"W": 1}))
    bear = named(game, "Ordinary Bear")[0]
    equip = [o for o in options(game, act.ActivateAbility)]
    assert equip, "equip should be offered in the main phase"
    game.apply(equip[0])
    resolve(game)
    assert (game.power_of(bear), game.toughness_of(bear)) == (5, 6)


def test_aura_locks_down_a_creature_and_strips_flying():
    game = scene(Side(hand=["Enchanted River's Grasp"], lands={"U": 3}),
                 Side(battlefield=["Long Lake Nuisance"]))
    cast(game, "Enchanted River's Grasp", target="Long Lake Nuisance")
    resolve(game)
    bird = named(game, "Long Lake Nuisance")[0]
    assert bird.tapped
    assert not game.has_keyword(bird, Keyword.FLYING)
    assert "doesnt_untap" in game.flags_of(bird)


def test_adventure_goes_on_an_adventure_then_casts_the_creature_from_exile():
    game = scene(Side(hand=["Smaug, the Great Calamity"], lands={"R": 7}),
                 Side(battlefield=["Ordinary Bear"]))
    cast(game, "Smaug, the Great Calamity", face="adventure", target="Ordinary Bear")
    resolve(game)
    assert not named(game, "Ordinary Bear")
    smaug = next(o for o in game.state.objects.values() if o.name == "Smaug, the Great Calamity")
    assert smaug.zone == "exile" and smaug.on_adventure
    # On our next turn, with seven lands untapped, the Dragon is castable from exile.
    for _ in range(200):
        if game.state.active == 0 and options(game, act.CastSpell):
            break
        legal = game.legal_actions()
        game.apply(next((o for o in legal if isinstance(o, (act.Pass, act.FinishDeclaring))),
                        legal[0]))
    cast(game, "Smaug, the Great Calamity")
    resolve(game)
    assert named(game, "Smaug, the Great Calamity", 0)


def test_flashback_casts_from_the_graveyard_with_the_bonus_then_exiles():
    game = scene(Side(graveyard=["Plunder the Trollshaws"], lands={"U": 4}))
    hand_before = len(game.state.players[0].hand)
    cast(game, "Plunder the Trollshaws", face="flashback")
    resolve(game)
    assert len(game.state.players[0].hand) == hand_before + 2
    plunder = next(o for o in game.state.objects.values() if o.name == "Plunder the Trollshaws")
    assert plunder.zone == "exile"


def test_modal_spell_offers_each_mode():
    game = scene(Side(hand=["Thorin's Last Stand"], lands={"W": 4}),
                 Side(battlefield=["Well-Worn Spatula"]))
    modes = {o.mode for o in options(game, act.CastSpell)}
    assert modes == {0, 1}
    cast(game, "Thorin's Last Stand", mode=1, target="Well-Worn Spatula")
    resolve(game)
    assert not named(game, "Well-Worn Spatula")
    assert game.state.players[0].life == 22


def test_saga_advances_each_turn_and_is_sacrificed_after_its_last_chapter():
    game = scene(Side(hand=["Down, Down to Goblin-town"], lands={"B": 3}),
                 Side(hand=["Ordinary Bear", "Ordinary Bear"]))
    cast(game, "Down, Down to Goblin-town")
    game.apply(act.Pass())  # the Saga resolves, chapter I triggers
    game.apply(act.Pass())
    assert [game.state.obj(i).name for i in game.state.players[1].graveyard] == [
        "Ordinary Bear"]  # chapter I: they discarded
    # The engine plays forced passes itself; walk turns until the Saga is gone.
    for _ in range(200):
        if not named(game, "Down, Down to Goblin-town") or game.is_over:
            break
        legal = game.legal_actions()
        game.apply(next((o for o in legal if isinstance(o, (act.Pass, act.FinishDeclaring,
                                                            act.KeepHand))), legal[0]))
    assert not named(game, "Down, Down to Goblin-town")
    assert game.state.players[1].life == 18  # chapters III and IV drain 1 each
    assert any("Army" in o.spec.subtypes for o in game.battlefield())


def test_landfall_pumps_attercop():
    game = scene(Side(battlefield=["Attercop"], hand=["Forest"]))
    spider = named(game, "Attercop")[0]
    game.apply(options(game, act.PlayLand)[0])
    resolve(game)
    assert (game.power_of(spider), game.toughness_of(spider)) == (3, 2)


def test_recruit_makes_a_soldier_when_it_discards_a_nonland():
    game = scene(Side(hand=["Patient Instructor", "Ordinary Bear"], lands={"W": 3},
                      library=["Magnificent End"] * 5))
    cast(game, "Patient Instructor")
    resolve(game)
    assert named(game, "Human Soldier", 0)


def test_storied_turns_on_with_three_legendaries_or_artifacts():
    game = scene(Side(battlefield=["Ori, Keeper of Songs", "Well-Worn Spatula"]))
    ori = named(game, "Ori, Keeper of Songs")[0]
    assert game.power_of(ori) == 3 and not game.has_keyword(ori, Keyword.VIGILANCE)
    game2 = scene(Side(battlefield=["Ori, Keeper of Songs", "Well-Worn Spatula",
                                    "Giant's Boulder"]))
    ori2 = named(game2, "Ori, Keeper of Songs")[0]
    assert game2.state.players[0].enduring_story
    assert game2.power_of(ori2) == 4 and game2.has_keyword(ori2, Keyword.VIGILANCE)


def test_ward_taxes_targeting_and_hexproof_forbids_it():
    game = scene(Side(hand=["Bilbo's Deadly Slice"], lands={"B": 5}),
                 Side(battlefield=["Gandalf, Wandering Wizard"]))
    assert not options(game, act.CastSpell), "3 + ward 3 is more than five lands"
    game2 = scene(Side(hand=["Bilbo's Deadly Slice"], lands={"B": 6}),
                  Side(battlefield=["Gandalf, Wandering Wizard"]))
    assert options(game2, act.CastSpell)
    game3 = scene(Side(hand=["Bilbo's Deadly Slice"], lands={"B": 3}),
                  Side(battlefield=["Gigantic Big Bear"]))
    assert not options(game3, act.CastSpell)


def test_legend_rule_keeps_the_newest_copy():
    game = scene(Side(battlefield=["Ori, Keeper of Songs"], hand=["Ori, Keeper of Songs"],
                      lands={"W": 3}))
    cast(game, "Ori, Keeper of Songs")
    resolve(game)
    assert len(named(game, "Ori, Keeper of Songs")) == 1


def test_hybrid_mana_accepts_either_color():
    for color in ("B", "G"):
        game = scene(Side(hand=["Duskwatch Hunter"], lands={color: 3}))
        assert options(game, act.CastSpell), f"{{2}}{{B/G}} should be payable with {color}"


def test_additional_cost_is_either_sacrifice_or_four_more_mana():
    game = scene(Side(hand=["Stir Up Trouble"], battlefield=["Well-Worn Spatula"],
                      lands={"B": 1}),
                 Side(battlefield=["Ordinary Bear"]))
    extras = {o.extra for o in options(game, act.CastSpell)}
    assert extras == {0}, "with one land, only the sacrifice option is affordable"
    cast(game, "Stir Up Trouble", target="Ordinary Bear", extra=0)
    resolve(game)
    assert not named(game, "Well-Worn Spatula") and not named(game, "Ordinary Bear")


def test_ferocious_needs_a_big_creature():
    game = scene(Side(battlefield=["Ravening Warg"]), step=Step.BEGIN_COMBAT)
    game2 = scene(Side(battlefield=["Ravening Warg", "Ordinary Bear"]), step=Step.BEGIN_COMBAT)
    for g, expected in ((game, 20), (game2, 22)):
        while g.state.pending != "attackers":
            g.apply(next(o for o in g.legal_actions() if isinstance(o, act.Pass)))
        warg = named(g, "Ravening Warg")[0]
        g.apply(act.DeclareAttacker(warg.id))
        if act.FinishDeclaring() in g.legal_actions():
            g.apply(act.FinishDeclaring())
        resolve(g)
        assert g.state.players[0].life == expected


@pytest.mark.parametrize("seed", range(40))
def test_menace_blocks_can_always_be_completed(seed):
    """Regression: a lone blocker on a menace attacker used to strand the
    block declaration with no legal action."""
    from mulligan.agents import RandomAgent
    from mulligan.engine.view import PlayerView
    game = scene(Side(battlefield=["Gollum, Silent Slinker", "Nighthowl Pursuer"]),
                 Side(battlefield=["Ordinary Bear", "Attercop", "Lake-town Lookout"]),
                 step=Step.BEGIN_COMBAT)
    agent = RandomAgent(seed)
    for _ in range(60):
        if game.is_over or game.state.turn > 6:
            break
        seat = game.state.decision_player
        game.apply(agent.choose(PlayerView(game, seat), game.legal_actions()))
