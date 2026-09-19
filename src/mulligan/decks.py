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
from dataclasses import dataclass
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


# --------------------------------------------------- Arena decks, by reference


@dataclass
class DeckRef:
    """A deck ready to play, with where it came from and what had to change."""

    label: str
    set_code: str
    cards: list[CardSpec]
    warnings: list[str]
    replaced: int = 0
    names: dict[str, int] | None = None   # the real list, before any replacement

    def fidelity(self) -> tuple[int, int, int]:
        """(exact, approximated, replaced) counts over the deck's nonland cards."""
        spells = [c for c in self.cards if not c.is_land]
        approx = sum(1 for c in spells if c.approximations)
        return len(spells) - approx, approx, self.replaced

    def fidelity_note(self) -> str | None:
        exact, approx, replaced = self.fidelity()
        total = exact + approx + replaced
        if not total:
            return None
        line = (f"{self.label}: {exact} of {total} spells modelled exactly, {approx} "
                f"approximated, {replaced} replaced")
        if (approx + replaced) / total > 0.25:
            line += (" — more than a quarter of this deck is simplified, so treat its "
                     "result as unreliable (synergy decks suffer most)")
        return line


def _substitute(names: dict[str, int], pool: dict[str, CardSpec], label: str
                ) -> tuple[list[CardSpec], list[str], int]:
    """Cards the engine cannot play yet are replaced by a basic land of the
    deck's most-used color, and reported — the deck then plays slightly weaker
    than it really is, never stronger."""
    deck: list[CardSpec] = []
    missing: dict[str, int] = {}
    colors: dict[str, int] = {}
    for name, count in names.items():
        spec = pool.get(name) or BASIC_NAMES.get(name)
        if spec is None:
            missing[name] = count
            continue
        deck.extend([spec] * count)
        for symbol, n in spec.cost.pips:
            for part in symbol.split("/"):
                colors[part] = colors.get(part, 0) + n * count
    warnings = []
    if missing:
        main = max(colors, key=colors.get) if colors else "W"
        land = BASICS.get(main, BASICS["W"])
        for count in missing.values():
            deck.extend([land] * count)
        warnings.append(f"{label}: not playable in the engine yet, replaced by {land.name}: "
                        + ", ".join(f"{n}× {name}" for name, n in missing.items())
                        + " (this deck will play slightly weaker than it is)")
    return deck, warnings, sum(missing.values())


def load_ref(ref: str, set_code: str | None = None) -> DeckRef:
    """``log:N`` / ``log:latest`` (a deck from your Arena log), ``clipboard``
    (Arena's Export button), a decklist file, or a built-in deck name."""
    from .arena_log import arena_names, export_set_code, read_decks, resolve
    from .cards.sets import available_sets, load_set

    if ref.startswith("log:"):
        decks = read_decks()
        which = ref[4:]
        if not decks:
            raise DeckError("no event decks in your Arena log")
        index = len(decks) - 1 if which == "latest" else int(which)
        logged = decks[index]
        code = set_code or logged.set_code
        if code not in available_sets():
            raise DeckError(f"{logged.event}: set {code!r} is not compiled yet "
                            f"(compiled: {', '.join(available_sets())})")
        data = load_set(code)
        ids = {int(e["arena_id"]): name for name, e in data.entries.items()
               if e.get("arena_id")}
        resolve(logged, ids)
        if logged.unresolved:
            ids.update(arena_names(list(logged.unresolved)))
            resolve(logged, ids)
        cards, warnings, replaced = _substitute(logged.names, data.playable, f"log:{index}")
        if logged.unresolved:
            warnings.append(f"log:{index}: {sum(logged.unresolved.values())} card(s) with "
                            f"unknown Arena ids were left out")
        return DeckRef(f"log:{index} ({logged.event})", code, cards, warnings, replaced,
                       dict(logged.names))
    if ref == "clipboard":
        import subprocess
        text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        label = "clipboard"
    elif Path(ref).exists():
        text = Path(ref).read_text()
        label = ref
    elif ref in DECKS:
        return DeckRef(ref, "", list(DECKS[ref]), [])
    else:
        raise DeckError(f"no deck {ref!r}: use log:N, log:latest, clipboard, or a file")
    code = set_code or export_set_code(text)
    if code is None or code not in available_sets():
        raise DeckError(f"{label}: cannot tell which compiled set this deck is from "
                        f"(found {code!r}; compiled: {', '.join(available_sets())}); "
                        f"pass --set")
    names: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower() in ("deck", "main", "maindeck", "companion", "commander"):
            continue
        if line.lower().startswith("sideboard"):
            break
        match = LINE.match(line)
        if not match:
            raise DeckError(f"{label}: cannot read decklist line {raw!r}")
        name = match.group(2).strip().split(" // ")[0]
        names[name] = names.get(name, 0) + int(match.group(1))
    cards, warnings, replaced = _substitute(names, load_set(code).playable, label)
    return DeckRef(label, code, cards, warnings, replaced, names)
