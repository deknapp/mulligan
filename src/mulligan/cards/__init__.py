"""Card definitions.

``cube`` is a small hand-written pool that exists so the engine has a fixture
that is known-correct by inspection. Real sets are compiled from Scryfall oracle
text into the same declarative form — see ``mulligan.cards.compiler``.
"""

from .cube import CUBE, DECKS, basic_land, build_deck, card  # noqa: F401
