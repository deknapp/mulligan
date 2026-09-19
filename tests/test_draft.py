from collections import Counter

from mulligan.cards.sets import load_set
from mulligan.limited.build import build_deck
from mulligan.limited.draft import card_colors, draft_pod, simulate_draft
from mulligan.limited.release_day import rating_points, sim_ratings


def test_pod_deals_every_card_once():
    data = load_set("fra")
    ratings = rating_points(data, sim_ratings("fra"))
    pools, taken_at = draft_pod(data, ratings, seed=1)
    assert len(pools) == 8
    assert len({len(p) for p in pools}) == 1 and len(pools[0]) >= 36
    assert sum(len(v) for v in taken_at.values()) == sum(len(p) for p in pools)


def test_drafters_settle_into_colors():
    data = load_set("fra")
    ratings = rating_points(data, sim_ratings("fra"))
    on_color = []
    for seed in range(3):
        pools, _ = draft_pod(data, ratings, seed=seed)
        for pool in pools:
            deck = build_deck(pool, data, ratings)
            assert len(deck.cards) == 40
            colored = [c for c in pool if card_colors(data, c)]
            on_color.append(sum(card_colors(data, c) <= set(deck.colors) for c in colored)
                            / len(colored))
    assert sum(on_color) / len(on_color) > 0.6


def test_bots_do_not_all_draft_the_same_colors():
    data = load_set("fra")
    ratings = rating_points(data, sim_ratings("fra"))
    pools, _ = draft_pod(data, ratings, seed=4)
    pairs = Counter(build_deck(p, data, ratings).colors for p in pools)
    assert len(pairs) >= 4


def test_simulate_draft_small():
    stats = simulate_draft("fra", pods=1, games=16, workers=1)
    assert len(stats.decks) == 8
    assert sum(n for _, n in stats.records.values()) == 32
    assert any(stats.gih(n) > 0 for n in stats.card_games)
    assert sum(g for _, g in stats.deck_records) == 32
    assert stats.play_records[1] == 16 and 0 <= stats.play_records[0] <= 16


def test_land_counts_small():
    from mulligan.limited.lands import land_counts
    data = load_set("fra")
    ratings = rating_points(data, sim_ratings("fra"))
    res = land_counts("fra", ratings, decks=2, opponents=3, games_per_opponent=2, workers=1)
    assert res.decks == 2 and set(res.win) == {16, 17, 18}
    assert res.diff[17] == (0.0, 0.0, 0.0)
