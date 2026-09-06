"""One-shot effects.

An effect is a small, declarative object with a ``resolve`` method. Cards are
built by listing effects, so a card definition stays data rather than code, and
every rules interaction lives in exactly one place instead of being reimplemented
per card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import Keyword, Target

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .game import Game


@dataclass(frozen=True)
class Context:
    """Everything an effect needs to know about why it is resolving."""

    controller: int
    targets: tuple[Target, ...] = ()
    source_id: int | None = None
    source_name: str = ""

    def target(self, index: int = 0) -> Target | None:
        return self.targets[index] if index < len(self.targets) else None


class Effect:
    """Base class. Subclasses implement ``resolve``."""

    def resolve(self, game: Game, ctx: Context) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - overridden where it matters
        return type(self).__name__


@dataclass(frozen=True)
class AddMana(Effect):
    """Mana abilities. ``symbols`` is one entry per unit of mana produced."""

    symbols: tuple[str, ...]

    def resolve(self, game: Game, ctx: Context) -> None:
        for symbol in self.symbols:
            game.state.players[ctx.controller].pool.add(symbol)

    def describe(self) -> str:
        return "add " + "".join(f"{{{s}}}" for s in self.symbols)


@dataclass(frozen=True)
class DealDamage(Effect):
    """Damage to the effect's targets, or to a whole class of objects.

    ``scope`` is ``"targets"``, ``"each_opponent"``, ``"all_creatures"``, or
    ``"creatures_opponents_control"``.
    """

    amount: int
    scope: str = "targets"

    def resolve(self, game: Game, ctx: Context) -> None:
        if self.scope == "targets":
            for target in ctx.targets:
                game.deal_damage(target, self.amount, source_id=ctx.source_id)
            return
        if self.scope == "each_opponent":
            for seat in game.opponents_of(ctx.controller):
                game.deal_damage(Target("player", seat), self.amount, source_id=ctx.source_id)
            return
        for perm in list(game.all_creatures()):
            if self.scope == "creatures_opponents_control" and perm.controller == ctx.controller:
                continue
            game.deal_damage(Target("object", perm.id), self.amount, source_id=ctx.source_id)

    def describe(self) -> str:
        where = {"targets": "target", "each_opponent": "each opponent"}.get(self.scope, self.scope)
        return f"deal {self.amount} damage to {where}"


@dataclass(frozen=True)
class GainLife(Effect):
    amount: int
    who: str = "you"  # "you" | "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        if self.who == "you":
            game.gain_life(ctx.controller, self.amount)
        else:
            for target in ctx.targets:
                if target.kind == "player":
                    game.gain_life(target.id, self.amount)

    def describe(self) -> str:
        return f"gain {self.amount} life"


@dataclass(frozen=True)
class LoseLife(Effect):
    amount: int
    who: str = "you"  # "you" | "target" | "each_opponent"

    def resolve(self, game: Game, ctx: Context) -> None:
        if self.who == "you":
            game.lose_life(ctx.controller, self.amount)
        elif self.who == "each_opponent":
            for seat in game.opponents_of(ctx.controller):
                game.lose_life(seat, self.amount)
        else:
            for target in ctx.targets:
                if target.kind == "player":
                    game.lose_life(target.id, self.amount)

    def describe(self) -> str:
        subject = {"you": "you", "each_opponent": "each opponent"}.get(self.who, "target player")
        return f"{subject} lose{'' if self.who != 'you' else ''} {self.amount} life"


@dataclass(frozen=True)
class DrawCards(Effect):
    count: int
    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        seat = ctx.controller
        if self.who == "target":
            target = ctx.target()
            if target is None or target.kind != "player":
                return
            seat = target.id
        for _ in range(self.count):
            game.draw_card(seat)

    def describe(self) -> str:
        return f"draw {self.count} card" + ("s" if self.count != 1 else "")


@dataclass(frozen=True)
class DestroyTarget(Effect):
    """Destruction, which regeneration would replace — nothing in the current
    card pool regenerates, but the distinction from exile is kept because
    graveyard contents are observable."""

    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind == "object":
                game.destroy(target.id)

    def describe(self) -> str:
        return "destroy target"


@dataclass(frozen=True)
class ExileTarget(Effect):
    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind == "object":
                game.move_to_zone(target.id, "exile")

    def describe(self) -> str:
        return "exile target"


@dataclass(frozen=True)
class DestroyAll(Effect):
    """Board wipe. ``only_opponents`` is False for symmetric sweepers."""

    only_opponents: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        for perm in list(game.all_creatures()):
            if self.only_opponents and perm.controller == ctx.controller:
                continue
            game.destroy(perm.id)

    def describe(self) -> str:
        return "destroy all creatures" + (" your opponents control" if self.only_opponents else "")


@dataclass(frozen=True)
class ReturnTargetToHand(Effect):
    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind == "object":
                game.move_to_zone(target.id, "hand")

    def describe(self) -> str:
        return "return target to its owner's hand"


@dataclass(frozen=True)
class Pump(Effect):
    """A temporary or permanent stat change, optionally granting keywords."""

    power: int = 0
    toughness: int = 0
    keywords: frozenset[Keyword] = field(default_factory=frozenset)
    until_end_of_turn: bool = True
    self_target: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        ids = [ctx.source_id] if self.self_target else [t.id for t in ctx.targets if t.kind == "object"]
        for obj_id in ids:
            if obj_id is not None:
                game.pump(obj_id, self.power, self.toughness, self.keywords, self.until_end_of_turn)

    def describe(self) -> str:
        stat = f"{self.power:+d}/{self.toughness:+d}"
        kw = "".join(f" and gains {k.value}" for k in sorted(self.keywords, key=lambda k: k.value))
        who = "it" if self.self_target else "target creature"
        return f"{who} gets {stat}{kw}" + (" until end of turn" if self.until_end_of_turn else "")


@dataclass(frozen=True)
class CounterTargetSpell(Effect):
    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind == "object":
                game.counter_spell(target.id)

    def describe(self) -> str:
        return "counter target spell"


@dataclass(frozen=True)
class Fight(Effect):
    """Two creatures deal damage equal to their power to each other.

    With two targets the first fights the second (``Prey Upon``); with one, the
    source creature fights it (a creature's own fight ability).
    """

    def resolve(self, game: Game, ctx: Context) -> None:
        if len(ctx.targets) >= 2:
            first, target = ctx.targets[0], ctx.targets[1]
            source = game.object_by_id(first.id) if first.kind == "object" else None
        else:
            target = ctx.target()
            source = game.object_by_id(ctx.source_id) if ctx.source_id is not None else None
        if target is None or target.kind != "object" or source is None:
            return
        other = game.object_by_id(target.id)
        if other is None or source.zone != "battlefield" or other.zone != "battlefield":
            return
        # Both hits are simultaneous: a creature that dies still deals its damage.
        source_power, other_power = game.power_of(source), game.power_of(other)
        game.deal_damage(Target("object", other.id), source_power, source_id=source.id)
        game.deal_damage(Target("object", source.id), other_power, source_id=other.id)

    def describe(self) -> str:
        return "it fights target creature"

    # A fight is two simultaneous damage events, not a sequence, which is why
    # both powers are read before either is applied.


@dataclass(frozen=True)
class CreateToken(Effect):
    name: str
    power: int
    toughness: int
    subtypes: tuple[str, ...] = ()
    keywords: frozenset[Keyword] = field(default_factory=frozenset)
    count: int = 1

    def resolve(self, game: Game, ctx: Context) -> None:
        for _ in range(self.count):
            game.create_token(ctx.controller, self)

    def describe(self) -> str:
        plural = "s" if self.count != 1 else ""
        return f"create {self.count} {self.power}/{self.toughness} {self.name} token{plural}"


@dataclass(frozen=True)
class TapTarget(Effect):
    untap: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind == "object":
                perm = game.object_by_id(target.id)
                if perm is not None and perm.zone == "battlefield":
                    perm.tapped = not self.untap

    def describe(self) -> str:
        return "untap target" if self.untap else "tap target"
