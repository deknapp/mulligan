"""One-shot effects.

An effect is a small, declarative object with a ``resolve`` method. Cards are
built by listing effects, so a card definition stays data rather than code, and
every rules interaction lives in exactly one place instead of being reimplemented
per card.

Most effects say what they act on with ``to`` (objects) or ``who`` (players),
using the references in ``Context.objects`` / ``Context.players``:

    "target"              every target of the spell or ability
    "target0", "target1"  one target, by position
    "self"                the source (the permanent whose ability this is)
    "it"                  the object the triggering event was about
    "enchanted", "equipped"  what the source is attached to
    "all:<filter>"        every object matching a filter (see ``filters``)

    "you", "each_opponent", "target_player", "each_player"

Amounts are an int or a small expression (see ``Game.amount``), such as
``"count:creature:yours"`` or ``"power:self"``.

Some choices inside an effect — which card to discard, what to scry to the
bottom, which basic land to fetch — are made by the engine with a fixed,
documented policy (``Game.auto_*``) rather than asked of the agent. Asking
would multiply the decision count for little strategic content; the list of
automated choices is in docs/DESIGN.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import Keyword, Target

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .card import GameObject
    from .game import Game

Amount = int | str


@dataclass(frozen=True)
class Context:
    """Everything an effect needs to know about why it is resolving."""

    controller: int
    targets: tuple[Target, ...] = ()
    source_id: int | None = None
    source_name: str = ""
    event_id: int | None = None
    cast_from: str = "hand"
    kicked: bool = False
    event_amount: int = 0
    x: int = 0

    def target(self, index: int = 0) -> Target | None:
        return self.targets[index] if index < len(self.targets) else None

    def objects(self, game: Game, ref: str) -> list[GameObject]:
        """The game objects ``ref`` names, skipping any that are gone."""
        found: list[GameObject] = []
        if ref == "target":
            ids = [t.id for t in self.targets if t.kind == "object"]
        elif ref.startswith("target") and ref[6:].isdigit():
            t = self.target(int(ref[6:]))
            ids = [t.id] if t is not None and t.kind == "object" else []
        elif ref == "self":
            ids = [self.source_id] if self.source_id is not None else []
        elif ref == "it":
            ids = [self.event_id] if self.event_id is not None else []
        elif ref in ("enchanted", "equipped"):
            source = game.object_by_id(self.source_id)
            ids = [source.attached_to] if source is not None and source.attached_to else []
        elif ref.startswith("all:"):
            return game.find(ref[4:], self.controller, self.source_id)
        else:
            raise ValueError(f"unknown object reference {ref!r}")
        for obj_id in ids:
            obj = game.object_by_id(obj_id)
            if obj is not None:
                found.append(obj)
        return found

    def players(self, game: Game, ref: str) -> list[int]:
        if ref == "you":
            return [self.controller]
        if ref == "each_opponent":
            return game.opponents_of(self.controller)
        if ref == "each_player":
            return [0, 1]
        if ref in ("target_player", "target"):
            return [t.id for t in self.targets if t.kind == "player"]
        if ref.startswith("target") and ref[6:].isdigit():
            t = self.target(int(ref[6:]))
            return [t.id] if t is not None and t.kind == "player" else []
        if ref in ("controller_of_target", "controller_of_it"):
            objs = self.objects(game, "target0" if ref == "controller_of_target" else "it")
            return [o.controller for o in objs]
        raise ValueError(f"unknown player reference {ref!r}")


class Effect:
    """Base class. Subclasses implement ``resolve``."""

    def resolve(self, game: Game, ctx: Context) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - overridden where it matters
        return type(self).__name__


def _on_battlefield(objs):
    return [o for o in objs if o.zone == "battlefield"]


# ------------------------------------------------------------------- mana


@dataclass(frozen=True)
class AddMana(Effect):
    """Mana abilities. ``symbols`` has one entry per unit of mana produced; an
    entry may offer a choice (``"W/U"``) or be ``"*"`` for any color.
    ``amount`` (an amount expression) repeats the symbols that many times, for
    "add X mana"; ``only_for`` is a filter the spell or ability source being
    paid for must match ("spend this mana only to cast Elf spells")."""

    symbols: tuple[str, ...]
    amount: Amount = 1
    only_for: str = ""

    def resolve(self, game: Game, ctx: Context) -> None:
        pool = game.state.players[ctx.controller].pool
        for _ in range(max(0, game.amount(self.amount, ctx))):
            for symbol in self.symbols:
                pool.add(game.pick_mana_color(ctx.controller, symbol))

    def describe(self) -> str:
        body = "".join(f"{{{s}}}" for s in self.symbols)
        return f"add {body}" if self.amount == 1 else f"add {body} for each {self.amount}"


# ------------------------------------------------------------ damage & life


@dataclass(frozen=True)
class DealDamage(Effect):
    """Damage to objects and/or players. ``to`` is an object reference or a
    player reference; ``"target"`` covers both (for "any target")."""

    amount: Amount
    to: str = "target"
    source: str = "self"

    def resolve(self, game: Game, ctx: Context) -> None:
        amount = game.amount(self.amount, ctx)
        source_id = ctx.source_id
        if self.source != "self":
            objs = ctx.objects(game, self.source)
            source_id = objs[0].id if objs else None
        if self.to in ("target", "each_opponent", "each_player", "you", "target_player") or (
                self.to.startswith("target") and self.to[6:].isdigit()):
            for seat in ctx.players(game, self.to):
                game.deal_damage(Target("player", seat), amount, source_id=source_id)
            if self.to in ("each_opponent", "each_player", "you", "target_player"):
                return
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            game.deal_damage(Target("object", obj.id), amount, source_id=source_id)

    def describe(self) -> str:
        return f"deal {self.amount} damage to {self.to.replace('all:', 'each ')}"


@dataclass(frozen=True)
class GainLife(Effect):
    amount: Amount
    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            game.gain_life(seat, game.amount(self.amount, ctx))

    def describe(self) -> str:
        return f"{self.who} gain {self.amount} life"


@dataclass(frozen=True)
class LoseLife(Effect):
    amount: Amount
    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            game.lose_life(seat, game.amount(self.amount, ctx))

    def describe(self) -> str:
        return f"{self.who} lose {self.amount} life"


# ------------------------------------------------------------------ cards


@dataclass(frozen=True)
class DrawCards(Effect):
    count: Amount
    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            for _ in range(game.amount(self.count, ctx)):
                game.draw_card(seat)

    def describe(self) -> str:
        return f"{self.who} draw {self.count} card(s)"


@dataclass(frozen=True)
class Discard(Effect):
    """The player discards; which card is the engine's automated choice."""

    count: Amount = 1
    who: str = "you"
    random: bool = False  # "discard a card at random"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            for _ in range(game.amount(self.count, ctx)):
                if self.random:
                    game.random_discard(seat)
                else:
                    game.auto_discard(seat)

    def describe(self) -> str:
        return f"{self.who} discard {self.count}"


@dataclass(frozen=True)
class Recruit(Effect):
    """HOB's recruit: draw, then discard; a nonland discard makes a 1/1 white
    Human Soldier."""

    def resolve(self, game: Game, ctx: Context) -> None:
        game.draw_card(ctx.controller)
        discarded = game.auto_discard(ctx.controller, prefer_nonland=True)
        if discarded is not None and not discarded.spec.is_land:
            game.create_token(ctx.controller, SOLDIER)

    def describe(self) -> str:
        return "recruit"


@dataclass(frozen=True)
class Loot(Effect):
    """Draw ``draw``, then discard ``discard``."""

    draw: int = 1
    discard: int = 1
    # "If you discard a land card this way, put it onto the battlefield tapped."
    land_to_battlefield: bool = False
    # "You may discard a card. If you do, draw two cards." (a rummage)
    discard_first: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        if self.discard_first:
            if game.auto_discard(ctx.controller) is not None:
                for _ in range(self.draw):
                    game.draw_card(ctx.controller)
            return
        for _ in range(self.draw):
            game.draw_card(ctx.controller)
        for _ in range(self.discard):
            card = game.auto_discard(ctx.controller, prefer_land=self.land_to_battlefield)
            if self.land_to_battlefield and card is not None and card.spec.is_land:
                game.put_onto_battlefield(card, ctx.controller, from_zone="graveyard",
                                          tapped=True)

    def describe(self) -> str:
        return f"draw {self.draw}, then discard {self.discard}"


@dataclass(frozen=True)
class Mill(Effect):
    count: Amount
    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            game.mill(seat, game.amount(self.count, ctx))

    def describe(self) -> str:
        return f"{self.who} mill {self.count}"


@dataclass(frozen=True)
class Scry(Effect):
    count: int

    def resolve(self, game: Game, ctx: Context) -> None:
        game.auto_scry(ctx.controller, self.count)

    def describe(self) -> str:
        return f"scry {self.count}"


@dataclass(frozen=True)
class Surveil(Effect):
    """Look at the top ``count``; each goes to the graveyard or back on top
    (an automated choice, like scry)."""

    count: int = 1

    def resolve(self, game: Game, ctx: Context) -> None:
        game.auto_surveil(ctx.controller, self.count)

    def describe(self) -> str:
        return f"surveil {self.count}"


@dataclass(frozen=True)
class SearchLibrary(Effect):
    """Search for a card matching ``filter`` and put it ``dest``: ``hand``,
    ``battlefield``, ``battlefield_tapped`` or ``top``."""

    filter: str = "land:basic"
    dest: str = "hand"
    count: Amount = 1

    def resolve(self, game: Game, ctx: Context) -> None:
        game.auto_search(ctx.controller, self.filter, self.dest, game.amount(self.count, ctx))

    def describe(self) -> str:
        return f"search for {self.filter} to {self.dest}"


@dataclass(frozen=True)
class LookAtTop(Effect):
    """Look at the top ``count``; put up to ``take`` matching cards into hand
    (the engine picks the best), the rest on the bottom (or into the graveyard
    when ``rest`` is ``graveyard``, i.e. a mill)."""

    count: int
    filter: str = "card"
    take: int = 1
    rest: str = "bottom"

    def resolve(self, game: Game, ctx: Context) -> None:
        game.auto_look(ctx.controller, self.count, self.filter, self.take, self.rest)

    def describe(self) -> str:
        return f"look at the top {self.count}, take {self.take} {self.filter}"


@dataclass(frozen=True)
class Impulse(Effect):
    """Exile the top ``count`` cards; they may be played until the end of your
    next turn."""

    count: int = 1

    def resolve(self, game: Game, ctx: Context) -> None:
        game.impulse(ctx.controller, self.count)

    def describe(self) -> str:
        return f"exile the top {self.count}; you may play it until your next end step"


@dataclass(frozen=True)
class AdditionalLand(Effect):
    def resolve(self, game: Game, ctx: Context) -> None:
        game.state.players[ctx.controller].extra_land_drops += 1

    def describe(self) -> str:
        return "you may play an additional land this turn"


# -------------------------------------------------------------- removal


@dataclass(frozen=True)
class Destroy(Effect):
    to: str = "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            game.destroy(obj.id)

    def describe(self) -> str:
        return f"destroy {self.to}"


@dataclass(frozen=True)
class Exile(Effect):
    """Exile. With ``until_source_leaves``, the card comes back when the source
    leaves the battlefield (the "Oblivion Ring" pattern)."""

    to: str = "target"
    until_source_leaves: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in ctx.objects(game, self.to):
            if obj.zone in ("battlefield", "graveyard", "hand"):
                game.exile(obj.id, linked_to=ctx.source_id if self.until_source_leaves else None)

    def describe(self) -> str:
        return f"exile {self.to}"


@dataclass(frozen=True)
class ReturnToHand(Effect):
    """Return to its owner's hand, from the battlefield or a graveyard."""

    to: str = "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in ctx.objects(game, self.to):
            if obj.zone in ("battlefield", "graveyard"):
                game.move_to_zone(obj.id, "hand")

    def describe(self) -> str:
        return f"return {self.to} to hand"


@dataclass(frozen=True)
class ReturnToBattlefield(Effect):
    """Put a card from a graveyard onto the battlefield under your control."""

    to: str = "target"
    tapped: bool = False
    attach_to: str = ""  # an Aura returning "attached to target creature"
    finality: bool = False  # "with a finality counter on it"

    def resolve(self, game: Game, ctx: Context) -> None:
        hosts = ctx.objects(game, self.attach_to) if self.attach_to else []
        for obj in ctx.objects(game, self.to):
            if obj.zone == "graveyard":
                host = hosts[0].id if hosts and hosts[0].zone == "battlefield" else None
                game.put_onto_battlefield(obj, ctx.controller, from_zone="graveyard",
                                          tapped=self.tapped, attach_to=host)
                obj.finality = self.finality

    def describe(self) -> str:
        return f"return {self.to} to the battlefield"


@dataclass(frozen=True)
class Flicker(Effect):
    """Exile, then return to the battlefield under the owner's control: a new
    object, so counters and Auras fall off and enter-the-battlefield
    abilities trigger again."""

    to: str = "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            game.move_to_zone(obj.id, "exile")
            if obj.zone == "exile":
                game.put_onto_battlefield(obj, obj.owner, from_zone="exile")

    def describe(self) -> str:
        return f"exile {self.to}, then return it to the battlefield"


@dataclass(frozen=True)
class PutOnLibrary(Effect):
    """Put on top or bottom of its owner's library (the owner's choice where
    the card says so: the engine picks top, the usual right answer)."""

    to: str = "target"
    position: str = "top"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            game.put_on_library(obj.id, self.position)

    def describe(self) -> str:
        return f"put {self.to} on the {self.position} of its owner's library"


@dataclass(frozen=True)
class ShuffleIntoLibrary(Effect):
    to: str = "self"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in ctx.objects(game, self.to):
            game.put_on_library(obj.id, "top")
            game.state.rng.shuffle(game.state.players[obj.owner].library)

    def describe(self) -> str:
        return f"shuffle {self.to} into its owner's library"


@dataclass(frozen=True)
class Sacrifice(Effect):
    """``who`` sacrifices ``count`` permanents matching ``filter``, choosing
    the least valuable (an automated choice)."""

    filter: str = "creature"
    who: str = "each_opponent"
    count: int = 1

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            for _ in range(self.count):
                game.auto_sacrifice(seat, self.filter)

    def describe(self) -> str:
        return f"{self.who} sacrifices a {self.filter}"


@dataclass(frozen=True)
class SacrificeSelf(Effect):
    def resolve(self, game: Game, ctx: Context) -> None:
        source = game.object_by_id(ctx.source_id)
        if source is not None and source.zone == "battlefield":
            game.sacrifice(source.id)

    def describe(self) -> str:
        return "sacrifice it"


@dataclass(frozen=True)
class CounterSpell(Effect):
    """Counter target spell, optionally "unless its controller pays N" (the
    engine pays automatically when it can)."""

    to: str = "target"
    unless_pay: int = 0

    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind != "object":
                continue
            item = next((s for s in game.state.stack if s.obj_id == target.id), None)
            if item is None:
                continue
            if self.unless_pay and game.try_pay_generic(item.controller, self.unless_pay):
                game.state.record(f"{item.name}'s controller pays {self.unless_pay}")
                continue
            game.counter_spell(target.id)

    def describe(self) -> str:
        tail = f" unless its controller pays {{{self.unless_pay}}}" if self.unless_pay else ""
        return "counter target spell" + tail


@dataclass(frozen=True)
class ReturnSpellToHand(Effect):
    def resolve(self, game: Game, ctx: Context) -> None:
        for target in ctx.targets:
            if target.kind == "object":
                game.counter_spell(target.id, to_zone="hand")

    def describe(self) -> str:
        return "return target spell to its owner's hand"


# ------------------------------------------------------- creature changes


@dataclass(frozen=True)
class Pump(Effect):
    """A stat change and/or keywords, until end of turn unless ``permanent``."""

    power: Amount = 0
    toughness: Amount = 0
    keywords: frozenset[Keyword] = field(default_factory=frozenset)
    to: str = "target"
    flags: frozenset[str] = field(default_factory=frozenset)
    permanent: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        power = game.amount(self.power, ctx)
        toughness = game.amount(self.toughness, ctx)
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            game.pump(obj.id, power, toughness, self.keywords, self.flags,
                      until_end_of_turn=not self.permanent)

    def describe(self) -> str:
        kw = "".join(f", gains {k.value}" for k in sorted(self.keywords, key=lambda k: k.value))
        fl = "".join(f", {f.replace('_', ' ')}" for f in sorted(self.flags))
        return f"{self.to} gets {self.power:+}/{self.toughness:+}{kw}{fl}" if isinstance(
            self.power, int) and isinstance(self.toughness, int) else f"{self.to} pumped{kw}{fl}"


@dataclass(frozen=True)
class Animate(Effect):
    """'becomes an artifact creature until end of turn' (a crewed Vehicle)."""

    to: str = "self"
    types: tuple[str, ...] = ("Artifact", "Creature")

    def resolve(self, game: Game, ctx: Context) -> None:
        from .types import CardType
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            obj.temp_types |= {CardType(t) for t in self.types}
            game._dirty()

    def describe(self) -> str:
        return f"{self.to} becomes {' '.join(self.types).lower()} until end of turn"


@dataclass(frozen=True)
class SetBasePT(Effect):
    power: int
    toughness: int
    to: str = "self"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            obj.base_override = (self.power, self.toughness)

    def describe(self) -> str:
        return f"{self.to} has base power and toughness {self.power}/{self.toughness}"


@dataclass(frozen=True)
class AddCounters(Effect):
    """+1/+1 counters."""

    count: Amount = 1
    to: str = "target"
    except_targets: bool = False  # "each other creature you control"

    def resolve(self, game: Game, ctx: Context) -> None:
        n = game.amount(self.count, ctx)
        skip = {t.id for t in ctx.targets if t.kind == "object"} if self.except_targets else set()
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            if obj.id not in skip:
                game.add_counters(obj.id, n)

    def describe(self) -> str:
        return f"put {self.count} +1/+1 counter(s) on {self.to}"


@dataclass(frozen=True)
class RemoveCounters(Effect):
    to: str = "target"
    count: int = 0  # 0: all of them

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            obj.counters = max(0, obj.counters - self.count) if self.count else 0

    def describe(self) -> str:
        return f"remove all counters from {self.to}"


@dataclass(frozen=True)
class Tap(Effect):
    to: str = "target"
    untap: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            obj.tapped = not self.untap

    def describe(self) -> str:
        return f"{'untap' if self.untap else 'tap'} {self.to}"


@dataclass(frozen=True)
class Fight(Effect):
    """``a`` and ``b`` deal damage equal to their power to each other. With
    ``one_sided`` only ``a`` deals damage (a "bite")."""

    a: str = "target0"
    b: str = "target1"
    one_sided: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        first = _on_battlefield(ctx.objects(game, self.a))
        second = _on_battlefield(ctx.objects(game, self.b))
        if not first or not second:
            return
        x, y = first[0], second[0]
        # Simultaneous: read both powers before either hit lands.
        x_power, y_power = game.power_of(x), game.power_of(y)
        game.deal_damage(Target("object", y.id), x_power, source_id=x.id)
        if not self.one_sided:
            game.deal_damage(Target("object", x.id), y_power, source_id=y.id)

    def describe(self) -> str:
        verb = "deals damage equal to its power to" if self.one_sided else "fights"
        return f"{self.a} {verb} {self.b}"


@dataclass(frozen=True)
class Attach(Effect):
    """Attach the source Equipment (or ``what``) to ``to``."""

    to: str = "target"
    what: str = "self"
    one: bool = False  # "attach an Equipment you control": just one (the strongest)

    def resolve(self, game: Game, ctx: Context) -> None:
        creatures = [o for o in _on_battlefield(ctx.objects(game, self.to))
                     if game.is_creature(o)]
        gear = _on_battlefield(ctx.objects(game, self.what))
        if self.one:
            gear = sorted(gear, key=lambda o: -o.spec.cost.mana_value)[:1]
        for equipment in gear:
            if creatures:
                game.attach(equipment.id, creatures[0].id)

    def describe(self) -> str:
        return f"attach {self.what} to {self.to}"


# ----------------------------------------------------------------- tokens


@dataclass(frozen=True)
class TokenSpec:
    name: str
    power: int = 0
    toughness: int = 0
    types: tuple[str, ...] = ("Creature",)
    subtypes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    keywords: frozenset[Keyword] = field(default_factory=frozenset)
    kind: str = ""  # "treasure", "food", "equipment", "jace" — tokens with rules text
    equip_cost: str = ""
    equip_power: int = 0
    equip_toughness: int = 0
    mana: tuple[str, ...] = ()   # "{T}: Add ..." (e.g. ("R/G",) for Heartwood)


SOLDIER = TokenSpec("Human Soldier", 1, 1, subtypes=("Human", "Soldier"), colors=("W",))
TREASURE = TokenSpec("Treasure", types=("Artifact",), subtypes=("Treasure",), kind="treasure")
ARMY = TokenSpec("Army", 0, 0, subtypes=("Army",), colors=("B",))
JACE = TokenSpec("Jace", types=("Planeswalker",), subtypes=("Jace",), colors=("U",), kind="jace")


@dataclass(frozen=True)
class CreateToken(Effect):
    token: TokenSpec
    count: Amount = 1
    tapped: bool = False
    attach_self: bool = False
    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            for _ in range(game.amount(self.count, ctx)):
                obj = game.create_token(seat, self.token, tapped=self.tapped)
                if self.attach_self and ctx.source_id is not None:
                    game.attach(ctx.source_id, obj.id)

    def describe(self) -> str:
        t = self.token
        body = f"{t.power}/{t.toughness} {t.name}" if "Creature" in t.types else t.name
        return f"create {self.count} {body} token(s)"


@dataclass(frozen=True)
class CopySelf(Effect):
    """Create ``count`` tokens that are copies of the source (unless the source
    is itself a token), optionally without the legendary supertype."""

    count: int = 1
    nonlegendary: bool = True

    def resolve(self, game: Game, ctx: Context) -> None:
        source = game.object_by_id(ctx.source_id)
        if source is None or source.is_token:
            return
        for _ in range(self.count):
            game.create_token_copy(ctx.controller, source.spec, self.nonlegendary)

    def describe(self) -> str:
        return f"create {self.count} token copies of it"


@dataclass(frozen=True)
class RevealUntil(Effect):
    """Reveal from the top until a card matching ``filter``; it goes onto the
    battlefield if its mana value is at most ``battlefield_if_mv_at_most`` (an
    amount), otherwise into your hand. The rest go to the bottom at random."""

    filter: str = "creature"
    battlefield_if_mv_at_most: Amount = -1

    def resolve(self, game: Game, ctx: Context) -> None:
        game.reveal_until(ctx.controller, self.filter,
                          game.amount(self.battlefield_if_mv_at_most, ctx))

    def describe(self) -> str:
        return f"reveal until a {self.filter}"


@dataclass(frozen=True)
class EmpowerJace(Effect):
    """Reality Fracture: put ``count`` loyalty counters on a Jace token you
    control, creating one first if you don't ("[-1]: Surveil 1",
    "[-3]: Draw a card")."""

    count: Amount = 1

    def resolve(self, game: Game, ctx: Context) -> None:
        game.empower_jace(ctx.controller, game.amount(self.count, ctx))

    def describe(self) -> str:
        return f"empower Jace {self.count}"


@dataclass(frozen=True)
class AddLoyalty(Effect):
    count: Amount = 1
    to: str = "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        n = game.amount(self.count, ctx)
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            game.add_loyalty(obj.id, n)

    def describe(self) -> str:
        return f"put {self.count} loyalty counter(s) on {self.to}"


@dataclass(frozen=True)
class SetPrepared(Effect):
    """'becomes prepared' (or, with ``value`` False, 'becomes unprepared')."""

    to: str = "self"
    value: bool = True

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            if obj.spec.prepare is not None or not self.value:
                obj.prepared = self.value

    def describe(self) -> str:
        return f"{self.to} becomes {'prepared' if self.value else 'unprepared'}"


@dataclass(frozen=True)
class Stun(Effect):
    """Stun counters: each one stops the permanent's next untap."""

    count: int = 1
    to: str = "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            obj.stun += self.count

    def describe(self) -> str:
        return f"put {self.count} stun counter(s) on {self.to}"


@dataclass(frozen=True)
class ChooseCreatureType(Effect):
    """"Choose a creature type" (an automated choice): ``policy`` ``yours``
    picks the type most common among your creatures in play, hand and library;
    ``keep`` picks the type that keeps the most of your creatures alive relative
    to your opponent's (for "destroy all creatures not of the chosen type")."""

    policy: str = "yours"

    def resolve(self, game: Game, ctx: Context) -> None:
        game.choose_creature_type(ctx.controller, ctx.source_id, self.policy)

    def describe(self) -> str:
        return "choose a creature type"


@dataclass(frozen=True)
class Amass(Effect):
    """Amass <subtype> N: counters on your Army, making a 0/0 one first if
    needed. ``attach_self`` attaches the source Equipment to the Army."""

    count: Amount = 1
    subtype: str = "Goblins"
    who: str = "you"
    attach_self: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            army = game.amass(seat, game.amount(self.count, ctx), self.subtype.rstrip("s"))
            if self.attach_self and army is not None and ctx.source_id is not None:
                game.attach(ctx.source_id, army.id)

    def describe(self) -> str:
        return f"amass {self.subtype} {self.count}"


# ------------------------------------------------------------- structure


@dataclass(frozen=True)
class Condition:
    """A yes/no question about the game, evaluated at resolution.

    ``kind``: ``control`` (you control at least ``n`` of ``filter``),
    ``opponent_controls``, ``graveyard`` (at least ``n`` cards in your
    graveyard), ``enduring_story``, ``cast_from_graveyard``, ``kicked``,
    ``drawn_this_turn`` (at least ``n``), ``creature_died_this_turn``,
    ``your_turn``, ``it_matches`` (the event object matches ``filter``),
    ``target_matches`` (target 0 matches ``filter``).
    """

    kind: str
    n: int = 1
    filter: str = ""
    negate: bool = False

    def holds(self, game: Game, ctx: Context) -> bool:
        return game.condition(self, ctx) != self.negate


@dataclass(frozen=True)
class If(Effect):
    condition: Condition
    then: tuple[Effect, ...] = ()
    otherwise: tuple[Effect, ...] = ()

    def resolve(self, game: Game, ctx: Context) -> None:
        for effect in (self.then if self.condition.holds(game, ctx) else self.otherwise):
            effect.resolve(game, ctx)

    def describe(self) -> str:
        body = ", ".join(e.describe() for e in self.then)
        other = ", ".join(e.describe() for e in self.otherwise)
        return f"if {self.condition.kind}: {body}" + (f"; otherwise {other}" if other else "")


@dataclass(frozen=True)
class MayPay(Effect):
    """"You may pay <cost>. If you do, ...": paid automatically whenever the
    controller can afford it (an automated choice)."""

    cost: str
    then: tuple[Effect, ...] = ()

    def resolve(self, game: Game, ctx: Context) -> None:
        if game.try_pay(ctx.controller, self.cost):
            for effect in self.then:
                effect.resolve(game, ctx)

    def describe(self) -> str:
        return f"you may pay {self.cost}: " + ", ".join(e.describe() for e in self.then)


@dataclass(frozen=True)
class CopyNextSpell(Effect):
    """'When you next cast an instant or sorcery spell this turn, copy that
    spell.' The copy keeps the original's targets (choosing new ones is an
    omitted refinement)."""

    filter: str = "card:type=instant|sorcery"

    def resolve(self, game: Game, ctx: Context) -> None:
        game.state.players[ctx.controller].copy_next = self.filter

    def describe(self) -> str:
        return "copy the next instant or sorcery you cast this turn"


@dataclass(frozen=True)
class CopyTriggeringSpell(Effect):
    """Copy the spell whose casting set this off (the event's source)."""

    def resolve(self, game: Game, ctx: Context) -> None:
        for item in reversed(game.state.stack):
            if item.kind == "spell" and item.source_id == ctx.event_id:
                game.copy_spell(item)
                return

    def describe(self) -> str:
        return "copy that spell"


@dataclass(frozen=True)
class Delayed(Effect):
    """Set up a delayed trigger: ``effects`` happen at ``when``
    (``next_upkeep`` or ``next_end_step``)."""

    when: str
    effects: tuple[Effect, ...] = ()

    def resolve(self, game: Game, ctx: Context) -> None:
        game.add_delayed(self.when, self.effects, ctx)

    def describe(self) -> str:
        return f"at the {self.when.replace('_', ' ')}, " + ", ".join(
            e.describe() for e in self.effects)


@dataclass(frozen=True)
class WinGame(Effect):
    """"You win the game." Ends the game immediately for ``who``.

    It outranks a loss already pending from an empty library, which is what
    Fblthp, Impossibly Lost is for: you draw from nothing and win anyway.
    """

    who: str = "you"

    def resolve(self, game: Game, ctx: Context) -> None:
        for seat in ctx.players(game, self.who):
            game.win_game(seat, f"{ctx.source_name} wins the game")

    def describe(self) -> str:
        return "you win the game"


@dataclass(frozen=True)
class TokenCopy(Effect):
    """Create a token that's a copy of each object ``of`` names.

    ``keywords`` are granted to the copies and ``sacrifice_at_end`` gives them
    the usual "sacrifice it at the beginning of the next end step" rider, which
    is how Face Yourself borrows a board for one swing.
    """

    of: str = "target"
    keywords: frozenset = frozenset()
    sacrifice_at_end: bool = False

    def resolve(self, game: Game, ctx: Context) -> None:
        for obj in _on_battlefield(ctx.objects(game, self.of)):
            game.token_copy_of(obj, ctx.controller, self.keywords, self.sacrifice_at_end)

    def describe(self) -> str:
        return f"create a token copy of {self.of}"


@dataclass(frozen=True)
class GrantAbility(Effect):
    """Give ``to`` an activated ability for the rest of the game.

    Unlike a static, the grant outlives its source leaving the battlefield,
    which is what "target land gains '{T}: Add {C}{C}'" needs when the card
    granting it is sitting in exile.
    """

    ability: dict = field(default_factory=dict)
    to: str = "target"

    def resolve(self, game: Game, ctx: Context) -> None:
        from ..cards.schema import ability as build_ability
        built = build_ability(dict(self.ability))
        for obj in _on_battlefield(ctx.objects(game, self.to)):
            if not any(a is built or a == built for a in obj.granted_abilities):
                obj.granted_abilities = obj.granted_abilities + (built,)

    def describe(self) -> str:
        return f"{self.to} gains an ability"


@dataclass(frozen=True)
class PlayableFromExile(Effect):
    """"You may cast this card for as long as it remains exiled."" """

    def resolve(self, game: Game, ctx: Context) -> None:
        source = game.object_by_id(ctx.source_id)
        if source is not None and source.zone == "exile":
            source.playable_until = 10 ** 6

    def describe(self) -> str:
        return "you may cast it from exile"
