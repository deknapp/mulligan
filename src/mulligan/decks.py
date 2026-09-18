"""Loading decks: built-in cube decks by name, or decklist files.

A decklist file is the format Arena and most deckbuilders export::

    Deck
    2 Grizzly Bears
    1 Lightning Bolt (FDN) 123
    8 Forest

The set code and collector number after a name are ignored; the card is looked
up by name in the set given (or in the built-in cube). Sideboard sections are
skipped. Unknown or unsupported cards are an error rather than a silent skip,
because a deck missing cards is a different deck.
"""

from __future__ import annotations

import re
from pathlib import Path

from .cards.cube import BASICS, CUBE, DECKS
from .engine.card import CardSpec

LINE = re.compile(r"^\s*(\d+)x?\s+(.+?)(?:\s+\([A-Za-z0-9]+\)(?:\s+\S+)?)?\s*$")
BASIC_NAMES = {spec.name: spec for spec in BASICS.values()}


class DeckError(ValueError):
    pass


def card_pool(set_code: str | None) -> dict[str, CardSpec]:
    if not set_code:
        return CUBE
    from .cards.sets import load_set
    return load_set(set_code).playable


def parse_decklist(text: str, pool: dict[str, CardSpec]) -> list[CardSpec]:
    deck: list[CardSpec] = []
    missing: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower() in ("deck", "main", "maindeck", "companion"):
            continue
        if line.lower().startswith("sideboard"):
            break
        match = LINE.match(line)
        if not match:
            raise DeckError(f"cannot read decklist line: {raw!r}")
        count, name = int(match.group(1)), match.group(2).strip()
        spec = pool.get(name) or BASIC_NAMES.get(name)
        if spec is None:
            missing.append(name)
            continue
        deck.extend([spec] * count)
    if missing:
        raise DeckError("not playable in this engine yet: " + ", ".join(sorted(set(missing))))
    return deck


def load_deck(ref: str, set_code: str | None = None) -> list[CardSpec]:
    if ref in DECKS and not Path(ref).exists():
        return list(DECKS[ref])
    path = Path(ref)
    if not path.exists():
        known = ", ".join(sorted(DECKS))
        raise DeckError(f"no deck {ref!r}: not a file, and not a built-in deck ({known})")
    return parse_decklist(path.read_text(), card_pool(set_code))
