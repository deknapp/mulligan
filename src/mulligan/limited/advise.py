"""Build advice from real results: the best deck a pool allows, by the
17Lands deck model, compared with the deck actually played.

Every card counts here — including ones the simulator cannot play — because
the model's card values come from real games, not from the rules engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..cards.schema import card as card_from_entry
from ..cards.sets import SetData
from ..deckmodel import DeckModel
from ..engine.card import CardSpec
from .build import build_deck


def _spec(data: SetData, name: str) -> CardSpec | None:
    if name in data.playable:
        return data.playable[name]
    entry = data.entries.get(name)
    if entry is None:
        return None
    # An unsupported card still has a cost and types: enough to build with.
    skeleton = {k: v for k, v in entry.items() if k in ("name", "cost", "types", "subtypes",
                                                         "supertypes", "power", "toughness",
                                                         "keywords", "mana")}
    return card_from_entry(skeleton)


@dataclass
class Advice:
    best: dict[str, int]
    colors: str
    alternatives: list[tuple[str, float]]  # other color pairs and their vs-field win rate


def best_build(pool: list[str], data: SetData, model: DeckModel,
               colors: str | None = None) -> Advice:
    playable = dict(data.playable)
    for name in set(pool):
        spec = _spec(data, name)
        if spec is not None:
            playable[name] = spec
    view = SetData(data.code, data.name, playable, data.entries)
    # Ratings on the deckbuilder's scale: points ~ 60 x log-odds per copy.
    ratings = {n: 60 * w for n, w in model.weights.items()}
    from itertools import combinations
    candidates = [colors] if colors else (
        ["".join(c) for c in combinations("WUBRG", 2)]
        + ["".join(c) for c in combinations("WUBRG", 3)])
    options = []
    for combo in candidates:
        deck = build_deck(pool, view, ratings, colors=combo)
        if len(deck.spells) < 23:
            continue  # not enough playables in these colors for a real deck
        names: dict[str, int] = {}
        for spec in deck.cards:
            names[spec.name] = names.get(spec.name, 0) + 1
        rate = model.vs_field(names)
        # A third color costs consistency the model cannot see (it rates cards,
        # not mana); only take one when it is clearly better.
        penalty = 0.02 if len(combo) == 3 else 0.0
        options.append((rate - penalty, rate, combo, names))
    if not options:
        raise ValueError("no color combination has 23 playable spells in this pool")
    options.sort(reverse=True)
    _, best_rate, best_combo, best_names = options[0]
    return Advice(best_names, best_combo, [(c, r) for _, r, c, _ in options[1:4]])
