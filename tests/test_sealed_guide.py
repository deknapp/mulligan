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
    build = sg._build(pool, "UG")
    names = [c.name for c in build.spells]
    assert "Removal0" in names and "Removal1" in names


def test_one_color_tapped_land_is_not_fixing():
    canopy = sg.CardInfo("Canopy", types=["Land"])
    canopy.makes = {"G"}
    dual = sg.CardInfo("Dual", types=["Land"])
    dual.makes = {"U", "G"}
    pool = [_card(f"C{i}", "{1}{U}", 0.1) for i in range(12)]
    pool += [_card(f"D{i}", "{1}{G}", 0.1) for i in range(12)] + [canopy, dual]
    build = sg._build(pool, "UG")
    assert "Dual" in build.lands and "Canopy" not in build.lands


def test_filler_cost_grows_with_each_filler_card():
    good = [_card(f"G{i}", "{1}{U}", 0.05) for i in range(23)]
    bad = [_card(f"B{i}", "{1}{U}", -0.14) for i in range(4)]
    one = sg.score_deck(good[:22] + bad[:1], [])[1]["filler_cost"]
    four = sg.score_deck(good[:19] + bad, [])[1]["filler_cost"]
    assert four > 4 * one


def test_deep_good_pool_can_beat_one_bomb_plus_filler():
    deep = [_card(f"G{i}", "{1}{U}", 0.08) for i in range(23)]
    bomb = [_card("Bomb", "{3}{U}", 0.40)] + [_card(f"F{i}", "{1}{U}", -0.14)
                                              for i in range(6)]
    bomb += [_card(f"M{i}", "{1}{U}", 0.0) for i in range(16)]
    assert sg.score_deck(deep, [])[0] > sg.score_deck(bomb, [])[0]


def test_roles_from_rules_text():
    removal = sg.CardInfo("Zap", cost="{1}{R}", types=["Instant"],
                          oracle="Zap deals 3 damage to target creature.")
    bounce = sg.CardInfo("Bounce", cost="{U}", types=["Instant"],
                         oracle="Return target creature to its owner's hand.")
    flyer = sg.CardInfo("Drake", cost="{3}{U}", types=["Creature"], power=3,
                        keywords={"flying"})
    assert sg.is_removal(removal) == 1.0 and sg.is_removal(bounce) == 0.5
    assert sg.win_condition(flyer) == "evasive threat"


def test_synergy_payoffs_need_committed_enablers():
    pk = [{"name": "Lifegain", "payoffs": ["P"], "enablers": [f"E{i}" for i in range(9)],
           "min_enablers": 6}]
    def deck(n_enablers):
        return [sg.CardInfo("P")] + [sg.CardInfo(f"E{i}") for i in range(n_enablers)]
    assert sg.deck_synergy(deck(3), pk)[0] == 0
    few, many = sg.deck_synergy(deck(5), pk)[0], sg.deck_synergy(deck(9), pk)[0]
    assert 0 < few < many
    assert "P" in sg.deck_synergy(deck(6), pk)[2]      # a working package isn't filler


def test_colorless_c_minus_is_filler():
    card = sg.CardInfo("Junk", cost="{3}", types=["Artifact"], grade="C-", edge=0.02)
    assert card.colorless and sg.is_filler(card)
    assert not sg.is_filler(sg.CardInfo("Fine", cost="{3}", types=["Artifact"], grade="C",
                                        edge=0.02))
