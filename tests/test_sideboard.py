"""Color-hate cards never make a main deck."""

import json
from pathlib import Path

from mulligan.cards.sets import load_set
from mulligan.limited.build import build_deck
from mulligan.limited.sideboard import is_sideboard_card

DATA = Path(__file__).resolve().parent.parent / "src" / "mulligan" / "cards" / "data"
HOSERS = {"Refute Destiny", "Precise Redaction", "Terminal Criticism", "Essence Burn",
          "Flourishing Grapple"}


def test_finds_exactly_the_fra_hosers():
    cards = json.loads((DATA / "fra.json").read_text())["cards"]
    assert {c["name"] for c in cards if is_sideboard_card(c["oracle"])} == HOSERS


def test_builder_leaves_them_out():
    data = load_set("fra")
    pool = [n for n in data.playable if not data.playable[n].is_land]
    deck = build_deck(pool * 2, data)
    assert not HOSERS & {c.name for c in deck.cards}
