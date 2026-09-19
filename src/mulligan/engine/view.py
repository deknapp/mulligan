"""What one player is allowed to know.

An agent is handed a ``PlayerView``, never the ``Game``. The view exposes
everything that is public in a real game of Magic — the battlefield, graveyards,
exile, the stack, life totals, how many cards each player holds — plus the
viewer's own hand. It does not expose the opponent's hand or either library's
order.

This matters more for learning agents than for any other kind: an agent that is
rewarded for winning will find and exploit any hidden information it can reach,
and its win rate then measures the leak, not its play. The view is a read-only
proxy rather than a copy, so building one per decision costs almost nothing.

Python cannot make the underlying game truly private; ``_game`` is reachable by
anyone determined to cheat. The contract is that agents use only the public
methods here, and ``tests/test_view.py`` checks that nothing hidden is reachable
through them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import actions as act
from .card import CardSpec, GameObject
from .types import CardType, Keyword, ManaCost, Step

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .game import Game


class CardView:
    """A card the viewer is allowed to see but which is not on the battlefield
    (own hand, any graveyard, exile, the stack)."""

    __slots__ = ("id", "spec", "owner", "zone")

    def __init__(self, obj: GameObject):
        self.id = obj.id
        self.spec: CardSpec = obj.spec
        self.owner = obj.owner
        self.zone = obj.zone

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def cost(self) -> ManaCost:
        return self.spec.cost

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name}#{self.id} {self.zone}>"


class PermanentView:
    """A permanent on the battlefield, with its current characteristics."""

    __slots__ = ("_obj", "_game")

    def __init__(self, obj: GameObject, game: Game):
        self._obj = obj
        self._game = game

    id = property(lambda self: self._obj.id)
    spec = property(lambda self: self._obj.spec)
    name = property(lambda self: self._obj.spec.name)
    owner = property(lambda self: self._obj.owner)
    controller = property(lambda self: self._obj.controller)
    tapped = property(lambda self: self._obj.tapped)
    damage = property(lambda self: self._obj.damage)
    counters = property(lambda self: self._obj.counters)
    attacking = property(lambda self: self._obj.attacking)
    blocking = property(lambda self: self._obj.blocking)
    blocked_by = property(lambda self: list(self._obj.blocked_by))
    is_token = property(lambda self: self._obj.is_token)
    is_creature = property(lambda self: self._game.is_creature(self._obj))
    is_land = property(lambda self: self._obj.spec.is_land)
    attached_to = property(lambda self: self._obj.attached_to)

    @property
    def power(self) -> int:
        return self._game.power_of(self._obj)

    @property
    def toughness(self) -> int:
        return self._game.toughness_of(self._obj)

    @property
    def keywords(self) -> frozenset[Keyword]:
        return self._game.keywords_of(self._obj)

    def has(self, keyword: Keyword) -> bool:
        return keyword in self._game.keywords_of(self._obj)

    @property
    def summoning_sick(self) -> bool:
        """True if it cannot attack or use {T} abilities this turn."""
        return self._game.has_summoning_sickness(self._obj)

    @property
    def can_block(self) -> bool:
        return self.is_creature and not self._obj.tapped and self._game.may_block(self._obj)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        stats = f" {self.power}/{self.toughness}" if self.is_creature else ""
        return f"<{self.name}#{self.id}{stats}{' T' if self.tapped else ''}>"


class StackView:
    __slots__ = ("name", "controller", "kind", "spec", "targets", "obj_id", "source_id",
                 "effects", "modes")

    def __init__(self, item, game: Game):
        self.name = item.name
        self.controller = item.controller
        self.kind = item.kind
        self.obj_id = item.obj_id
        self.source_id = item.source_id
        source = game.object_by_id(item.obj_id if item.obj_id is not None else item.source_id)
        self.spec = source.spec if source is not None else None
        self.targets = tuple(item.targets)
        # What it will do. Always public: it is printed on a card everyone can see.
        self.effects = tuple(item.effects)
        self.modes = tuple(item.extra.get("modes") or ())


class PlayerView:
    """The game as seen from ``seat``."""

    __slots__ = ("_game", "seat")

    def __init__(self, game: Game, seat: int):
        self._game = game
        self.seat = seat

    # ------------------------------------------------------------- turn state

    @property
    def opponent(self) -> int:
        return 1 - self.seat

    @property
    def turn(self) -> int:
        return self._game.state.turn

    @property
    def step(self) -> Step:
        return self._game.state.step

    @property
    def active(self) -> int:
        return self._game.state.active

    @property
    def is_my_turn(self) -> bool:
        return self._game.state.active == self.seat

    @property
    def pending(self) -> str:
        """What kind of decision is being asked for (``priority``, ``attackers``,
        ``blockers``, ``mulligan``, ...)."""
        return self._game.state.pending

    @property
    def on_the_play(self) -> bool:
        return self._game.state.on_the_play == self.seat

    # ------------------------------------------------------------ public info

    def life(self, seat: int) -> int:
        return self._game.state.players[seat].life

    def hand_size(self, seat: int) -> int:
        return len(self._game.state.players[seat].hand)

    def library_size(self, seat: int) -> int:
        return len(self._game.state.players[seat].library)

    def mulligans(self, seat: int) -> int:
        return self._game.state.mulligan_counts[seat]

    def lands_played_this_turn(self) -> int:
        return self._game.state.players[self.seat].lands_played

    def battlefield(self, seat: int) -> list[PermanentView]:
        return [PermanentView(o, self._game)
                for o in self._game.state.zone_objects(seat, "battlefield")]

    def creatures(self, seat: int) -> list[PermanentView]:
        return [PermanentView(o, self._game) for o in self._game.creatures_of(seat)]

    def lands(self, seat: int) -> list[PermanentView]:
        return [PermanentView(o, self._game)
                for o in self._game.state.zone_objects(seat, "battlefield") if o.spec.is_land]

    def graveyard(self, seat: int) -> list[CardView]:
        return [CardView(o) for o in self._game.state.zone_objects(seat, "graveyard")]

    def exile(self, seat: int) -> list[CardView]:
        return [CardView(o) for o in self._game.state.zone_objects(seat, "exile")]

    def stack(self) -> list[StackView]:
        """Bottom first; the last item resolves next."""
        return [StackView(item, self._game) for item in self._game.state.stack]

    def pending_trigger(self) -> StackView | None:
        """The triggered ability whose targets are being chosen, if any."""
        trigger = self._game.state.pending_trigger
        return StackView(trigger, self._game) if trigger is not None else None

    def attackers(self) -> list[PermanentView]:
        state = self._game.state
        ids = state.attackers_declared
        return [PermanentView(state.obj(i), self._game) for i in ids
                if state.obj(i).zone == "battlefield"]

    def blocks(self) -> list[tuple[int, int]]:
        """(blocker id, attacker id) pairs declared so far this combat."""
        return list(self._game.state.blocks_declared)

    def permanent(self, obj_id: int) -> PermanentView | None:
        obj = self._game.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield":
            return None
        return PermanentView(obj, self._game)

    # ----------------------------------------------------------- own resources

    def hand(self) -> list[CardView]:
        return [CardView(o) for o in self._game.state.zone_objects(self.seat, "hand")]

    def card(self, obj_id: int) -> CardView | PermanentView | None:
        """Look up any object the viewer is allowed to see; None otherwise."""
        obj = self._game.object_by_id(obj_id)
        if obj is None:
            return None
        if obj.zone == "battlefield":
            return PermanentView(obj, self._game)
        if obj.zone == "hand" and obj.owner != self.seat:
            return None
        if obj.zone == "library":
            return None
        return CardView(obj)

    def could_block(self, blocker: PermanentView, attacker: PermanentView) -> bool:
        """Whether ``blocker`` may legally block ``attacker`` (flying, reach,
        menace aside, "can't be blocked", "can't block", ...). Rules are public."""
        return (not blocker.tapped
                and self._game.can_block_attacker(blocker._obj, attacker._obj))

    def mana_available(self, seat: int | None = None) -> int:
        """Untapped mana sources plus floating mana. Public for both players:
        which lands are untapped is visible across the table."""
        seat = self.seat if seat is None else seat
        return self._game.mana_available(seat)

    def can_pay(self, cost: ManaCost) -> bool:
        return self._game.can_pay(self.seat, cost)

    # ------------------------------------------------------------- utilities

    def describe(self, action: act.Action) -> str:
        return self._game.describe_action(action)

    def is_creature_spec(self, spec: CardSpec) -> bool:
        return CardType.CREATURE in spec.types
