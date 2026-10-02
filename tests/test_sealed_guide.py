from mulligan import sealed_guide as sg


def _card(name, cost, edge, creature=True):
    c = sg.CardInfo(name, cost=cost, mv=sg._mana_value(cost), pips=sg._pips(cost),
                    types=["Creature"] if creature else ["Instant"])
    c.edge = edge
    return c


def test_costs_parse():
    assert sg._mana_value("{2}{U}{U}") == 4
    assert sg._pips("{1}{U/R}") == {"U": 0.5, "R": 0.5}
    assert _card("x", "{1}{U/R}", 0).castable({"R", "G"})
    assert not _card("x", "{U}{B}", 0).castable({"U", "G"})


def test_builds_rank_the_stronger_pair_first_and_splash_bombs():
    pool = [_card(f"Blue{i}", "{1}{U}", 0.2) for i in range(12)]
    pool += [_card(f"Black{i}", "{2}{B}", 0.1) for i in range(12)]
    pool += [_card(f"Green{i}", "{1}{G}", -0.3) for i in range(12)]
    pool += [_card("Red Bomb", "{3}{R}", 0.8, creature=False)]
    infos = {c.name: c for c in pool}
    pairs, best = sg.candidate_builds(list(infos), infos, games=500)
    assert pairs[0].colors == "UB"
    assert len(best[0].spells) == 23 and len(best[0].lands) == 17
    splashed = [b for b in best if b.splash]
    assert splashed and splashed[0].colors == "UB+r"
    assert splashed[0].lands.count("Mountain") == 3


def test_removal_is_not_cut_for_a_worse_fourteenth_creature():
    pool = [_card(f"Bear{i}", "{1}{G}", 0.0) for i in range(13)]
    pool += [_card(f"Sentry{i}", "{2}", -0.14) for i in range(3)]
    pool += [_card(f"Removal{i}", "{2}{U}", -0.02, creature=False) for i in range(2)]
    pool += [_card(f"Spell{i}", "{1}{U}", 0.05, creature=False) for i in range(8)]
    build = sg._build(pool, "UG", [])
    names = [c.name for c in build.spells]
    assert "Removal0" in names and "Removal1" in names


def test_one_color_tapped_land_is_not_fixing():
    canopy = sg.CardInfo("Canopy", types=["Land"])
    canopy.makes = {"G"}
    dual = sg.CardInfo("Dual", types=["Land"])
    dual.makes = {"U", "G"}
    pool = [_card(f"C{i}", "{1}{U}", 0.1) for i in range(12)]
    pool += [_card(f"D{i}", "{1}{G}", 0.1) for i in range(12)] + [canopy, dual]
    build = sg._build(pool, "UG", [])
    assert "Dual" in build.lands and "Canopy" not in build.lands
