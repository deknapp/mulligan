"""The field: the decks a deck will actually face in a set's Limited format.

Built by opening sealed pools of the set and building each with the same
deckbuilder, so "better against the field" means better against the decks this
set produces, not against a hand-picked opponent.
"""

from __future__ import annotations

from ..cards.sets import SetData
from .build import Deck, build_deck
from .pools import sealed_pool

FIELD_SEED_BASE = 1_000_000
"""Field pools use their own seed range, so they never coincide with a pool a
user generates with a small seed."""


def sealed_field(data: SetData, size: int = 24, seed: int = 0,
                 ratings: dict[str, float] | None = None) -> list[Deck]:
    return [build_deck(sealed_pool(data, FIELD_SEED_BASE + seed + i), data, ratings)
            for i in range(size)]
