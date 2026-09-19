"""Build advice with no real data: the release-day case.

Before 17Lands has games for a set, there is no deck model. What there is:
simulated card ratings (``mulligan rate``, shipped per set) and a simulator
that is validated for comparing *versions of one deck* (it agreed with
real-game values on 77% of build changes in HOB). So:

1. Rate every card by its simulated win rate (shrunk toward the
   text-derived rating when few games back it).
2. Recommend the best two-color build by those ratings.
3. Optionally, show the top few candidates played against the same field of
   the set's sealed decks on the same shuffles.

Measured on HOB, judged by the real-data deck model over 20 sealed pools:
plain text-rated builder 53.2%, this recommendation 55.9%, the real-data
model's own pick 65.0%. Letting the simulation choose did not help (55.4%
among two-color builds, 54.6% when it could pick a splash: bots handle three
colors better than people do), so the simulation is information, not the
decider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from ..arena import Entry, GauntletResult, gauntlet
from ..cards.sets import SetData
from .build import Deck, build_deck
from .field import sealed_field
from .rating import static_rating

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"


def sim_ratings(set_code: str) -> dict[str, tuple[float, int]]:
    path = MODEL_DIR / f"{set_code.lower()}_sim_ratings.json"
    if not path.exists():
        return {}
    return {name: (v[0], v[1]) for name, v in json.loads(path.read_text()).items()}


def rating_points(data: SetData, ratings: dict[str, tuple[float, int]],
                  weight: float = 60.0, shrink: int = 300) -> dict[str, float]:
    """Deckbuilder points: the text-derived rating, moved by how far the card's
    simulated win rate sits from the set's average (shrunk when few games)."""
    total = sum(n for _, n in ratings.values()) or 1
    mean = sum(r * n for r, n in ratings.values()) / total
    points = {}
    for name, spec in data.playable.items():
        base = static_rating(spec)
        if name in ratings:
            rate, n = ratings[name]
            base += weight * (rate - mean) * n / (n + shrink)
        points[name] = base
    return points


@dataclass
class ReleaseDayAdvice:
    best: Deck                      # the recommendation: top-rated two-color build
    candidates: list[Deck]
    result: GauntletResult | None   # simulated comparison, for information


def candidate_builds(pool: list[str], data: SetData, points: dict[str, float],
                     top: int = 3, splash: bool = False) -> list[Deck]:
    """The best ``top - 1`` two-color builds plus the best three-color build,
    so a splash is always tested against staying in two colors."""
    def best(combos: list[str], n: int) -> list[Deck]:
        decks = [d for d in (build_deck(pool, data, points, colors=c) for c in combos)
                 if len(d.spells) >= 23]
        decks.sort(key=lambda d: -d.score)
        chosen: list[Deck] = []
        seen: set[tuple[str, ...]] = set()
        for deck in decks:
            key = tuple(sorted(c.name for c in deck.cards))
            if key not in seen:
                seen.add(key)
                chosen.append(deck)
            if len(chosen) == n:
                break
        return chosen

    pairs = ["".join(c) for c in combinations("WUBRG", 2)]
    triples = ["".join(c) for c in combinations("WUBRG", 3)]
    two = best(pairs, top if not splash else max(1, top - 1))
    return two + (best(triples, 1) if splash or not two else [])


def release_day_build(pool: list[str], data: SetData, top: int = 3, field_size: int = 16,
                      games_per_opponent: int = 8, seed: int = 0, simulate: bool = True,
                      workers: int | None = None) -> ReleaseDayAdvice:
    points = rating_points(data, sim_ratings(data.code))
    candidates = candidate_builds(pool, data, points, top)
    if not candidates:
        raise ValueError("no color combination has 23 playable spells in this pool")
    if not simulate:
        return ReleaseDayAdvice(candidates[0], candidates, None)
    field = [Entry(f"field{i}", "heuristic", tuple(d.cards))
             for i, d in enumerate(sealed_field(data, field_size, seed, points))]
    entries = [Entry(f"{d.colors}#{i}", "heuristic", tuple(d.cards))
               for i, d in enumerate(candidates)]
    result = gauntlet(entries, field, games_per_opponent=games_per_opponent, seed=seed,
                      workers=workers)
    return ReleaseDayAdvice(candidates[0], candidates, result)
