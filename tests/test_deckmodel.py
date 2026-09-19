"""The 17Lands deck model's arithmetic, and the shipped HOB models."""

from __future__ import annotations

from mulligan.deckmodel import DeckModel


def _model() -> DeckModel:
    return DeckModel("hob", "PremierDraft", {"Bomb": 0.2, "Dud": -0.1, "Filler": 0.0},
                     intercept=0.1, play=0.4, skill=0.4)


def test_head_to_head_is_symmetric_and_favours_the_better_deck():
    m = _model()
    good, bad = {"Bomb": 2, "Filler": 21}, {"Dud": 2, "Filler": 21}
    p = m.head_to_head(good, bad)
    assert p > 0.5
    assert abs(p + m.head_to_head(bad, good) - 1) < 1e-12
    assert m.head_to_head(good, good) == 0.5


def test_unknown_cards_are_reported():
    assert _model().unknown({"Bomb": 1, "Mystery": 1}) == ["Mystery"]


def test_shipped_hob_models_load_and_know_every_card():
    from mulligan.cards.sets import load_set
    names = set(load_set("hob").entries)
    for fmt in ("PremierDraft", "Sealed"):
        m = DeckModel.load("hob", fmt)
        assert names <= set(m.weights) | {"Plains", "Island", "Swamp", "Mountain", "Forest"}
        assert m.meta["test_log_loss"] < m.meta["baseline_log_loss"]


def test_differences_and_interval():
    m = _model()
    m.bootstrap = [{"Bomb": 0.18, "Dud": -0.1}, {"Bomb": 0.22, "Dud": -0.12},
                   {"Bomb": 0.2, "Dud": -0.08}, {"Bomb": 0.21, "Dud": -0.1}]
    a, b = {"Bomb": 1, "Filler": 22}, {"Dud": 1, "Filler": 22}
    diffs = {name: (delta, effect) for name, delta, effect in m.differences(a, b)}
    assert diffs["Bomb"][0] == 1 and diffs["Bomb"][1] > 0
    assert diffs["Dud"][0] == -1 and diffs["Dud"][1] > 0   # B's dud helps A
    assert "Filler" not in diffs
    low, high = m.head_to_head_interval(a, b)
    assert low < m.head_to_head(a, b) < high and low > 0.5


def test_best_build_is_always_a_full_deck():
    from mulligan.cards.sets import load_set
    from mulligan.limited.advise import best_build
    from mulligan.limited.pools import sealed_pool
    data = load_set("hob")
    model = DeckModel.load("hob")
    for seed in range(3):
        advice = best_build(sealed_pool(data, seed), data, model)
        assert sum(advice.best.values()) == 40
        spells = sum(n for name, n in advice.best.items()
                     if "Land" not in data.entries.get(name, {}).get("types", ["Land"]))
        assert spells == 23


def test_hybrid_pips_do_not_add_a_color():
    from mulligan.deckmodel import _card_info, structure
    info = _card_info("hob")
    # Duskwatch Hunter is {2}{B/G}: in a blue-green deck it needs no third color.
    ug = {"Duskwatch Hunter": 2, "Mirkwood Nurturer": 2, "Elvenking's Harper": 2,
          "Ordinary Bear": 17, "Forest": 9, "Island": 8}
    assert "shape:colors>=3" not in structure(ug, info)
    ugb = {**ug, "Bilbo's Deadly Slice": 1}
    assert "shape:colors>=3" in structure(ugb, info)
