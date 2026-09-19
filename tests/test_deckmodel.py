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
