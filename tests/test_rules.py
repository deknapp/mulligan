"""Rules behaviour, checked on constructed positions.

Each test states a situation any Magic player would adjudicate the same way. If
one of these breaks, every benchmark number the engine produces is suspect.

Assertions are on outcomes rather than on intermediate prompts, because the
engine auto-resolves any point where a player has only one legal action — a
blocker that cannot legally block is never offered as a choice at all.
"""

from __future__ import annotations

from mulligan.engine import actions as act
from mulligan.engine.types import TURN_SEQUENCE, Step, Target
from mulligan.scenario import Side, build_scenario

STEP_ORDER = {step: index for index, step in enumerate(TURN_SEQUENCE)}


def _position(game) -> tuple[int, int]:
    return (game.state.turn, STEP_ORDER[game.state.step])


def run_until(game, step: Step, *, turn: int | None = None, attack: bool = False,
              block: bool = False):
    """Play forward to a point in the turn, optionally attacking and blocking.

    Attacks and blocks are all-in: every legal attacker attacks, and every
    legal blocker blocks. That is not good play, but these tests are about what
    the rules do, not about what is wise.
    """
    target = (turn if turn is not None else game.state.turn, STEP_ORDER[step])
    guard = 0
    while not game.is_over and _position(game) < target:
        guard += 1
        assert guard < 500, f"never reached {step}"
        options = game.legal_actions()
        finish = next((o for o in options if isinstance(o, act.FinishDeclaring)), None)
        if game.state.pending == "attackers":
            declares = [o for o in options if isinstance(o, act.DeclareAttacker)]
            game.apply(declares[0] if (attack and declares) else finish or declares[0])
        elif game.state.pending == "blockers":
            declares = [o for o in options if isinstance(o, act.DeclareBlocker)]
            game.apply(declares[0] if (block and declares) else finish or declares[0])
        else:
            game.apply(next((o for o in options if isinstance(o, act.Pass)), options[0]))
    return game


def after_combat(game, **kwargs):
    return run_until(game, Step.POSTCOMBAT_MAIN, attack=True, **kwargs)


def creature_names(game) -> list[str]:
    return [obj.name for obj in game.all_creatures()]


def cast(game, card_name: str, *, target_name: str | None = None, target_index: int = 0):
    """Cast a named card from hand.

    ``target_name`` picks the target explicitly. Tests that leave it out take
    the first legal target set, which is only safe when the choice cannot
    change the outcome — targeting the wrong creature silently is exactly the
    sort of bug that makes a test pass for the wrong reason.
    """
    options = [o for o in game.legal_actions()
               if isinstance(o, act.CastSpell)
               and game.state.obj(o.card_id).name == card_name]
    if target_name is not None:
        options = [o for o in options
                   if any(t.kind == "object"
                          and game.object_by_id(t.id).name == target_name for t in o.targets)]
    assert options, f"{card_name} is not castable here" + (
        f" targeting {target_name}" if target_name else "")
    game.apply(options[target_index])


# ------------------------------------------------------------------- combat


def test_unblocked_creature_damages_the_player():
    game = after_combat(build_scenario(Side(battlefield=["Grizzly Bears"]), Side(life=20)))
    assert game.state.players[1].life == 18


def test_flying_is_unblockable_by_ground_creatures():
    """A 4/4 flier connects for 4 even though a 2/2 is untapped and willing."""
    game = after_combat(build_scenario(Side(battlefield=["Serra Angel"]),
                                       Side(battlefield=["Grizzly Bears"], life=20)),
                        block=True)
    assert game.state.players[1].life == 16
    assert "Grizzly Bears" in creature_names(game), "the blocker never entered combat"


def test_reach_blocks_a_flier():
    """The same attack, into a 2/4 reach blocker, deals nothing to the player."""
    game = after_combat(build_scenario(Side(battlefield=["Serra Angel"]),
                                       Side(battlefield=["Giant Spider"], life=20)),
                        block=True)
    assert game.state.players[1].life == 20


def test_deathtouch_kills_a_larger_creature():
    game = after_combat(build_scenario(Side(battlefield=["Craw Wurm"]),
                                       Side(battlefield=["Vampire Nighthawk"])),
                        block=True)
    assert "Craw Wurm" not in creature_names(game), "deathtouch damage must be lethal"


def test_lifelink_gains_life_on_combat_damage():
    game = after_combat(build_scenario(Side(battlefield=["Vampire Nighthawk"], life=10),
                                       Side(life=20)))
    assert game.state.players[0].life == 12
    assert game.state.players[1].life == 18


def test_trample_pushes_excess_damage_through():
    """A 6/6 trampler blocked by a 2/2 assigns 2 lethal and tramples 4 over."""
    game = after_combat(build_scenario(Side(battlefield=["Colossal Dreadmaw"]),
                                       Side(battlefield=["Grizzly Bears"], life=20)),
                        block=True)
    assert game.state.players[1].life == 16


def test_blocked_creature_without_trample_deals_nothing_to_the_player():
    game = after_combat(build_scenario(Side(battlefield=["Craw Wurm"]),
                                       Side(battlefield=["Grizzly Bears"], life=20)),
                        block=True)
    assert game.state.players[1].life == 20


def test_vigilance_creature_stays_untapped_after_attacking():
    game = after_combat(build_scenario(Side(battlefield=["Serra Angel"]), Side()))
    angel = next(o for o in game.all_creatures() if o.name == "Serra Angel")
    assert not angel.tapped


def test_attacking_taps_a_creature_without_vigilance():
    game = after_combat(build_scenario(Side(battlefield=["Craw Wurm"]), Side()))
    wurm = next(o for o in game.all_creatures() if o.name == "Craw Wurm")
    assert wurm.tapped


def test_summoning_sick_creature_cannot_attack():
    game = build_scenario(Side(battlefield=["Grizzly Bears"],
                               summoning_sick=["Grizzly Bears"]), Side(life=20))
    game = after_combat(game)
    assert game.state.players[1].life == 20


def test_haste_creature_can_attack_immediately():
    game = build_scenario(Side(battlefield=["Raging Goblin"],
                               summoning_sick=["Raging Goblin"]), Side(life=20))
    game = after_combat(game)
    assert game.state.players[1].life == 19


# -------------------------------------------------------------------- spells


def test_lightning_bolt_kills_a_two_toughness_creature():
    game = build_scenario(Side(hand=["Lightning Bolt"], lands={"R": 1}),
                          Side(battlefield=["Grizzly Bears"]))
    bolts = [o for o in game.legal_actions()
             if isinstance(o, act.CastSpell)
             and game.state.obj(o.card_id).name == "Lightning Bolt"
             and o.targets[0].kind == "object"]
    game.apply(bolts[0])
    run_until(game, Step.DECLARE_ATTACKERS)
    assert "Grizzly Bears" not in creature_names(game)


def test_lightning_bolt_to_the_face_can_win_the_game():
    game = build_scenario(Side(hand=["Lightning Bolt"], lands={"R": 1}), Side(life=3))
    kill = next(o for o in game.legal_actions()
                if isinstance(o, act.CastSpell)
                and game.state.obj(o.card_id).name == "Lightning Bolt"
                and o.targets[0] == Target("player", 1))
    game.apply(kill)
    assert game.is_over and game.state.winner == 0


def test_counterspell_stops_a_spell_from_resolving():
    game = build_scenario(Side(hand=["Craw Wurm"], lands={"G": 6}),
                          Side(hand=["Counterspell"], lands={"U": 2}))
    cast(game, "Craw Wurm")
    cast(game, "Counterspell")
    run_until(game, Step.DECLARE_ATTACKERS)
    assert "Craw Wurm" not in creature_names(game)
    assert "Craw Wurm" in [game.state.obj(i).name for i in game.state.players[0].graveyard]


def test_spell_fizzles_when_its_only_target_leaves():
    """Murder pointed at a creature that gets bounced in response does nothing."""
    game = build_scenario(Side(hand=["Murder"], lands={"B": 3}),
                          Side(battlefield=["Grizzly Bears"], hand=["Unsummon"],
                               lands={"U": 1}))
    cast(game, "Murder")
    cast(game, "Unsummon")
    run_until(game, Step.DECLARE_ATTACKERS)
    hand = [game.state.obj(i).name for i in game.state.players[1].hand]
    assert "Grizzly Bears" in hand, "the bounced creature should be safe in hand"


def test_etb_trigger_fires_and_can_be_pointed():
    game = build_scenario(Side(hand=["Flametongue Kavu"], lands={"R": 4}),
                          Side(battlefield=["Craw Wurm"]))
    cast(game, "Flametongue Kavu")
    while game.state.pending == "trigger_targets":
        choice = next(o for o in game.legal_actions()
                      if isinstance(o, act.ChooseTargets)
                      and game.object_by_id(o.targets[0].id).name == "Craw Wurm")
        game.apply(choice)
    run_until(game, Step.DECLARE_ATTACKERS)
    assert "Craw Wurm" not in creature_names(game)
    assert "Flametongue Kavu" in creature_names(game)


def test_wrath_of_god_sweeps_both_sides():
    game = build_scenario(Side(hand=["Wrath of God"], lands={"W": 4},
                               battlefield=["Savannah Lions"]),
                          Side(battlefield=["Craw Wurm", "Grizzly Bears"]))
    cast(game, "Wrath of God")
    run_until(game, Step.DECLARE_ATTACKERS)
    assert creature_names(game) == []


def test_giant_growth_pumps_and_then_wears_off():
    game = build_scenario(Side(battlefield=["Grizzly Bears"], hand=["Giant Growth"],
                               lands={"G": 1}), Side())
    cast(game, "Giant Growth", target_name="Grizzly Bears")
    run_until(game, Step.DECLARE_ATTACKERS)
    bears = next(o for o in game.all_creatures() if o.name == "Grizzly Bears")
    assert (game.power_of(bears), game.toughness_of(bears)) == (5, 5)
    run_until(game, Step.PRECOMBAT_MAIN, turn=game.state.turn + 1)
    bears = next(o for o in game.all_creatures() if o.name == "Grizzly Bears")
    assert (game.power_of(bears), game.toughness_of(bears)) == (2, 2), "pump must expire"


def test_giant_growth_wins_a_combat():
    """A 2/2 that blocks a 6/4 and grows to 5/5 dies; the Wurm dies too."""
    game = build_scenario(Side(battlefield=["Craw Wurm"]),
                          Side(battlefield=["Grizzly Bears"], hand=["Giant Growth"],
                               lands={"G": 1}))
    game = run_until(game, Step.DECLARE_BLOCKERS, attack=True)
    # Blocks are declared first: the pump has to come after blockers are locked
    # in, which is exactly when a real player would cast it.
    while game.state.pending == "blockers":
        options = game.legal_actions()
        blocks = [o for o in options if isinstance(o, act.DeclareBlocker)]
        game.apply(blocks[0] if blocks else act.FinishDeclaring())
    cast(game, "Giant Growth", target_name="Grizzly Bears")
    run_until(game, Step.END_STEP, attack=True, block=True)
    assert "Craw Wurm" not in creature_names(game), "a 5/5 blocker kills a 6/4"


def test_mana_is_actually_required():
    """With one Mountain, one Lightning Bolt is castable and Craw Wurm is not."""
    game = build_scenario(Side(hand=["Lightning Bolt", "Craw Wurm"], lands={"R": 1}), Side())
    castable = {game.state.obj(o.card_id).name for o in game.legal_actions()
                if isinstance(o, act.CastSpell)}
    assert castable == {"Lightning Bolt"}


def test_a_land_can_only_be_played_once_per_turn():
    game = build_scenario(Side(hand=["Forest", "Forest"]), Side())
    plays = [o for o in game.legal_actions() if isinstance(o, act.PlayLand)]
    assert len(plays) == 2, "both lands are playable before one is played"
    game.apply(plays[0])
    assert game.state.players[0].lands_played == 1
    # The engine auto-passes out of a turn with nothing left to do, so the only
    # land drop still on offer can be the opponent's, on their own turn.
    remaining = [o for o in game.legal_actions() if isinstance(o, act.PlayLand)]
    assert all(game.state.obj(o.card_id).owner != 0 for o in remaining)
