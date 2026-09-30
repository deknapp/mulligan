"""Pick advice beyond the card's own rating: your pool, colors and mana."""

from mulligan.pick_advice import (
    DRAWN_TOGETHER,
    SYNERGY_WEIGHT,
    advise,
    build_ratings,
    commitment,
    expert_ratings,
    grade_points,
    land_colors,
    lane,
    lanes,
    pips,
    pool_synergy,
)


def _card(name, colors, wr=0.55, cost="", t="Creature", o=""):
    return {"n": name, "c": colors, "t": t, "w": 1000 * wr, "g": 1000, "cost": cost, "o": o}


def _sim(cards, syn=()):
    names = [c["n"] for c in cards]
    flat = [x for a, b, e in syn for x in (names.index(a), names.index(b), e)]
    return {"run": "2026-09-29", "mean": 0.5, "prior": 0, "cards": cards, "pc": {},
            "syn": {"a": flat}}


RG = [_card(f"R{i}", "R", cost="{1}{R}") for i in range(7)] + \
     [_card(f"G{i}", "G", cost="{1}{G}") for i in range(7)]
FILLER = [_card(f"F{i}", "B", wr=0.45 + 0.005 * i) for i in range(20)]


def test_pips_and_land_colors():
    assert pips("{2}{R}{R}") == {"R": 2}
    assert pips("{W/U}{2/B}{G}") == {"W": 0.5, "U": 0.5, "G": 1}
    assert land_colors("This land enters tapped.\n{T}: Add {R} or {W}.") == "WR"
    assert land_colors("{T}: Add {C}.") == ""
    assert land_colors("As it enters, choose a color.\n{T}: Add one mana of the chosen "
                       "color.") == "WUBRG"


def test_commitment_needs_both_picks_and_concentration():
    ratings = build_ratings(_sim(RG + FILLER), None)
    settled, _ = lane(ratings, [c["n"] for c in RG])
    assert commitment(settled, 14) == 1.0
    assert commitment(settled, 7) == 0.5
    spread = dict.fromkeys("WUBRG", 1.0)
    assert commitment(spread, 14) == 0.0


def test_synergy_with_the_pool_breaks_a_tie():
    pack = [_card("Partner", "R"), _card("Plain", "R")]
    ratings = build_ratings(_sim(RG + FILLER + pack, [("Partner", "R0", 30)]), None)
    picks = [c["n"] for c in RG]
    prob = lanes(ratings, picks).prob
    total, parts = pool_synergy(ratings, "Partner", picks, prob)
    red = sum(q for p, q in prob.items() if "R" in p)
    assert abs(total - SYNERGY_WEIGHT * DRAWN_TOGETHER * 0.030 * red) < 1e-9
    assert parts[0][0] == "R0"
    ranked = advise(ratings, ["Plain", "Partner"], picks)
    assert ranked[0].card.name == "Partner"
    assert any("Synergy with your pool" in w and "R0" in w for w in ranked[0].why)


def test_synergy_counts_less_with_off_color_pool_cards():
    pack = [_card("Partner", "R")]
    ratings = build_ratings(_sim(RG + FILLER + pack, [("Partner", "F0", 30),
                                                      ("Partner", "R0", 30)]), None)
    picks = [c["n"] for c in RG] + ["F0"]
    parts = dict(pool_synergy(ratings, "Partner", picks, lanes(ratings, picks).prob)[1])
    assert parts["F0"] < parts["R0"]


def test_heavy_off_color_mana_cant_be_splashed():
    pack = [_card("Splashy", "U", wr=0.60, cost="{3}{U}"),
            _card("Heavy", "U", wr=0.60, cost="{1}{U}{U}")]
    ratings = build_ratings(_sim(RG + FILLER + pack), None)
    ranked = advise(ratings, ["Heavy", "Splashy"], [c["n"] for c in RG] * 2)
    assert [a.card.name for a in ranked] == ["Splashy", "Heavy"]
    assert any("splashed" in w for w in ranked[0].why)


def test_fixing_in_the_pool_makes_a_splash_worth_more():
    dual = _card("UG Dual", "", t="Land", o="{T}: Add {G} or {U}.")
    pack = [_card("Blue", "U", wr=0.62, cost="{3}{U}")]
    ratings = build_ratings(_sim(RG + FILLER + pack + [dual]), None)
    picks = [c["n"] for c in RG] * 2
    plain = advise(ratings, ["Blue"], picks)[0].score
    fixed = advise(ratings, ["Blue"], picks + ["UG Dual"])[0].score
    assert fixed > plain


def test_dual_lands_are_rated_by_your_colors_and_splash():
    lands = [_card("RG Dual", "", t="Land", o="{T}: Add {R} or {G}."),
             _card("GU Dual", "", t="Land", o="{T}: Add {G} or {U}."),
             _card("WB Dual", "", t="Land", o="{T}: Add {W} or {B}.")]
    bomb = _card("Blue Bomb", "U", wr=0.70, cost="{3}{U}")
    ratings = build_ratings(_sim(RG + FILLER + lands + [bomb]), None)
    picks = [c["n"] for c in RG] * 2
    by = {a.card.name: a for a in advise(ratings, ["RG Dual", "GU Dual", "WB Dual"], picks)}
    assert by["RG Dual"].score > 10 * by["WB Dual"].score
    assert by["RG Dual"].score > 10 * by["GU Dual"].score      # nothing to splash yet
    by = {a.card.name: a for a in advise(ratings, ["RG Dual", "GU Dual"],
                                         picks + ["Blue Bomb"])}
    assert by["GU Dual"].score > by["RG Dual"].score
    assert any("Blue Bomb" in w for w in by["GU Dual"].why)


def test_a_full_deck_makes_another_playable_worth_little():
    pack = [_card("Red", "R")]
    ratings = build_ratings(_sim(RG + FILLER + pack), None)
    few = advise(ratings, ["Red"], [c["n"] for c in RG])[0].score
    full = advise(ratings, ["Red"], [c["n"] for c in RG] * 3)[0].score
    assert few > full >= 0


def test_an_off_color_bomb_fades_as_the_draft_goes_on():
    pack = [_card("Blue Bomb", "U", wr=0.62, cost="{3}{U}{U}"), _card("Red", "R", wr=0.56)]
    ratings = build_ratings(_sim(RG + FILLER + pack), None)
    rg = [c["n"] for c in RG]
    early = {a.card.name: a.score for a in advise(ratings, ["Blue Bomb", "Red"], rg[:3])}
    late = {a.card.name: a.score for a in advise(ratings, ["Blue Bomb", "Red"], rg * 2)}
    assert early["Blue Bomb"] > early["Red"]
    assert late["Blue Bomb"] < late["Red"]


def test_a_first_pick_bomb_pulls_the_next_picks_by_how_good_it_is():
    """P1P1 a bomb: a slightly worse card in its color beats a slightly better
    one elsewhere at pick 2, and a bigger bomb pulls harder than a mere good card."""
    cards = RG + FILLER + [_card("Bomb", "R", wr=0.65), _card("Good", "R", wr=0.57),
                           _card("Red B", "R", wr=0.555), _card("Blue B+", "U", wr=0.565)]
    ratings = build_ratings(_sim(cards), None)
    ranked = advise(ratings, ["Blue B+", "Red B"], ["Bomb"])
    assert ranked[0].card.name == "Red B"
    pull = {p: lanes(ratings, [p]).prob for p in ("Bomb", "Good")}
    red = [p for p in pull["Bomb"] if "R" in p]
    assert sum(pull["Bomb"][p] for p in red) > sum(pull["Good"][p] for p in red) > 0.4
    assert ranked[0].why[1].startswith("In your final colors")


def test_first_pick_takes_the_bomb_over_a_flexible_good_card():
    cards = RG + FILLER + [_card("Gold Bomb", "RG", wr=0.66, cost="{2}{R}{G}"),
                           _card("Mono B", "U", wr=0.565), _card("Artifact", "", wr=0.56)]
    ratings = build_ratings(_sim(cards), None)
    ranked = advise(ratings, ["Mono B", "Artifact", "Gold Bomb"], [])
    assert ranked[0].card.name == "Gold Bomb"


def test_expert_grades_are_convex():
    assert grade_points(12) - grade_points(9) > 2 * (grade_points(6) - grade_points(5))
    experts = {"cards": {n: {"grades": [{"show": "S", "host": "H", "grade": g}]}
                         for n, g in (("R0", "A+"), ("R1", "B+"), ("R2", "C+"), ("R3", "C"))}}
    ratings = expert_ratings(_sim(RG + FILLER), experts)
    est = {n: ratings.get(n).est for n in ("R0", "R1", "R2", "R3")}
    assert est["R0"] - est["R1"] > 3 * (est["R2"] - est["R3"])
    assert ratings.get("G0").est is None


def test_pair_bonus_is_measured_against_cards_in_that_pair():
    cards = RG + FILLER + [_card("Star", "R"), _card("Dud", "R")]
    sim = _sim(cards)
    sim["pc"] = {"Star": {"RG": [600, 1000]}, "Dud": {"RG": [500, 1000]},
                 "R0": {"RG": [550, 1000]}}
    ratings = build_ratings(sim, None)
    assert abs(ratings.pair_mean["RG"] - 0.55) < 1e-9
    by = {a.card.name: a for a in advise(ratings, ["Star", "Dud"], [c["n"] for c in RG])}
    assert by["Star"].score > by["Dud"].score
