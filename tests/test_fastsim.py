from mulligan import fastsim


def _values():
    return fastsim.CardValues.from_gih({"Bomb": (0.65, 10000), "Dud": (0.48, 10000),
                                        "Rare": (0.70, 10)}, shrink=300)


def test_values_center_on_the_average_and_shrink_small_samples():
    v = _values()
    assert v.value("Bomb") > 0 > v.value("Dud")
    assert abs(v.value("Rare")) < 0.05  # 10 games: mostly shrunk away
    assert v.value("Unknown") == 0.0


def test_better_cards_win_more_and_mirrors_are_even():
    v = _values()
    is_land = lambda n: n == "Island"  # noqa: E731
    bombs = fastsim.Deck.from_names({"Bomb": 23, "Island": 17}, v, is_land)
    duds = fastsim.Deck.from_names({"Dud": 23, "Island": 17}, v, is_land)
    assert fastsim.head_to_head(bombs, duds, 4000) > 0.8
    assert abs(fastsim.head_to_head(bombs, bombs, 4000) - 0.5) < 0.02


def test_too_few_lands_costs_games():
    v = _values()
    is_land = lambda n: n == "Island"  # noqa: E731
    normal = fastsim.Deck.from_names({"Dud": 23, "Island": 17}, v, is_land)
    starved = fastsim.Deck.from_names({"Dud": 33, "Island": 7}, v, is_land)
    assert fastsim.deck_score(normal) > fastsim.deck_score(starved)
