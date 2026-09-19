"""Primitive vocabulary: colors, mana, zones, steps, keywords, object references.

Nothing in here knows about game state. Everything else in the engine is built
out of these.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class Color(str, Enum):
    WHITE = "W"
    BLUE = "U"
    BLACK = "B"
    RED = "R"
    GREEN = "G"


COLORLESS = "C"
"""Generic/colorless mana in a pool. Not a Color: {C} cannot pay {G}."""

MANA_SYMBOLS = [c.value for c in Color] + [COLORLESS]


class CardType(str, Enum):
    LAND = "Land"
    CREATURE = "Creature"
    INSTANT = "Instant"
    SORCERY = "Sorcery"
    ARTIFACT = "Artifact"
    ENCHANTMENT = "Enchantment"
    PLANESWALKER = "Planeswalker"


class Zone(str, Enum):
    LIBRARY = "library"
    HAND = "hand"
    BATTLEFIELD = "battlefield"
    GRAVEYARD = "graveyard"
    EXILE = "exile"
    STACK = "stack"


class Step(str, Enum):
    """Turn structure. Untap and cleanup grant no priority in real Magic and
    grant none here either; the engine passes through them."""

    UNTAP = "untap"
    UPKEEP = "upkeep"
    DRAW = "draw"
    PRECOMBAT_MAIN = "precombat_main"
    BEGIN_COMBAT = "begin_combat"
    DECLARE_ATTACKERS = "declare_attackers"
    DECLARE_BLOCKERS = "declare_blockers"
    COMBAT_DAMAGE = "combat_damage"
    END_COMBAT = "end_combat"
    POSTCOMBAT_MAIN = "postcombat_main"
    END_STEP = "end_step"
    CLEANUP = "cleanup"


TURN_SEQUENCE: list[Step] = list(Step)

MAIN_PHASES = {Step.PRECOMBAT_MAIN, Step.POSTCOMBAT_MAIN}


class Keyword(str, Enum):
    FLYING = "flying"
    REACH = "reach"
    VIGILANCE = "vigilance"
    HASTE = "haste"
    TRAMPLE = "trample"
    DEATHTOUCH = "deathtouch"
    LIFELINK = "lifelink"
    FIRST_STRIKE = "first strike"
    DOUBLE_STRIKE = "double strike"
    MENACE = "menace"
    DEFENDER = "defender"
    FLASH = "flash"
    HEXPROOF = "hexproof"
    INDESTRUCTIBLE = "indestructible"
    PROWESS = "prowess"
    CONVOKE = "convoke"


@dataclass(frozen=True)
class ManaCost:
    """A mana cost: some generic, plus colored pips.

    ``ManaCost.parse("3UU")`` is three generic and two blue; Scryfall's brace
    form ``"{3}{U}{U}"`` parses the same. A hybrid pip is written ``"B/G"`` and
    can be paid with either color. ``"0"`` or ``""`` is free. ``{X}`` is kept as
    ``has_x`` and costs nothing extra: cards that scale with X are not
    supported by the engine, and the set compiler marks them so.
    """

    generic: int = 0
    pips: tuple[tuple[str, int], ...] = ()
    has_x: bool = False

    @staticmethod
    def parse(text: str) -> ManaCost:
        text = (text or "").strip().upper()
        if "{" in text:
            symbols = [part for part in text.replace("}", "").split("{") if part]
        else:
            symbols = []
            digits = ""
            for ch in text:
                if ch.isdigit():
                    digits += ch
                    continue
                if digits:
                    symbols.append(digits)
                    digits = ""
                symbols.append(ch)
            if digits:
                symbols.append(digits)
        generic = 0
        has_x = False
        counts: Counter[str] = Counter()
        for sym in symbols:
            if sym.isdigit():
                generic += int(sym)
            elif sym == "X":
                has_x = True
            elif sym in MANA_SYMBOLS:
                counts[sym] += 1
            elif "/" in sym and all(part in MANA_SYMBOLS for part in sym.split("/")):
                counts["/".join(sorted(sym.split("/"), key=MANA_SYMBOLS.index))] += 1
            else:
                raise ValueError(f"unparseable mana symbol {sym!r} in {text!r}")
        return ManaCost(generic=generic, pips=tuple(sorted(counts.items())), has_x=has_x)

    @property
    def mana_value(self) -> int:
        """Converted mana cost."""
        return self.generic + sum(n for _, n in self.pips)

    @property
    def colors(self) -> frozenset[Color]:
        return frozenset(Color(part) for sym, _ in self.pips for part in sym.split("/")
                         if part != COLORLESS)

    def reduced(self, amount: int) -> ManaCost:
        """This cost with up to ``amount`` generic mana taken off."""
        return ManaCost(max(0, self.generic - amount), self.pips, self.has_x)

    def plus(self, other: ManaCost) -> ManaCost:
        merged: Counter[str] = Counter(dict(self.pips))
        merged.update(dict(other.pips))
        return ManaCost(self.generic + other.generic, tuple(sorted(merged.items())),
                        self.has_x or other.has_x)

    def __str__(self) -> str:
        if self.mana_value == 0 and not self.has_x:
            return "{0}"
        head = "{X}" if self.has_x else ""
        head += f"{{{self.generic}}}" if self.generic else ""
        tail = "".join(f"{{{sym}}}" * n for sym, n in self.pips)
        return head + tail


def pip_options(pip: str) -> tuple[str, ...]:
    """The mana symbols that can pay one pip (two for a hybrid)."""
    return tuple(pip.split("/"))


class ManaPool(Counter):
    """Mana available to a player right now. Keys are ``MANA_SYMBOLS``."""

    def add(self, symbol: str, amount: int = 1) -> None:
        if symbol not in MANA_SYMBOLS:
            raise ValueError(f"not a mana symbol: {symbol!r}")
        self[symbol] += amount

    def total(self) -> int:
        return sum(self.values())


@dataclass(frozen=True)
class Target:
    """A reference to something a spell or ability can point at.

    ``kind`` is ``"player"`` (``id`` is the seat index) or ``"object"``
    (``id`` is a game-object id, on the battlefield or on the stack).
    """

    kind: str
    id: int

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


def player_target(seat: int) -> Target:
    return Target("player", seat)


def object_target(obj_id: int) -> Target:
    return Target("object", obj_id)


@dataclass(frozen=True)
class TargetSpec:
    """What a spell or ability demands of one of its targets.

    ``selector`` is ``any_target``, ``player``, ``opponent``, ``spell`` (or
    ``spell:<filter>``), or a filter string (see ``engine.filters``) naming a
    permanent or a card in a graveyard. Keeping the predicate out of the card
    data means card definitions stay declarative and comparable, and there is
    exactly one place where "what is a legal target" is decided.

    ``optional`` is "up to one target": choosing nothing is legal.
    """

    selector: str
    description: str = ""
    optional: bool = False

    def label(self) -> str:
        return self.description or self.selector.replace("_", " ")


NO_TARGET = Target("none", 0)
"""Fills the slot of an optional target that was not chosen, or of a target
that became illegal before resolution, so later targets keep their positions."""


def dedupe(items: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
