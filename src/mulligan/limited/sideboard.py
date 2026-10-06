"""Sideboard cards: interaction that only works against some colors ("counter
target white or black spell", "exile target creature that's green or blue").
In best-of-one they are dead against half the decks you meet, so no builder
puts them in a main deck."""

from __future__ import annotations

import re

_COLOR = r"(?:white|blue|black|red|green)"
_COLORS = rf"{_COLOR}(?:,? (?:or|and) {_COLOR})*"
_THING = r"(?:spell|creature|permanent|planeswalker|artifact|enchantment)"
HOSER = re.compile(
    rf"\btarget {_COLORS} {_THING}"            # target white or black spell
    rf"|\btarget [^.]*?{_THING}s?[^.]*? that's {_COLORS}",  # ... that's blue
    re.IGNORECASE)


def is_sideboard_card(oracle: str) -> bool:
    return bool(HOSER.search(oracle or ""))
