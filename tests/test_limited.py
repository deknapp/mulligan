"""Pools, the deckbuilder, simulated ratings and the correlation maths."""

from __future__ import annotations

from collections import Counter

from mulligan.arena import Entry, gauntlet
from mulligan.cards.sets import load_set
from mulligan.limited.build import build_deck
from mulligan.limited.pools import sealed_pool
from mulligan.limited.simulate import simulate_ratings
from mulligan.validation.seventeen import pearson, spearman

HOB = load_set("hob")


def test_pools_are_reproducible_and_have_the_rarity_mix():
    pool = sealed_pool(HOB, 3)
    assert pool == sealed_pool(HOB, 3)
    assert pool != sealed_pool(HOB, 4)
    rarity = Counter(HOB.rarity(n) for n in pool)
    assert rarity["rare"] + rarity["mythic"] >= 6
    assert rarity["common"] >= 42


def test_built_deck_is_forty_cards_two_colors_and_castable():
    for seed in range(5):
        deck = build_deck(sealed_pool(HOB, seed), HOB)
        assert len(deck.cards) == 40
        assert sum(1 for c in deck.cards if c.is_land) == 17
        assert len(deck.colors) == 2
        for spec in deck.spells:
            for symbol, _ in spec.cost.pips:
                assert set(symbol.split("/")) & set(deck.colors) or symbol == "C", spec.name


def test_decklist_round_trips_through_the_loader():
    from mulligan.decks import parse_decklist
    deck = build_deck(sealed_pool(HOB, 1), HOB)
    again = parse_decklist(deck.decklist(), HOB.playable)
    assert sorted(c.name for c in again) == sorted(c.name for c in deck.cards)


def test_simulated_ratings_count_cards_in_hand():
    stats = simulate_ratings("hob", n_decks=4, n_games=12, workers=1)
    assert stats
    for s in stats.values():
        assert 0 <= s.wins <= s.games


def test_gauntlet_pairs_candidates_on_identical_slots():
    decks = [build_deck(sealed_pool(HOB, s), HOB) for s in range(3)]
    a = Entry("a", "heuristic", tuple(decks[0].cards))
    field = [Entry("f", "heuristic", tuple(decks[2].cards))]
    result = gauntlet([a, a], field, games_per_opponent=4, workers=1)
    assert result.outcomes[0] == result.outcomes[1]  # same deck, same slots, same games
    mean, low, high = result.paired_difference(0, 1)
    assert mean == low == high == 0


def test_correlations():
    assert abs(spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1) < 1e-9
    assert abs(spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1) < 1e-9
    assert abs(pearson([1, 2, 3], [2, 4, 6]) - 1) < 1e-9


def test_shipped_real_field_is_real_forty_card_decks():
    from mulligan.limited.field import real_field
    for fmt in ("PremierDraft", "Sealed"):
        field = real_field("hob", fmt)
        assert len(field) == 40
        assert all(40 <= sum(d.values()) <= 41 for d in field)
        assert all(set(d) <= set(HOB.entries) | {"Plains", "Island", "Swamp", "Mountain",
                                                 "Forest"} for d in field)


def test_release_day_build_is_a_full_two_color_deck():
    from mulligan.limited.release_day import release_day_build
    advice = release_day_build(sealed_pool(HOB, 7), HOB, simulate=False)
    assert len(advice.best.cards) == 40 and len(advice.best.colors) == 2
    assert advice.result is None
