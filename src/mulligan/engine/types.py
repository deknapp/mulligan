"""Primitive vocabulary: colors, mana, zones, steps, keywords, object references.

Nothing in here knows about game state. Everything else in the engine is built
out of these.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


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


@dataclass(frozen=True)
class ManaCost:
    """A mana cost: some generic, plus colored pips.

    ``ManaCost.parse("3UU")`` is three generic and two blue. ``"0"`` is free.
    """

    generic: int = 0
    pips: tuple[tuple[str, int], ...] = ()

    @staticmethod
    def parse(text: str) -> ManaCost:
        generic = 0
        counts: Counter[str] = Counter()
        digits = ""
        for ch in text.strip().upper():
            if ch.isdigit():
                digits += ch
            elif ch in MANA_SYMBOLS:
                if digits:
                    generic += int(digits)
                    digits = ""
                counts[ch] += 1
            else:
                raise ValueError(f"unparseable mana symbol {ch!r} in {text!r}")
        if digits:
            generic += int(digits)
        return ManaCost(generic=generic, pips=tuple(sorted(counts.items())))

    @property
    def mana_value(self) -> int:
        """Converted mana cost."""
        return self.generic + sum(n for _, n in self.pips)

    @property
    def colors(self) -> frozenset[Color]:
        return frozenset(Color(sym) for sym, _ in self.pips if sym != COLORLESS)

    def __str__(self) -> str:
        if self.mana_value == 0:
            return "{0}"
        head = f"{{{self.generic}}}" if self.generic else ""
        tail = "".join(f"{{{sym}}}" * n for sym, n in self.pips)
        return head + tail


class ManaPool(Counter):
    """Mana available to a player right now. Keys are ``MANA_SYMBOLS``."""

    def add(self, symbol: str, amount: int = 1) -> None:
        if symbol not in MANA_SYMBOLS:
            raise ValueError(f"not a mana symbol: {symbol!r}")
        self[symbol] += amount

    def total(self) -> int:
        return sum(self.values())

    def can_pay(self, cost: ManaCost) -> bool:
        return self._pay(cost, commit=False) is not None

    def pay(self, cost: ManaCost) -> None:
        """Spend ``cost`` from this pool. Raises if it cannot be paid."""
        spent = self._pay(cost, commit=True)
        if spent is None:
            raise ValueError(f"cannot pay {cost} from {dict(self)}")

    def _pay(self, cost: ManaCost, commit: bool) -> ManaPool | None:
        """Pay coloured pips first, then generic from whatever is left over.

        Colored pips have exactly one source each, so paying them first is not a
        heuristic — it is forced. Only the generic remainder involves a choice,
        and any mana pays it, so which units are spent cannot change whether the
        cost is payable.
        """
        pool = ManaPool(self)
        for sym, n in cost.pips:
            if pool[sym] < n:
                return None
            pool[sym] -= n
        if pool.total() < cost.generic:
            return None
        owed = cost.generic
        # Spend colorless before colored, and otherwise the most plentiful
        # color: this leaves the pool as flexible as possible for later costs
        # in the same priority window.
        for sym in sorted(MANA_SYMBOLS, key=lambda s: (s != COLORLESS, -pool[s])):
            if owed == 0:
                break
            take = min(owed, pool[sym])
            pool[sym] -= take
            owed -= take
        if owed:
            return None
        if commit:
            self.clear()
            self.update({k: v for k, v in pool.items() if v})
        return pool


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
    """What a spell or ability demands of each of its targets.

    ``selector`` names a predicate implemented in ``engine.game``; keeping the
    predicate out of the card data means card definitions stay declarative and
    comparable, and there is exactly one place where "what is a legal target"
    is decided.
    """

    selector: str
    description: str = ""

    def label(self) -> str:
        return self.description or self.selector.replace("_", " ")


def dedupe(items: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
