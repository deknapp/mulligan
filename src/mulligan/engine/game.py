"""The rules engine.

The contract with an agent is one method pair:

    actions = game.legal_actions()   # never empty while the game is running
    game.apply(actions[i])           # only ever an action the engine offered

Everything else — priority, the stack, triggers, combat, state-based actions,
continuous effects, tapping lands for mana — is the engine's job. Agents
choose; they never assert.

Rules coverage is deliberately "what Limited needs": the full turn structure,
the stack, triggered/activated/static abilities, combat with the common
keywords, auras and equipment, tokens and counters, modal spells, adventures,
flashback, kicker, sagas, ward and hexproof, and the legend rule. Things it
does not model are listed in docs/DESIGN.md, and cards that need them are
excluded from a compiled set rather than approximated silently.
"""

from __future__ import annotations

import itertools
import random
from collections import Counter

from . import actions as act
from . import filters
from .card import ActivatedAbility, CardSpec, Cost, GameObject, Player, StackItem, Static
from .effects import (
    ARMY,
    TREASURE,
    AddMana,
    Condition,
    Context,
    Effect,
    TokenSpec,
)
from .state import GameState
from .types import (
    COLORLESS,
    MANA_SYMBOLS,
    NO_TARGET,
    TURN_SEQUENCE,
    CardType,
    Color,
    Keyword,
    ManaCost,
    Step,
    Target,
    TargetSpec,
    pip_options,
)

MAX_HAND_SIZE = 7
MAX_MULLIGANS = 6
TARGET_COMBINATION_CAP = 64
"""Multi-target spells enumerate every combination of legal targets. The cap
stops a pathological board from producing a five-figure action list."""

ALL_COLORS = frozenset(c.value for c in Color)

SELECTOR_ALIASES = {
    "creature_you_control": "creature:yours",
    "creature_you_dont_control": "creature:theirs",
    "nonland_permanent": "nonland",
    "tapped_creature": "creature:tapped",
    "creature_power_4_or_greater": "creature:power>=4",
    "attacking_or_blocking_creature": "creature:attacking|blocking",
}


class IllegalAction(Exception):
    """Raised when an agent submits an action the engine did not offer."""


def _unit_options(unit: str) -> frozenset[str]:
    if unit == "*":
        return ALL_COLORS
    return frozenset(unit.split("/"))


class Game:
    def __init__(
        self,
        decks: list[list[CardSpec]],
        names: tuple[str, str] = ("Player 0", "Player 1"),
        seed: int | None = None,
        starting_life: int = 20,
        max_turns: int = 60,
        on_the_play: int = 0,
    ):
        if len(decks) != 2:
            raise ValueError("mulligan plays two-player games")
        self.max_turns = max_turns
        self._next_id = itertools.count(1)
        rng = random.Random(seed)
        players = [Player(i, names[i], starting_life) for i in range(2)]
        self.state = GameState(players=players, rng=rng, on_the_play=on_the_play)
        self.state.active = on_the_play
        self.state.priority = on_the_play
        self.state.decision_player = on_the_play
        self.state.mulligan_counts = [0, 0]
        self.state.mulligan_decided = [False, False]
        self._trigger_queue: list[StackItem] = []
        self._delayed: list[tuple[str, int, tuple[Effect, ...], Context]] = []
        self._static_sources: list[GameObject] | None = None
        self._fired_once: set[tuple[int, int]] = set()
        self._legal_cache: list[act.Action] | None = None

        for seat, deck in enumerate(decks):
            for spec in deck:
                obj = self._new_object(spec, seat)
                players[seat].library.append(obj.id)
            rng.shuffle(players[seat].library)
            for _ in range(MAX_HAND_SIZE):
                self.draw_card(seat, is_setup=True)
        self.state.record(f"{players[on_the_play].name} is on the play")
        self.advance()

    # ------------------------------------------------------------------ objects

    def _new_object(self, spec: CardSpec, owner: int, is_token: bool = False) -> GameObject:
        obj = GameObject(next(self._next_id), spec, owner, is_token=is_token)
        self.state.objects[obj.id] = obj
        return obj

    def object_by_id(self, obj_id: int | None) -> GameObject | None:
        return self.state.objects.get(obj_id) if obj_id is not None else None

    def opponents_of(self, seat: int) -> list[int]:
        return [1 - seat]

    def battlefield(self) -> list[GameObject]:
        state = self.state
        return [state.objects[i] for p in state.players for i in p.battlefield]

    def all_creatures(self) -> list[GameObject]:
        return [o for o in self.battlefield() if self.is_creature(o)]

    def creatures_of(self, seat: int) -> list[GameObject]:
        return [o for o in self.state.zone_objects(seat, "battlefield") if self.is_creature(o)]

    def find(self, filter_text: str, controller: int,
             source_id: int | None = None) -> list[GameObject]:
        flt = filters.parse(filter_text)
        if flt.zone == "battlefield":
            pool = self.battlefield()
        else:
            pool = [o for o in self.state.objects.values() if o.zone != "nonexistent"]
        return [o for o in pool if flt.matches(self, o, controller, source_id)]

    def count(self, filter_text: str, controller: int, source_id: int | None = None) -> int:
        return len(self.find(filter_text, controller, source_id))

    # ------------------------------------------------- continuous effects

    def _statics(self) -> list[GameObject]:
        if self._static_sources is None:
            self._static_sources = [o for o in self.battlefield() if o.spec.statics]
        return self._static_sources

    def _dirty(self) -> None:
        self._static_sources = None

    def _lost_abilities(self, obj: GameObject) -> bool:
        for src in self._statics():
            if src.attached_to != obj.id and src.id != obj.id:
                continue
            for static in src.spec.statics:
                if "loses_abilities" in static.flags and self._static_applies(src, static, obj):
                    return True
        return False

    def _static_applies(self, src: GameObject, static: Static, obj: GameObject) -> bool:
        affects = static.affects
        if affects == "self":
            hit = src.id == obj.id
        elif affects in ("equipped", "enchanted"):
            hit = src.attached_to == obj.id
        elif affects.startswith("all:"):
            hit = filters.matches(self, affects[4:], obj, src.controller, src.id)
        else:
            hit = False
        if not hit:
            return False
        if static.condition is not None:
            ctx = Context(controller=src.controller, source_id=src.id)
            return static.condition.holds(self, ctx)
        return True

    def statics_on(self, obj: GameObject) -> list[tuple[GameObject, Static]]:
        found = []
        own_lost = None
        for src in self._statics():
            for static in src.spec.statics:
                if src.id == obj.id:
                    if own_lost is None:
                        own_lost = self._lost_abilities(obj)
                    if own_lost and "loses_abilities" not in static.flags:
                        continue
                if self._static_applies(src, static, obj):
                    found.append((src, static))
        return found

    def is_creature(self, obj: GameObject) -> bool:
        return CardType.CREATURE in obj.spec.types

    def types_of(self, obj: GameObject) -> frozenset[CardType]:
        return obj.spec.types

    def subtypes_of(self, obj: GameObject) -> tuple[str, ...]:
        return obj.spec.subtypes

    def keywords_of(self, obj: GameObject) -> frozenset[Keyword]:
        base = set(obj.granted_keywords)
        if not (obj.spec.statics or self._statics()):
            return frozenset(base | obj.spec.keywords)
        if not self._lost_abilities(obj):
            base |= obj.spec.keywords
        for _, static in self.statics_on(obj):
            base |= static.keywords
        return frozenset(base)

    def has_keyword(self, obj: GameObject, keyword: Keyword) -> bool:
        return keyword in self.keywords_of(obj)

    def flags_of(self, obj: GameObject) -> set[str]:
        flags = set(obj.temp_flags)
        for _, static in self.statics_on(obj):
            flags |= static.flags
        return flags

    def ward_of(self, obj: GameObject) -> int:
        ward = 0 if self._lost_abilities(obj) else obj.spec.ward
        for _, static in self.statics_on(obj):
            ward = max(ward, static.ward)
        return ward

    def _base_pt(self, obj: GameObject) -> tuple[int, int]:
        if obj.base_override is not None:
            return obj.base_override
        spec = obj.spec
        ctx = Context(controller=obj.controller, source_id=obj.id)
        power = self.amount(spec.power_expr, ctx) if spec.power_expr else (spec.power or 0)
        tough = (self.amount(spec.toughness_expr, ctx) if spec.toughness_expr
                 else (spec.toughness or 0))
        return power, tough

    def power_of(self, obj: GameObject) -> int:
        power, _ = self._base_pt(obj)
        power += obj.temp_power + obj.counters
        for src, static in self.statics_on(obj):
            power += self._static_amount(static.power, src)
        return max(0, power)

    def toughness_of(self, obj: GameObject) -> int:
        _, tough = self._base_pt(obj)
        tough += obj.temp_toughness + obj.counters
        for src, static in self.statics_on(obj):
            tough += self._static_amount(static.toughness, src)
        return tough

    def _static_amount(self, value, src: GameObject) -> int:
        if isinstance(value, int):
            return value
        return self.amount(value, Context(controller=src.controller, source_id=src.id))

    def has_summoning_sickness(self, obj: GameObject) -> bool:
        """Whether a creature is restricted from attacking or using {T} costs.

        Haste is checked here rather than cleared on entry, so it covers both
        restrictions at once and a creature that loses haste is restricted
        again, as the rules require.
        """
        return (self.is_creature(obj) and obj.summoning_sick
                and not self.has_keyword(obj, Keyword.HASTE))

    def may_block(self, obj: GameObject) -> bool:
        return "cant_block" not in self.flags_of(obj)

    def mana_available(self, seat: int) -> int:
        return (sum(len(units) for _, _, units, sac in self._mana_sources(seat) if not sac)
                + self.state.players[seat].pool.total())

    # -------------------------------------------------------- amounts & tests

    def amount(self, value, ctx: Context) -> int:
        """Evaluate an amount: an int, or ``count:<filter>``,
        ``count*N:<filter>``, ``power:<ref>``, ``toughness:<ref>``,
        ``graveyard``, ``hand``, ``event``, ``lands``."""
        if isinstance(value, int):
            return value
        if value in ("", None):
            return 0
        if value == "lands":
            return self.count("land:yours", ctx.controller)
        if value == "event":
            return ctx.event_amount
        if value == "graveyard":
            return len(self.state.players[ctx.controller].graveyard)
        if value == "hand":
            return len(self.state.players[ctx.controller].hand)
        head, _, rest = value.partition(":")
        if head.startswith("count"):
            factor = int(head[6:]) if head.startswith("count*") else 1
            return factor * self.count(rest, ctx.controller, ctx.source_id)
        if head == "mv":
            objs = ctx.objects(self, rest)
            return objs[0].spec.cost.mana_value if objs else 0
        if head in ("power", "toughness"):
            objs = ctx.objects(self, rest)
            if not objs:
                return 0
            return self.power_of(objs[0]) if head == "power" else self.toughness_of(objs[0])
        if head.lstrip("-").isdigit():
            return int(head)
        raise ValueError(f"unknown amount {value!r}")

    def condition(self, cond: Condition, ctx: Context) -> bool:
        seat = ctx.controller
        player = self.state.players[seat]
        kind = cond.kind
        if kind == "control":
            return self.count(cond.filter, seat, ctx.source_id) >= cond.n
        if kind == "opponent_controls":
            return self.count(cond.filter, 1 - seat, ctx.source_id) >= cond.n
        if kind == "graveyard":
            return len(player.graveyard) >= cond.n
        if kind == "enduring_story":
            return player.enduring_story
        if kind == "cast_from_graveyard":
            return ctx.cast_from == "graveyard"
        if kind == "kicked":
            return ctx.kicked
        if kind == "drawn_this_turn":
            return player.draws_this_turn >= cond.n
        if kind == "creature_died_this_turn":
            return self.state.creature_died_this_turn
        if kind == "your_turn":
            return self.state.active == seat
        if kind in ("it_matches", "target_matches"):
            objs = ctx.objects(self, "it" if kind == "it_matches" else "target0")
            return bool(objs) and filters.matches(self, cond.filter, objs[0], seat,
                                                  ctx.source_id)
        raise ValueError(f"unknown condition {kind!r}")

    # ------------------------------------------------------- mutations (effects)

    def draw_card(self, seat: int, is_setup: bool = False) -> None:
        player = self.state.players[seat]
        if not player.library:
            # Drawing from an empty library does not lose the game on the spot;
            # the loss is a state-based action checked at the next opportunity.
            player.lost = True
            player.loss_reason = "drew from an empty library"
            return
        obj_id = player.library.pop(0)
        player.hand.append(obj_id)
        self.state.objects[obj_id].zone = "hand"
        if is_setup:
            return
        self.state.record(f"{player.name} draws a card")
        player.draws_this_turn += 1
        self._fire("draw", controller=seat)
        if player.draws_this_turn == 2:
            self._fire("draw_second", controller=seat)
            self._fire("opp_draw_second", controller=1 - seat)

    def gain_life(self, seat: int, amount: int) -> None:
        if amount <= 0:
            return
        self.state.players[seat].life += amount
        self.state.record(f"{self.state.players[seat].name} gains {amount} life")

    def lose_life(self, seat: int, amount: int) -> None:
        if amount <= 0:
            return
        self.state.players[seat].life -= amount
        self.state.record(f"{self.state.players[seat].name} loses {amount} life")

    def deal_damage(self, target: Target, amount: int, source_id: int | None = None,
                    combat: bool = False) -> None:
        if amount <= 0:
            return
        source = self.object_by_id(source_id)
        if target.kind == "player":
            self.state.players[target.id].life -= amount
            self.state.record(
                f"{source.name if source else 'an effect'} deals {amount} to "
                f"{self.state.players[target.id].name}"
            )
            if combat and source is not None:
                self._fire("combat_damage_player", subject=source, amount=amount)
        elif target.kind == "object":
            obj = self.object_by_id(target.id)
            if obj is None or obj.zone != "battlefield":
                return
            obj.damage += amount
            if source is not None and self.has_keyword(source, Keyword.DEATHTOUCH):
                obj.deathtouched = True
            self.state.record(
                f"{source.name if source else 'an effect'} deals {amount} to {obj.name}"
            )
        else:
            return
        if source is not None and self.has_keyword(source, Keyword.LIFELINK):
            self.gain_life(source.controller, amount)

    def pump(self, obj_id: int, power: int, toughness: int, keywords=frozenset(),
             flags=frozenset(), until_end_of_turn: bool = True) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield":
            return
        if until_end_of_turn:
            obj.temp_power += power
            obj.temp_toughness += toughness
            obj.granted_keywords |= set(keywords)
            obj.temp_flags |= set(flags)
        else:
            obj.counters += power
        self.state.record(f"{obj.name} gets {power:+d}/{toughness:+d}")

    def add_counters(self, obj_id: int, n: int) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield" or n <= 0:
            return
        obj.counters += n
        self.state.record(f"{obj.name} gets {n} +1/+1 counter(s)")
        self._fire("counters_placed", subject=obj, controller=obj.controller)

    def destroy(self, obj_id: int) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield":
            return
        if self.has_keyword(obj, Keyword.INDESTRUCTIBLE):
            return
        self.state.record(f"{obj.name} is destroyed")
        self.move_to_zone(obj_id, "graveyard")

    def sacrifice(self, obj_id: int) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield":
            return
        self.state.record(f"{obj.name} is sacrificed")
        self.move_to_zone(obj_id, "graveyard")

    def exile(self, obj_id: int, linked_to: int | None = None) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None:
            return
        self.state.record(f"{obj.name} is exiled")
        self.move_to_zone(obj_id, "exile")
        obj.linked_to = linked_to

    def put_on_library(self, obj_id: int, position: str = "top") -> None:
        obj = self.object_by_id(obj_id)
        if obj is None:
            return
        self.move_to_zone(obj_id, "library")
        if obj.zone != "library":
            return  # a token ceased to exist
        library = self.state.players[obj.owner].library
        library.remove(obj_id)
        if position == "top":
            library.insert(0, obj_id)
        else:
            library.append(obj_id)

    def mill(self, seat: int, n: int) -> list[GameObject]:
        player = self.state.players[seat]
        milled = []
        for _ in range(min(n, len(player.library))):
            obj = self.state.objects[player.library[0]]
            self.move_to_zone(obj.id, "graveyard")
            milled.append(obj)
        if milled:
            self.state.record(f"{player.name} mills {len(milled)}")
        return milled

    def move_to_zone(self, obj_id: int, zone: str) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None:
            return
        state = self.state
        was_on_battlefield = obj.zone == "battlefield"
        owner = state.players[obj.owner]
        controller = state.players[obj.controller]
        for name in ("hand", "battlefield", "graveyard", "exile", "library"):
            for player in (owner, controller):
                if obj_id in player.zone(name):
                    player.zone(name).remove(obj_id)
        obj.clear_combat()
        obj.tapped = False
        obj.damage = 0
        obj.deathtouched = False
        obj.temp_power = obj.temp_toughness = 0
        obj.granted_keywords.clear()
        obj.temp_flags.clear()
        obj.base_override = None
        obj.counters = 0
        obj.lore = 0
        obj.on_adventure = False
        obj.playable_until = None
        obj.linked_to = None
        obj.activations = {}
        died_as_creature = was_on_battlefield and zone == "graveyard" and self.is_creature(obj)
        obj.controller = obj.owner
        if was_on_battlefield:
            self._dirty()
            obj.attached_to = None
            for other in self.battlefield():
                if other.attached_to == obj.id:
                    other.attached_to = None  # auras fall off in SBA; equipment stays
            for other in list(state.objects.values()):
                if other.linked_to == obj.id and other.zone == "exile":
                    other.linked_to = None
                    self.put_onto_battlefield(other, other.owner, from_zone="exile")
        if obj.is_token and zone != "battlefield":
            # A token that leaves the battlefield ceases to exist (CR 111.7).
            obj.zone = "nonexistent"
        else:
            obj.zone = zone
            owner.zone(zone).append(obj_id)
        if was_on_battlefield and zone == "graveyard":
            if died_as_creature:
                state.creature_died_this_turn = True
            self._fire("dies", subject=obj, controller=controller.seat)

    def counter_spell(self, obj_id: int, to_zone: str = "graveyard") -> None:
        for index, item in enumerate(self.state.stack):
            if item.obj_id == obj_id and item.kind == "spell":
                self.state.stack.pop(index)
                self.state.record(f"{item.name} is countered")
                self.move_to_zone(obj_id, to_zone)
                return

    def create_token(self, seat: int, token: TokenSpec, tapped: bool = False) -> GameObject:
        spec = _token_card(token)
        obj = self._new_object(spec, seat, is_token=True)
        self.put_onto_battlefield(obj, seat, tapped=tapped)
        return obj

    def amass(self, seat: int, n: int, subtype: str) -> GameObject | None:
        armies = [o for o in self.state.zone_objects(seat, "battlefield")
                  if "Army" in o.spec.subtypes]
        if armies:
            army = armies[0]
        else:
            army = self.create_token(seat, TokenSpec(f"{subtype} Army", 0, 0,
                                                     subtypes=(subtype, "Army"), colors=("B",)))
        self.add_counters(army.id, n)
        return army

    def attach(self, attachment_id: int, creature_id: int) -> None:
        attachment = self.object_by_id(attachment_id)
        creature = self.object_by_id(creature_id)
        if attachment is None or creature is None:
            return
        if attachment.zone != "battlefield" or creature.zone != "battlefield":
            return
        if not self.is_creature(creature):
            return
        attachment.attached_to = creature.id
        self._dirty()
        self.state.record(f"{attachment.name} is attached to {creature.name}")

    def put_onto_battlefield(self, obj: GameObject, seat: int, from_zone: str | None = None,
                             tapped: bool = False, attach_to: int | None = None) -> None:
        state = self.state
        if from_zone is not None:
            source = state.players[obj.owner].zone(from_zone)
            if obj.id in source:
                source.remove(obj.id)
        obj.zone = "battlefield"
        obj.controller = seat
        obj.entered_turn = state.turn
        obj.summoning_sick = True
        obj.tapped = tapped or obj.spec.enters_tapped
        obj.attached_to = None
        state.players[seat].battlefield.append(obj.id)
        self._dirty()
        state.record(f"{obj.name} enters the battlefield under {state.players[seat].name}")
        if attach_to is not None:
            self.attach(obj.id, attach_to)
        if obj.spec.chapters:
            self._add_lore(obj)
        self._fire("etb", subject=obj, controller=seat)
        self._fire("other_etb", subject=obj, controller=seat)
        if obj.spec.is_land:
            self._fire("landfall", subject=obj, controller=seat)

    # Kept for the scenario builder and tests written against the old name.
    def _put_onto_battlefield(self, obj: GameObject, seat: int) -> None:
        self.put_onto_battlefield(obj, seat)

    def try_pay_generic(self, seat: int, n: int) -> bool:
        cost = ManaCost(generic=n)
        if not self.can_pay(seat, cost):
            return False
        self._pay_mana(seat, cost)
        return True

    def add_delayed(self, when: str, effects: tuple[Effect, ...], ctx: Context) -> None:
        self._delayed.append((when, self.state.turn, effects, ctx))

    def impulse(self, seat: int, n: int) -> None:
        player = self.state.players[seat]
        until = self.state.turn + (2 if self.state.active == seat else 1)
        for _ in range(min(n, len(player.library))):
            obj = self.state.objects[player.library[0]]
            self.move_to_zone(obj.id, "exile")
            obj.playable_until = until
            self.state.record(f"{player.name} exiles {obj.name} (may play it)")

    # ------------------------------------------------------ automated choices

    def card_value(self, spec: CardSpec) -> float:
        """A rough, set-independent value used by the engine's automated
        choices. Agents never see or rely on it."""
        if spec.is_creature:
            return (spec.power or 0) * 1.5 + (spec.toughness or 0) + len(spec.keywords)
        if spec.is_land:
            return 1.0
        return 1.5 + spec.cost.mana_value * 0.7

    def _lands_total(self, seat: int) -> int:
        player = self.state.players[seat]
        in_hand = sum(1 for i in player.hand if self.state.objects[i].spec.is_land)
        return in_hand + sum(1 for i in player.battlefield if self.state.objects[i].spec.is_land)

    def auto_discard(self, seat: int, prefer_nonland: bool = False) -> GameObject | None:
        """Discard the card its owner least needs: an excess land when flooded,
        otherwise the least valuable spell."""
        player = self.state.players[seat]
        if not player.hand:
            return None
        hand = [self.state.objects[i] for i in player.hand]
        lands = [c for c in hand if c.spec.is_land]
        spells = [c for c in hand if not c.spec.is_land]
        flooded = self._lands_total(seat) >= 6 and lands
        if flooded and not (prefer_nonland and spells and self._lands_total(seat) < 7):
            choice = lands[0]
        elif spells and (prefer_nonland or not lands or self._lands_total(seat) >= 4):
            choice = min(spells, key=lambda c: self.card_value(c.spec))
        else:
            choice = lands[0] if lands and self._lands_total(seat) >= 5 else min(
                hand, key=lambda c: self.card_value(c.spec) + (5 if c.spec.is_land else 0))
        self.move_to_zone(choice.id, "graveyard")
        self.state.record(f"{player.name} discards {choice.name}")
        return choice

    def _want_on_top(self, seat: int, obj: GameObject) -> bool:
        lands = self._lands_total(seat)
        if obj.spec.is_land:
            return lands < 5
        return obj.spec.cost.mana_value <= lands + 2

    def auto_scry(self, seat: int, n: int) -> None:
        library = self.state.players[seat].library
        top = library[:n]
        keep = [i for i in top if self._want_on_top(seat, self.state.objects[i])]
        bottom = [i for i in top if i not in keep]
        library[:n] = keep
        library.extend(bottom)
        self.state.record(f"{self.state.players[seat].name} scries {n} "
                          f"({len(keep)} top, {len(bottom)} bottom)")

    def auto_search(self, seat: int, filter_text: str, dest: str, count: int = 1) -> None:
        player = self.state.players[seat]
        flt = filters.parse(filter_text)
        for _ in range(count):
            candidates = [self.state.objects[i] for i in player.library]
            candidates = [o for o in candidates if _card_matches(self, flt, o, seat)]
            if not candidates:
                break
            choice = max(candidates, key=lambda o: self._search_value(seat, o))
            if dest.startswith("battlefield"):
                self.put_onto_battlefield(choice, seat, from_zone="library",
                                          tapped=dest == "battlefield_tapped")
            elif dest == "top":
                player.library.remove(choice.id)
                self.state.rng.shuffle(player.library)
                player.library.insert(0, choice.id)
                continue
            else:
                player.library.remove(choice.id)
                player.hand.append(choice.id)
                choice.zone = "hand"
            self.state.record(f"{player.name} searches for {choice.name}")
        self.state.rng.shuffle(player.library)

    def _search_value(self, seat: int, obj: GameObject) -> float:
        if obj.spec.is_land:
            return self._color_need(seat, obj)
        return self.card_value(obj.spec)

    def _color_need(self, seat: int, land: GameObject) -> float:
        produced = set()
        for ab in land.spec.abilities:
            for e in ab.effects:
                if isinstance(e, AddMana):
                    for unit in e.symbols:
                        produced |= _unit_options(unit)
        have: Counter[str] = Counter()
        for obj in self.state.zone_objects(seat, "battlefield"):
            for ab in obj.spec.abilities:
                for e in ab.effects:
                    if isinstance(e, AddMana):
                        for unit in e.symbols:
                            have.update(_unit_options(unit))
        want: Counter[str] = Counter()
        for obj in self.state.zone_objects(seat, "hand") + self.state.zone_objects(seat, "library"):
            for sym, n in obj.spec.cost.pips:
                for part in pip_options(sym):
                    want[part] += n
        return sum(want[c] / (1 + have[c]) for c in produced) + 0.1 * len(produced)

    def auto_look(self, seat: int, count: int, filter_text: str, take: int, rest: str) -> None:
        player = self.state.players[seat]
        top = [self.state.objects[i] for i in player.library[:count]]
        flt = filters.parse(filter_text)
        eligible = [o for o in top if _card_matches(self, flt, o, seat)]
        chosen = sorted(eligible, key=lambda o: self._search_value(seat, o), reverse=True)[:take]
        for obj in top:
            player.library.remove(obj.id)
        for obj in chosen:
            player.hand.append(obj.id)
            obj.zone = "hand"
        others = [o for o in top if o not in chosen]
        if rest == "graveyard":
            for obj in others:
                obj.zone = "graveyard"
                player.graveyard.append(obj.id)
        else:
            self.state.rng.shuffle(others)
            player.library.extend(o.id for o in others)
        self.state.record(f"{player.name} looks at {count}, takes {len(chosen)}")

    def auto_sacrifice(self, seat: int, filter_text: str,
                       exclude: int | None = None) -> GameObject | None:
        options = [o for o in self.find(filter_text, seat)
                   if o.controller == seat and o.id != exclude]
        if not options:
            return None
        choice = min(options, key=self._permanent_value)
        self.sacrifice(choice.id)
        return choice

    def _permanent_value(self, obj: GameObject) -> float:
        if self.is_creature(obj):
            return self.power_of(obj) * 1.5 + self.toughness_of(obj) + (
                -3 if obj.is_token else 0)
        if obj.spec.subtypes and "Treasure" in obj.spec.subtypes:
            return 0.5
        return 1.0 + obj.spec.cost.mana_value

    def pick_mana_color(self, seat: int, unit: str) -> str:
        options = _unit_options(unit)
        if len(options) == 1:
            return next(iter(options))
        want: Counter[str] = Counter()
        for obj in self.state.zone_objects(seat, "hand"):
            for sym, n in obj.spec.cost.pips:
                for part in pip_options(sym):
                    want[part] += n
        return max(sorted(options), key=lambda c: want[c])

    # ------------------------------------------------------------------ mana

    def _mana_sources(self, seat: int):
        """Untapped permanents that can be tapped for mana right now:
        ``(object, ability index, units, sacrifices)``."""
        found = []
        for obj in self.state.zone_objects(seat, "battlefield"):
            if obj.tapped or not obj.spec.abilities:
                continue
            if self.has_summoning_sickness(obj):
                continue  # a {T} cost needs the creature to have been around
            for index, ability in enumerate(obj.spec.abilities):
                if not (ability.is_mana_ability and ability.tap_cost):
                    continue
                if ability.mana_cost.mana_value or ability.sacrifice or ability.life:
                    continue  # sources with their own costs are not auto-tapped
                units = tuple(u for e in ability.effects if isinstance(e, AddMana)
                              for u in e.symbols)
                if units:
                    found.append((obj, index, units, ability.sacrifice_self))
        return found

    def _solve_payment(self, seat: int, cost: ManaCost):
        """Find sources to tap (and floating mana to spend) that pay ``cost``.

        Pips are matched by search, fewest-options first; the generic remainder
        is then filled from whatever is left, where any mana is interchangeable.
        Lands are preferred over creatures (tapping a creature also costs an
        attack), and sacrifice sources like Treasure come last. Returns
        ``(sources, floating spent)`` or None.
        """
        pool = self.state.players[seat].pool
        sources = self._mana_sources(seat)
        order = sorted(range(len(sources)),
                       key=lambda i: (sources[i][3], self.is_creature(sources[i][0]),
                                      len(_unit_options(sources[i][2][0]))))
        # Generic mana comes from the sources whose colors the rest of the hand
        # needs least, so paying for one spell does not strand the next.
        demand: Counter[str] = Counter()
        for obj_id in self.state.players[seat].hand:
            for symbol, n in self.state.objects[obj_id].spec.cost.pips:
                for part in pip_options(symbol):
                    demand[part] += n

        def generic_rank(i: int):
            colors = set().union(*(_unit_options(u) for u in sources[i][2]))
            return (sources[i][3], self.is_creature(sources[i][0]),
                    sum(demand[c] for c in colors) / max(1, len(colors)))
        generic_order = sorted(order, key=generic_rank)
        pips: list[frozenset[str]] = []
        for symbol, count in cost.pips:
            pips.extend([frozenset(pip_options(symbol))] * count)
        pips.sort(key=len)

        used: list[int] = []
        floating = Counter(pool)
        spent: Counter[str] = Counter()
        spare_units = 0

        def pay_pips(index: int) -> bool:
            nonlocal spare_units
            if index == len(pips):
                return True
            options = pips[index]
            for symbol in sorted(options):
                if floating[symbol] > 0:
                    floating[symbol] -= 1
                    spent[symbol] += 1
                    if pay_pips(index + 1):
                        return True
                    floating[symbol] += 1
                    spent[symbol] -= 1
            for i in order:
                if i in used:
                    continue
                units = sources[i][2]
                if not any(options & _unit_options(u) for u in units):
                    continue
                used.append(i)
                spare_units += len(units) - 1
                if pay_pips(index + 1):
                    return True
                spare_units -= len(units) - 1
                used.pop()
            return False

        if not pay_pips(0):
            return None
        owed = cost.generic
        take = min(owed, spare_units)
        owed -= take
        for symbol in sorted(MANA_SYMBOLS, key=lambda s: (s != COLORLESS, -floating[s])):
            if owed <= 0:
                break
            take = min(owed, floating[symbol])
            if take:
                spent[symbol] += take
                floating[symbol] -= take
                owed -= take
        for i in generic_order:
            if owed <= 0:
                break
            if i in used:
                continue
            used.append(i)
            owed -= len(sources[i][2])
        if owed > 0:
            return None
        return [(sources[i][0], sources[i][1], sources[i][3]) for i in used], spent

    def can_pay(self, seat: int, cost: ManaCost) -> bool:
        if cost.mana_value == 0:
            return True
        return self._solve_payment(seat, cost) is not None

    def _pay_mana(self, seat: int, cost: ManaCost) -> None:
        if cost.mana_value == 0:
            return
        plan = self._solve_payment(seat, cost)
        if plan is None:
            raise IllegalAction(f"cannot pay {cost}")
        sources, spent = plan
        for obj, _index, sacrifices in sources:
            obj.tapped = True
            if sacrifices:
                self.sacrifice(obj.id)
        pool = self.state.players[seat].pool
        for symbol, n in spent.items():
            pool[symbol] -= n
            if pool[symbol] <= 0:
                del pool[symbol]

    def _can_pay_extra(self, seat: int, cost: Cost, source: GameObject | None) -> bool:
        player = self.state.players[seat]
        if cost.life and player.life < cost.life:
            return False
        if cost.sacrifice:
            exclude = source.id if source is not None else None
            if not [o for o in self.find(cost.sacrifice, seat, exclude)
                    if o.controller == seat and o.id != exclude]:
                return False
        if cost.discard:
            in_hand = len(player.hand) - (1 if source is not None and source.zone == "hand"
                                          else 0)
            if in_hand < cost.discard:
                return False
        return True

    def _pay_extra(self, seat: int, cost: Cost, source: GameObject | None) -> None:
        if cost.life:
            self.lose_life(seat, cost.life)
        if cost.sacrifice:
            self.auto_sacrifice(seat, cost.sacrifice,
                                exclude=source.id if source is not None else None)
        for _ in range(cost.discard):
            self.auto_discard(seat)

    # -------------------------------------------------------------- targeting

    def legal_targets(self, spec: TargetSpec, controller: int,
                      source_id: int | None = None) -> list[Target]:
        selector = SELECTOR_ALIASES.get(spec.selector, spec.selector)
        found: list[Target] = []
        if selector in ("any_target", "player", "opponent", "any_player"):
            seats = [controller, 1 - controller] if selector != "opponent" else [1 - controller]
            found.extend(Target("player", s) for s in seats)
            if selector != "any_target":
                return found
            selector = "creature"
        if selector.startswith("spell"):
            _, _, flt = selector.partition(":")
            for item in self.state.stack:
                if item.kind != "spell" or item.obj_id is None:
                    continue
                if flt and not filters.matches(self, flt + ",zone=stack" if ":" in flt
                                               else flt + ":zone=stack",
                                               self.state.obj(item.obj_id), controller):
                    continue
                found.append(Target("object", item.obj_id))
            return found
        flt = filters.parse(selector)
        pool = self.battlefield() if flt.zone == "battlefield" else [
            o for o in self.state.objects.values() if o.zone == flt.zone]
        for obj in pool:
            if not flt.matches(self, obj, controller, source_id):
                continue
            if (obj.zone == "battlefield" and obj.controller != controller
                    and self.has_keyword(obj, Keyword.HEXPROOF)):
                continue
            found.append(Target("object", obj.id))
        return found

    def _target_combinations(self, specs: tuple[TargetSpec, ...], controller: int,
                             source_id: int | None = None) -> list[tuple[Target, ...]]:
        if not specs:
            return [()]
        options = []
        for spec in specs:
            legal = self.legal_targets(spec, controller, source_id)
            if spec.optional:
                legal = legal + [NO_TARGET]
            if not legal:
                return []
            options.append(legal)
        combos = []
        for combo in itertools.product(*options):
            real = [(t.kind, t.id) for t in combo if t.kind != "none"]
            if len(set(real)) != len(real):
                continue  # "target X and target Y" must be different objects
            combos.append(combo)
            if len(combos) >= TARGET_COMBINATION_CAP:
                break
        return combos

    def _still_legal(self, item: StackItem) -> tuple[Target, ...] | None:
        """The item's targets with any now-illegal ones blanked, or None if it
        had targets and every one of them is gone (it fizzles)."""
        specs = item.extra.get("target_specs") or ()
        if not item.targets:
            return ()
        result = []
        any_legal = False
        for spec, target in zip(specs, item.targets, strict=False):
            if target.kind == "none":
                result.append(target)
                continue
            if target in self.legal_targets(spec, item.controller, item.source_id):
                result.append(target)
                any_legal = True
            else:
                result.append(NO_TARGET)
        real = [t for t in item.targets if t.kind != "none"]
        if real and not any_legal:
            return None
        return tuple(result)

    def _ward_tax(self, targets: tuple[Target, ...], controller: int) -> int:
        """Ward is modelled as an additional cost: "counter unless its
        controller pays N" is paid whenever it can be, so the engine only
        offers the targeting when the tax is affordable."""
        tax = 0
        for t in targets:
            if t.kind != "object":
                continue
            obj = self.object_by_id(t.id)
            if obj is not None and obj.zone == "battlefield" and obj.controller != controller:
                tax += self.ward_of(obj)
        return tax

    # ---------------------------------------------------------- legal actions

    def legal_actions(self) -> list[act.Action]:
        if self.state.over:
            return []
        if self._legal_cache is not None:
            return list(self._legal_cache)
        pending = self.state.pending
        seat = self.state.decision_player
        if pending == "mulligan":
            options: list[act.Action] = [act.KeepHand()]
            if self.state.mulligan_counts[seat] < MAX_MULLIGANS:
                options.append(act.Mulligan())
            return options
        if pending == "bottom":
            return [act.PutOnBottom(cid) for cid in self.state.players[seat].hand]
        if pending == "discard":
            return [act.Discard(cid) for cid in self.state.players[seat].hand]
        if pending == "trigger_targets":
            trigger = self.state.pending_trigger
            assert trigger is not None
            specs = trigger.extra.get("target_specs") or ()
            return [act.ChooseTargets(combo) for combo in
                    self._target_combinations(specs, trigger.controller, trigger.source_id)]
        if pending == "attackers":
            return self._attacker_options(seat)
        if pending == "blockers":
            return self._blocker_options(seat)
        return self._priority_options(seat)

    def _sorcery_timing(self, seat: int) -> bool:
        return (not self.state.stack and self.state.active == seat
                and self.state.step in (Step.PRECOMBAT_MAIN, Step.POSTCOMBAT_MAIN))

    def _castable_faces(self, seat: int):
        """Every (object, face, spec to cast, base cost) the player could cast
        from any zone, before timing, targets and payment are checked."""
        state = self.state
        player = state.players[seat]
        for obj_id in player.hand:
            obj = state.objects[obj_id]
            if not obj.spec.is_land:
                yield obj, "", obj.spec, obj.spec.cost
            if obj.spec.adventure is not None:
                yield obj, "adventure", obj.spec.adventure, obj.spec.adventure.cost
        for obj_id in player.exile:
            obj = state.objects[obj_id]
            if obj.on_adventure or (obj.playable_until is not None
                                    and obj.playable_until >= state.turn):
                if not obj.spec.is_land:
                    yield obj, "", obj.spec, obj.spec.cost
        for obj_id in player.graveyard:
            obj = state.objects[obj_id]
            if obj.spec.flashback is not None:
                yield obj, "flashback", obj.spec, obj.spec.flashback

    def _priority_options(self, seat: int) -> list[act.Action]:
        options: list[act.Action] = [act.Pass()]
        state = self.state
        player = state.players[seat]
        sorcery_ok = self._sorcery_timing(seat)
        if sorcery_ok and player.lands_played < 1 + player.extra_land_drops:
            for obj in state.zone_objects(seat, "hand"):
                if obj.spec.is_land:
                    options.append(act.PlayLand(obj.id))
            for obj in state.zone_objects(seat, "exile"):
                if (obj.spec.is_land and obj.playable_until is not None
                        and obj.playable_until >= state.turn):
                    options.append(act.PlayLand(obj.id))
        for obj, face, spec, base_cost in self._castable_faces(seat):
            if not (sorcery_ok or spec.is_instant):
                continue
            options.extend(self._cast_variants(seat, obj, face, spec, base_cost))
        for obj, index, ability in self._activatable(seat):
            if ability.sorcery_speed and not sorcery_ok:
                continue
            if not self._can_pay_extra(seat, ability.cost, obj):
                continue
            combos = self._target_combinations(ability.targets, seat, obj.id)
            for combo in combos:
                if ability.is_equip and any(t.kind == "object" and t.id == obj.attached_to
                                            for t in combo):
                    continue
                cost = ability.mana_cost
                tax = self._ward_tax(combo, seat)
                if tax:
                    cost = cost.plus(ManaCost(generic=tax))
                if not self.can_pay(seat, cost):
                    continue
                options.append(act.ActivateAbility(obj.id, index, combo))
        return options

    def _cast_variants(self, seat: int, obj: GameObject, face: str, spec: CardSpec,
                       base_cost: ManaCost) -> list[act.CastSpell]:
        found: list[act.CastSpell] = []
        if spec.enchant is not None:
            modes = [(-1, (spec.enchant,))]
        elif spec.modes:
            modes = [(i, m.targets) for i, m in enumerate(spec.modes)]
        else:
            modes = [(-1, spec.targets)]
        extras = list(enumerate(spec.additional_costs)) or [(-1, None)]
        kicks = [False, True] if spec.kicker is not None else [False]
        for mode, target_specs in modes:
            combos = self._target_combinations(target_specs, seat, obj.id)
            for combo in combos:
                for extra_index, extra in extras:
                    if extra is not None and not self._can_pay_extra(seat, extra, obj):
                        continue
                    for kicked in kicks:
                        cost = self._spell_cost(seat, spec, base_cost, combo, extra, kicked,
                                                obj)
                        if not self.can_pay(seat, cost):
                            continue
                        found.append(act.CastSpell(obj.id, combo, mode, face, kicked,
                                                   extra_index))
        return found

    def _spell_cost(self, seat: int, spec: CardSpec, base_cost: ManaCost,
                    targets: tuple[Target, ...], extra: Cost | None, kicked: bool,
                    obj: GameObject) -> ManaCost:
        cost = base_cost
        if extra is not None and extra.mana.mana_value:
            cost = cost.plus(extra.mana)
        if kicked and spec.kicker is not None:
            cost = cost.plus(spec.kicker)
        if spec.cost_reduction:
            applies = spec.cost_reduction_if is None or spec.cost_reduction_if.holds(
                self, Context(controller=seat, targets=targets, source_id=obj.id))
            if applies:
                cost = cost.reduced(spec.cost_reduction)
        tax = self._ward_tax(targets, seat)
        if tax:
            cost = cost.plus(ManaCost(generic=tax))
        return cost

    def _activatable(self, seat: int):
        state = self.state
        for zone in ("battlefield", "hand", "graveyard"):
            for obj in state.zone_objects(seat, zone):
                if zone == "battlefield" and obj.spec.abilities and self._lost_abilities(obj):
                    continue
                for index, ability in enumerate(obj.spec.abilities):
                    if ability.zone != zone or ability.is_mana_ability:
                        continue  # the engine taps for mana; see docs/DESIGN.md
                    if ability.tap_cost and (obj.tapped or self.has_summoning_sickness(obj)):
                        continue
                    if ability.once_per_turn and obj.activations.get(index):
                        continue
                    yield obj, index, ability

    def _attacker_options(self, seat: int) -> list[act.Action]:
        options: list[act.Action] = [act.FinishDeclaring()]
        for obj in self.creatures_of(seat):
            if obj.id in self.state.attackers_declared:
                continue
            if obj.tapped or self.has_summoning_sickness(obj):
                continue
            if self.has_keyword(obj, Keyword.DEFENDER) or "cant_attack" in self.flags_of(obj):
                continue
            options.append(act.DeclareAttacker(obj.id))
        return options

    def can_block_attacker(self, blocker: GameObject, attacker: GameObject) -> bool:
        if not self.may_block(blocker):
            return False
        flags = self.flags_of(attacker)
        if "unblockable" in flags:
            return False
        if self.has_keyword(attacker, Keyword.FLYING) and not (
                self.has_keyword(blocker, Keyword.FLYING)
                or self.has_keyword(blocker, Keyword.REACH)):
            return False
        for flag in flags:
            if flag.startswith("cant_be_blocked_by:") and filters.matches(
                    self, flag.split(":", 1)[1], blocker, attacker.controller, attacker.id):
                return False
        return True

    def _blocker_options(self, seat: int) -> list[act.Action]:
        """Menace needs care in an incremental declaration: a first blocker on
        a menace attacker is offered only if a second one is available, and a
        half-made menace block must be completed before anything else is
        declared. Otherwise a player could strand the declaration with no
        legal way to finish it."""
        assigned = {b for b, _ in self.state.blocks_declared}
        free = [b for b in self.creatures_of(seat) if not b.tapped and b.id not in assigned]
        counts = Counter(a for _, a in self.state.blocks_declared)
        half_done = [a for a in self.state.attackers_declared if counts[a] == 1
                     and self.has_keyword(self.state.obj(a), Keyword.MENACE)]
        options: list[act.Action] = []
        for blocker in free:
            for attacker_id in half_done or self.state.attackers_declared:
                attacker = self.state.obj(attacker_id)
                if attacker.zone != "battlefield":
                    continue
                if not self.can_block_attacker(blocker, attacker):
                    continue
                if (counts[attacker_id] == 0 and self.has_keyword(attacker, Keyword.MENACE)
                        and not any(other is not blocker
                                    and self.can_block_attacker(other, attacker)
                                    for other in free)):
                    continue
                options.append(act.DeclareBlocker(blocker.id, attacker_id))
        if self._blocks_are_legal():
            options.append(act.FinishDeclaring())
        return options

    def _blocks_are_legal(self) -> bool:
        """Menace is the only blocking restriction a partial declaration can
        violate, so ``FinishDeclaring`` is withheld until the declaration as a
        whole would be legal."""
        counts = Counter(a for _, a in self.state.blocks_declared)
        for attacker_id in self.state.attackers_declared:
            attacker = self.state.obj(attacker_id)
            if self.has_keyword(attacker, Keyword.MENACE) and counts[attacker_id] == 1:
                return False
        return True

    # ----------------------------------------------------------------- apply

    def apply(self, action: act.Action) -> None:
        if self.state.over:
            raise IllegalAction("the game is over")
        offered = self._legal_cache if self._legal_cache is not None else self.legal_actions()
        if action not in offered:
            raise IllegalAction(f"{action!r} is not legal right now")
        self._legal_cache = None
        self.state.decisions += 1
        self._perform(action)
        self.advance()

    def _perform(self, action: act.Action) -> None:
        self._legal_cache = None
        seat = self.state.decision_player
        state = self.state
        if isinstance(action, act.Mulligan):
            player = state.players[seat]
            for obj_id in list(player.hand) + list(player.library):
                state.objects[obj_id].zone = "library"
            player.library = list(player.hand) + list(player.library)
            player.hand = []
            state.rng.shuffle(player.library)
            state.mulligan_counts[seat] += 1
            for _ in range(MAX_HAND_SIZE):
                self.draw_card(seat, is_setup=True)
            state.record(f"{player.name} mulligans to "
                         f"{MAX_HAND_SIZE - state.mulligan_counts[seat]}")
            return
        if isinstance(action, act.KeepHand):
            state.mulligan_decided[seat] = True
            state.record(f"{state.players[seat].name} keeps")
            return
        if isinstance(action, act.PutOnBottom):
            player = state.players[seat]
            player.hand.remove(action.card_id)
            player.library.append(action.card_id)
            state.objects[action.card_id].zone = "library"
            return
        if isinstance(action, act.Discard):
            player = state.players[seat]
            self.move_to_zone(action.card_id, "graveyard")
            state.record(f"{player.name} discards {state.objects[action.card_id].name}")
            return
        if isinstance(action, act.ChooseTargets):
            trigger = state.pending_trigger
            assert trigger is not None
            trigger.targets = action.targets
            state.stack.append(trigger)
            state.pending_trigger = None
            state.pending = "priority"
            state.priority = state.active
            state.passes = 0
            state.record(f"{trigger.name} goes on the stack")
            return
        if isinstance(action, act.PlayLand):
            player = state.players[seat]
            obj = state.obj(action.card_id)
            player.lands_played += 1
            self.put_onto_battlefield(obj, seat, from_zone=obj.zone)
            return
        if isinstance(action, act.CastSpell):
            self._cast(seat, action)
            return
        if isinstance(action, act.ActivateAbility):
            self._activate(seat, action)
            return
        if isinstance(action, act.DeclareAttacker):
            state.attackers_declared.append(action.creature_id)
            return
        if isinstance(action, act.DeclareBlocker):
            state.blocks_declared.append((action.blocker_id, action.attacker_id))
            return
        if isinstance(action, act.FinishDeclaring):
            if state.pending == "attackers":
                self._finish_attackers()
            else:
                self._finish_blockers()
            return
        if isinstance(action, act.Pass):
            state.passes += 1
            state.priority = 1 - state.priority
            if state.passes >= 2:
                state.passes = 0
                if state.stack:
                    self._resolve_top()
                    state.priority = state.active
                else:
                    self._next_step()
            return
        raise IllegalAction(f"unhandled action {action!r}")

    def _cast(self, seat: int, action: act.CastSpell) -> None:
        obj = self.state.obj(action.card_id)
        face = action.face
        spec = obj.spec.adventure if face == "adventure" else obj.spec
        from_zone = obj.zone
        base_cost = obj.spec.flashback if face == "flashback" else spec.cost
        extra = spec.additional_costs[action.extra] if action.extra >= 0 else None
        cost = self._spell_cost(seat, spec, base_cost, action.targets, extra, action.kicked, obj)
        player = self.state.players[seat]
        # The card moves to the stack before costs are paid (CR 601.2a), so a
        # sacrifice or discard cost can never pick the spell itself.
        for zone in ("hand", "exile", "graveyard"):
            if obj.id in player.zone(zone):
                player.zone(zone).remove(obj.id)
        obj.zone = "stack"
        obj.cast_face = face
        obj.on_adventure = False
        obj.playable_until = None
        self._pay_mana(seat, cost)
        if extra is not None:
            self._pay_extra(seat, extra, obj)
        if action.mode >= 0:
            effects = spec.modes[action.mode].effects
            target_specs = spec.modes[action.mode].targets
        else:
            effects = spec.on_resolve
            target_specs = (spec.enchant,) if spec.enchant is not None else spec.targets
        item = StackItem(
            obj_id=obj.id, controller=seat, name=spec.name, kind="spell",
            effects=effects, targets=action.targets,
            is_permanent_spell=spec.is_permanent and face != "adventure", source_id=obj.id,
            extra={"target_specs": target_specs, "cast_from": from_zone, "face": face,
                   "kicked": action.kicked},
        )
        self.state.stack.append(item)
        target_note = ""
        if action.targets:
            target_note = " targeting " + ", ".join(self.describe_target(t)
                                                    for t in action.targets)
        self.state.record(f"{player.name} casts {spec.name}{target_note}")
        player.spells_cast_this_turn += 1
        self._fire("cast_spell", subject=obj, controller=seat)
        if spec.is_creature:
            self._fire("cast_creature", subject=obj, controller=seat)
        else:
            self._fire("cast_noncreature", subject=obj, controller=seat)
            self._fire("opp_cast_noncreature", subject=obj, controller=1 - seat)
        self.state.passes = 0
        self.state.priority = seat

    def _activate(self, seat: int, action: act.ActivateAbility) -> None:
        obj = self.state.obj(action.source_id)
        ability: ActivatedAbility = obj.spec.abilities[action.index]
        obj.activations[action.index] = obj.activations.get(action.index, 0) + 1
        if ability.tap_cost:
            obj.tapped = True
        cost = ability.mana_cost
        tax = self._ward_tax(action.targets, seat)
        if tax:
            cost = cost.plus(ManaCost(generic=tax))
        self._pay_mana(seat, cost)
        self._pay_extra(seat, ability.cost, obj)
        if ability.discard_self:
            self.move_to_zone(obj.id, "graveyard")
        if ability.sacrifice_self and obj.zone == "battlefield":
            self.sacrifice(obj.id)
        item = StackItem(
            obj_id=None, controller=seat, name=f"{obj.name}: {ability.describe()}",
            kind="activated", effects=ability.effects, targets=action.targets,
            source_id=obj.id, extra={"target_specs": ability.targets},
        )
        self.state.stack.append(item)
        self.state.record(f"{self.state.players[seat].name} activates {obj.name}")
        self.state.passes = 0
        self.state.priority = seat

    def _resolve_top(self) -> None:
        item = self.state.stack.pop()
        targets = self._still_legal(item)
        if targets is None:
            self.state.record(f"{item.name} is countered on resolution (no legal targets)")
            if item.obj_id is not None:
                self.move_to_zone(item.obj_id, "graveyard")
            return
        extra = item.extra
        if item.is_permanent_spell and item.obj_id is not None:
            obj = self.state.obj(item.obj_id)
            if obj.spec.enchant is not None:
                target = targets[0] if targets else NO_TARGET
                if target.kind != "object":
                    self.move_to_zone(obj.id, "graveyard")
                    return
                self.put_onto_battlefield(obj, item.controller, attach_to=target.id)
            else:
                self.put_onto_battlefield(obj, item.controller)
            return
        ctx = Context(controller=item.controller, targets=targets,
                      source_id=item.source_id, source_name=item.name,
                      event_id=extra.get("event_id"), cast_from=extra.get("cast_from", "hand"),
                      kicked=extra.get("kicked", False),
                      event_amount=extra.get("event_amount", 0))
        condition = extra.get("condition")
        if condition is not None and not condition.holds(self, ctx):
            self.state.record(f"{item.name} does nothing (its condition no longer holds)")
            return
        for effect in item.effects:
            effect.resolve(self, ctx)
        self.state.record(f"{item.name} resolves")
        if extra.get("chapter_of") is not None:
            self._saga_check(extra["chapter_of"])
        if item.kind == "spell" and item.obj_id is not None:
            obj = self.state.obj(item.obj_id)
            if obj.zone != "stack":
                return
            face = extra.get("face", "")
            if face == "adventure":
                self.move_to_zone(obj.id, "exile")
                obj.on_adventure = True
            elif face == "flashback":
                self.move_to_zone(obj.id, "exile")
            else:
                self.move_to_zone(obj.id, "graveyard")

    # ------------------------------------------------------------- triggers

    def _fire(self, event: str, subject: GameObject | None = None,
              controller: int | None = None, amount: int = 0) -> None:
        """Queue every triggered ability that ``event`` sets off."""
        watchers = [o for o in self.battlefield() if o.spec.triggers]
        if event == "dies" and subject is not None and subject not in watchers \
                and subject.spec.triggers:
            watchers.append(subject)  # leaves-the-battlefield: look back in time
        for obj in watchers:
            if obj.zone == "battlefield" and self._lost_abilities(obj):
                continue
            for index, trigger in enumerate(obj.spec.triggers):
                if not self._trigger_matches(trigger, event, obj, subject, controller):
                    continue
                if trigger.once_per_turn:
                    key = (obj.id, index)
                    if key in self._fired_once:
                        continue
                    self._fired_once.add(key)
                ctx = Context(controller=obj.controller, source_id=obj.id,
                              event_id=subject.id if subject is not None else None,
                              event_amount=amount)
                if trigger.condition is not None and not trigger.condition.holds(self, ctx):
                    continue
                self._trigger_queue.append(StackItem(
                    obj_id=None, controller=obj.controller,
                    name=f"{obj.name} ({trigger.when})", kind="triggered",
                    effects=trigger.effects, source_id=obj.id,
                    extra={"target_specs": trigger.targets,
                           "event_id": subject.id if subject is not None else None,
                           "event_amount": amount, "condition": trigger.condition},
                ))

    def _trigger_matches(self, trigger, event: str, obj: GameObject,
                         subject: GameObject | None, controller: int | None) -> bool:
        when = trigger.when
        mine = controller == obj.controller
        if when == "etb":
            return event == "etb" and subject is obj
        if when == "dies":
            return event == "dies" and subject is obj
        if when == "other_etb":
            return (event == "other_etb" and subject is not obj and mine
                    and (not trigger.filter or filters.matches(self, trigger.filter, subject,
                                                               obj.controller, obj.id)))
        if when == "landfall":
            return event == "landfall" and mine
        if when == "other_dies":
            return (event == "dies" and subject is not obj and mine
                    and self.is_creature(subject)
                    and (not trigger.filter or filters.matches(
                        self, trigger.filter + (",zone=graveyard" if ":" in trigger.filter
                                                else ":zone=graveyard"),
                        subject, obj.controller, obj.id)))
        if when == "any_dies":
            return event == "dies" and subject is not obj and self.is_creature(subject)
        if when == "attacks":
            return event == "attacks" and subject is obj
        if when == "combat_damage_player":
            return event == "combat_damage_player" and subject is obj
        if when == "counters_placed":
            return (event == "counters_placed" and mine and (
                not trigger.filter or filters.matches(self, trigger.filter, subject,
                                                      obj.controller, obj.id)))
        if when in ("you_attack", "upkeep", "begin_combat", "first_main", "end_step",
                    "cast_noncreature", "cast_creature", "cast_spell", "draw_second",
                    "opp_draw_second", "opp_cast_noncreature", "draw"):
            return event == when and mine
        return False

    def _add_lore(self, saga: GameObject) -> None:
        saga.lore += 1
        chapter = saga.spec.chapters[min(saga.lore, len(saga.spec.chapters)) - 1]
        self._trigger_queue.append(StackItem(
            obj_id=None, controller=saga.controller, name=f"{saga.name} (chapter {saga.lore})",
            kind="triggered", effects=chapter.effects, source_id=saga.id,
            extra={"target_specs": chapter.targets, "chapter_of": saga.id},
        ))

    def _saga_check(self, saga_id: int) -> None:
        saga = self.object_by_id(saga_id)
        if saga is not None and saga.zone == "battlefield" and saga.lore >= len(
                saga.spec.chapters):
            self.sacrifice(saga.id)

    # ------------------------------------------------------------ turn engine

    def _next_step(self) -> None:
        state = self.state
        for player in state.players:
            player.pool.clear()
        index = TURN_SEQUENCE.index(state.step)
        if index + 1 < len(TURN_SEQUENCE):
            state.step = TURN_SEQUENCE[index + 1]
        else:
            state.step = TURN_SEQUENCE[0]
            state.active = 1 - state.active
            state.turn += 1
        state.priority = state.active
        state.passes = 0
        self._begin_step()

    def _run_delayed(self, when: str) -> None:
        due = [d for d in self._delayed
               if d[0] == when and (when == "next_end_step" or d[1] < self.state.turn)]
        for entry in due:
            self._delayed.remove(entry)
            _, _, effects, ctx = entry
            self._trigger_queue.append(StackItem(
                obj_id=None, controller=ctx.controller, name="delayed trigger",
                kind="triggered", effects=effects, source_id=ctx.source_id,
            ))

    def _begin_step(self) -> None:
        state = self.state
        step = state.step
        seat = state.active
        if step is Step.UNTAP:
            if state.turn > self.max_turns:
                state.over = True
                state.result_reason = f"draw: turn limit of {self.max_turns} reached"
                return
            player = state.players[seat]
            player.lands_played = 0
            player.extra_land_drops = 0
            state.creature_died_this_turn = False
            self._fired_once.clear()
            for p in state.players:
                p.draws_this_turn = 0
                p.spells_cast_this_turn = 0
            for obj in self.state.zone_objects(seat, "battlefield"):
                obj.activations = {}
                if obj.tapped and "doesnt_untap" in self.flags_of(obj):
                    continue
                obj.tapped = False
                obj.summoning_sick = False
            state.record(f"--- {player.name}'s turn {state.turn} ---")
            self._next_step()
        elif step is Step.UPKEEP:
            self._fire("upkeep", controller=seat)
            self._run_delayed("next_upkeep")
        elif step is Step.DRAW:
            first_turn_on_the_play = state.turn == 1 and seat == state.on_the_play
            if not first_turn_on_the_play:
                self.draw_card(seat)
        elif step is Step.PRECOMBAT_MAIN:
            for obj in list(self.state.zone_objects(seat, "battlefield")):
                if obj.spec.chapters:
                    self._add_lore(obj)
            self._fire("first_main", controller=seat)
        elif step is Step.BEGIN_COMBAT:
            self._fire("begin_combat", controller=seat)
        elif step is Step.DECLARE_ATTACKERS:
            state.attackers_declared = []
            state.blocks_declared = []
            state.blockers_done = False
            state.pending = "attackers"
            state.decision_player = seat
        elif step is Step.COMBAT_DAMAGE:
            self._combat_damage()
        elif step is Step.END_COMBAT:
            for obj in self.state.objects.values():
                obj.clear_combat()
        elif step is Step.END_STEP:
            self._fire("end_step", controller=seat)
            self._run_delayed("next_end_step")
        elif step is Step.CLEANUP:
            for obj in self.state.objects.values():
                obj.reset_end_of_turn()
            if len(state.players[seat].hand) > MAX_HAND_SIZE:
                state.pending = "discard"
                state.decision_player = seat
            else:
                self._next_step()

    def _finish_attackers(self) -> None:
        state = self.state
        for attacker_id in state.attackers_declared:
            obj = state.obj(attacker_id)
            obj.attacking = True
            if not self.has_keyword(obj, Keyword.VIGILANCE):
                obj.tapped = True
        if state.attackers_declared:
            names = ", ".join(state.obj(i).name for i in state.attackers_declared)
            state.record(f"{state.players[state.active].name} attacks with {names}")
            self._fire("you_attack", controller=state.active)
            for attacker_id in state.attackers_declared:
                self._fire("attacks", subject=state.obj(attacker_id), controller=state.active)
        state.pending = "priority"
        state.priority = state.active
        state.decision_player = state.active
        state.passes = 0

    def _finish_blockers(self) -> None:
        state = self.state
        state.blockers_done = True
        for blocker_id, attacker_id in state.blocks_declared:
            blocker = state.obj(blocker_id)
            attacker = state.obj(attacker_id)
            blocker.blocking = attacker_id
            attacker.blocked_by.append(blocker_id)
            attacker.was_blocked = True
        if state.blocks_declared:
            pairs = ", ".join(f"{state.obj(b).name} blocks {state.obj(a).name}"
                              for b, a in state.blocks_declared)
            state.record(pairs)
        state.pending = "priority"
        state.priority = state.active
        state.decision_player = state.active
        state.passes = 0

    def _combat_damage(self) -> None:
        strikers = [o for o in self.all_creatures()
                    if (o.attacking or o.blocking is not None)
                    and (self.has_keyword(o, Keyword.FIRST_STRIKE)
                         or self.has_keyword(o, Keyword.DOUBLE_STRIKE))]
        if strikers:
            self._deal_combat_damage(first_strike=True)
            self._state_based_actions()
        self._deal_combat_damage(first_strike=False)

    def _deal_combat_damage(self, first_strike: bool) -> None:
        """Combat damage is dealt simultaneously: every assignment is computed
        against the pre-damage board, then applied."""
        assignments: list[tuple[Target, int, int]] = []
        defender = 1 - self.state.active

        def strikes_now(obj: GameObject) -> bool:
            has_fs = self.has_keyword(obj, Keyword.FIRST_STRIKE)
            has_ds = self.has_keyword(obj, Keyword.DOUBLE_STRIKE)
            return (has_fs or has_ds) if first_strike else (has_ds or not has_fs)

        for attacker in [o for o in self.all_creatures() if o.attacking]:
            if not strikes_now(attacker):
                continue
            power = self.power_of(attacker)
            blockers = [self.state.obj(i) for i in attacker.blocked_by
                        if self.state.obj(i).zone == "battlefield"]
            if not attacker.was_blocked:
                assignments.append((Target("player", defender), power, attacker.id))
                continue
            remaining = power
            deathtouch = self.has_keyword(attacker, Keyword.DEATHTOUCH)
            for blocker in blockers:
                if remaining <= 0:
                    break
                need = 1 if deathtouch else max(1, self.toughness_of(blocker) - blocker.damage)
                give = min(remaining, need)
                assignments.append((Target("object", blocker.id), give, attacker.id))
                remaining -= give
            if remaining > 0:
                if self.has_keyword(attacker, Keyword.TRAMPLE):
                    assignments.append((Target("player", defender), remaining, attacker.id))
                elif blockers:
                    assignments.append((Target("object", blockers[-1].id), remaining,
                                        attacker.id))

        for blocker in [o for o in self.all_creatures() if o.blocking is not None]:
            if not strikes_now(blocker):
                continue
            attacker = self.object_by_id(blocker.blocking)
            if attacker is None or attacker.zone != "battlefield":
                continue
            assignments.append((Target("object", attacker.id), self.power_of(blocker),
                                blocker.id))

        for target, amount, source_id in assignments:
            self.deal_damage(target, amount, source_id=source_id, combat=True)

    # ------------------------------------------------------ state-based actions

    def _state_based_actions(self) -> None:
        state = self.state
        changed = True
        while changed and not state.over:
            changed = False
            for obj in self.battlefield():
                if obj.zone != "battlefield":
                    continue
                if self.is_creature(obj):
                    toughness = self.toughness_of(obj)
                    indestructible = self.has_keyword(obj, Keyword.INDESTRUCTIBLE)
                    if toughness <= 0:
                        state.record(f"{obj.name} is put into the graveyard (0 toughness)")
                        self.move_to_zone(obj.id, "graveyard")
                        changed = True
                    elif obj.deathtouched and obj.damage > 0 and not indestructible:
                        state.record(f"{obj.name} is destroyed (deathtouch)")
                        self.move_to_zone(obj.id, "graveyard")
                        changed = True
                    elif obj.damage >= toughness and not indestructible:
                        state.record(f"{obj.name} is destroyed (lethal damage)")
                        self.move_to_zone(obj.id, "graveyard")
                        changed = True
                elif obj.spec.enchant is not None:
                    host = self.object_by_id(obj.attached_to)
                    if host is None or host.zone != "battlefield":
                        state.record(f"{obj.name} is put into the graveyard (unattached)")
                        self.move_to_zone(obj.id, "graveyard")
                        changed = True
            changed |= self._legend_rule()
            for seat, player in enumerate(state.players):
                if player.enduring_story:
                    continue
                mine = state.zone_objects(seat, "battlefield")
                if not any(o.spec.storied for o in mine):
                    continue
                story = sum(1 for o in mine if CardType.ARTIFACT in o.spec.types
                            or "Legendary" in o.spec.supertypes or o.spec.chapters)
                if story >= 3:
                    player.enduring_story = True
                    state.record(f"{player.name} has an enduring story")
            losers = [p for p in state.players if p.life <= 0 or p.lost]
            if losers:
                for player in losers:
                    if not player.lost:
                        player.lost = True
                        player.loss_reason = "life total reached 0"
                state.over = True
                if len(losers) == 2:
                    state.winner = None
                    state.result_reason = "draw: both players lost simultaneously"
                else:
                    state.winner = 1 - losers[0].seat
                    state.result_reason = (
                        f"{state.players[state.winner].name} wins: "
                        f"{losers[0].name} {losers[0].loss_reason}"
                    )
                state.record(state.result_reason)
                changed = True

    def _legend_rule(self) -> bool:
        changed = False
        for seat in (0, 1):
            seen: dict[str, GameObject] = {}
            for obj in self.state.zone_objects(seat, "battlefield"):
                if "Legendary" not in obj.spec.supertypes:
                    continue
                if obj.name in seen:
                    older = seen[obj.name]
                    self.state.record(f"{older.name}: legend rule")
                    self.move_to_zone(older.id, "graveyard")
                    changed = True
                seen[obj.name] = obj
        return changed

    # -------------------------------------------------------------- advancing

    def advance(self) -> None:
        """Run the game forward until a player faces a genuine choice.

        A player with exactly one legal action has no decision to make, so the
        engine takes it. This keeps agent transcripts free of thousands of
        forced passes, which matters enormously when each decision costs an LLM
        call.
        """
        self._legal_cache = None
        guard = 0
        while not self.state.over:
            guard += 1
            if guard > 200_000:  # pragma: no cover - defensive
                raise RuntimeError("engine failed to make progress")
            self._state_based_actions()
            if self.state.over:
                return
            if self._enter_pending_phase():
                continue
            options = self.legal_actions()
            if len(options) > 1:
                self._legal_cache = options
                return
            if not options:  # pragma: no cover - defensive
                raise RuntimeError(f"no legal actions in state {self.state.pending}")
            self._perform(options[0])

    def _enter_pending_phase(self) -> bool:
        """Set up whatever decision is outstanding. Returns True if the state
        changed and the caller should re-examine it."""
        state = self.state
        if state.pending in ("mulligan", "bottom"):
            return self._advance_mulligans()
        if state.pending in ("attackers", "blockers", "trigger_targets"):
            return False
        if self._trigger_queue:
            # Active player's triggers go on the stack first, so they resolve last.
            self._trigger_queue.sort(key=lambda t: t.controller != state.active)
            trigger = self._trigger_queue.pop(0)
            specs = trigger.extra.get("target_specs") or ()
            if specs:
                combos = self._target_combinations(specs, trigger.controller, trigger.source_id)
                if not combos:
                    state.record(f"{trigger.name} is removed (no legal targets)")
                    if trigger.extra.get("chapter_of") is not None:
                        self._saga_check(trigger.extra["chapter_of"])
                    return True
                state.pending_trigger = trigger
                state.pending = "trigger_targets"
                state.decision_player = trigger.controller
                return True
            state.stack.append(trigger)
            state.record(f"{trigger.name} goes on the stack")
            state.passes = 0
            state.priority = state.active
            return True
        if state.pending == "discard":
            if len(state.players[state.decision_player].hand) <= MAX_HAND_SIZE:
                state.pending = "priority"
                self._next_step()
                return True
            return False
        if (state.step is Step.DECLARE_BLOCKERS and state.pending == "priority"
                and not state.blockers_done and self._needs_block_decision()):
            state.pending = "blockers"
            state.decision_player = 1 - state.active
            return True
        state.pending = "priority"
        state.decision_player = state.priority
        return False

    def _needs_block_decision(self) -> bool:
        return bool(self.state.attackers_declared) and bool(
            [c for c in self.creatures_of(1 - self.state.active) if not c.tapped])

    def describe_action(self, action: act.Action) -> str:
        """A one-line human-readable rendering, for logs, replays and prompts."""
        def name(obj_id: int) -> str:
            return self.describe_target(Target("object", obj_id))

        def targets(ts) -> str:
            real = [t for t in ts if t.kind != "none"]
            return " -> " + ", ".join(self.describe_target(t) for t in real) if real else ""

        if isinstance(action, act.PlayLand):
            return f"play {name(action.card_id)}"
        if isinstance(action, act.CastSpell):
            obj = self.state.obj(action.card_id)
            spec = obj.spec.adventure if action.face == "adventure" else obj.spec
            label = spec.name
            if action.mode >= 0:
                label += f" (mode {action.mode + 1})"
            if action.face == "flashback":
                label += " (flashback)"
            if action.kicked:
                label += " (kicked)"
            return f"cast {label}{targets(action.targets)}"
        if isinstance(action, act.ActivateAbility):
            ability = self.state.obj(action.source_id).spec.abilities[action.index]
            return f"activate {name(action.source_id)}: {ability.describe()}" + targets(
                action.targets)
        if isinstance(action, act.DeclareAttacker):
            return f"attack with {name(action.creature_id)}"
        if isinstance(action, act.DeclareBlocker):
            return f"block {name(action.attacker_id)} with {name(action.blocker_id)}"
        if isinstance(action, act.ChooseTargets):
            return f"target{targets(action.targets)}"
        if isinstance(action, (act.Discard, act.PutOnBottom)):
            verb = "discard" if isinstance(action, act.Discard) else "bottom"
            return f"{verb} {name(action.card_id)}"
        return {act.Pass: "pass", act.FinishDeclaring: "done declaring",
                act.Mulligan: "mulligan", act.KeepHand: "keep"}.get(type(action), repr(action))

    def describe_target(self, target: Target) -> str:
        if target.kind == "player":
            return self.state.players[target.id].name
        if target.kind == "none":
            return "nothing"
        obj = self.object_by_id(target.id)
        return obj.name if obj else f"object {target.id}"

    # ------------------------------------------------------------------ result

    @property
    def is_over(self) -> bool:
        return self.state.over

    def result(self) -> dict:
        return {
            "winner": self.state.winner,
            "reason": self.state.result_reason,
            "turns": self.state.turn,
            "decisions": self.state.decisions,
            "life": [p.life for p in self.state.players],
        }

    # Mulligan bookkeeping lives at the bottom because it only runs before turn 1.
    def _advance_mulligans(self) -> bool:
        state = self.state
        order = [state.on_the_play, 1 - state.on_the_play]
        for seat in order:
            if not state.mulligan_decided[seat]:
                state.pending = "mulligan"
                state.decision_player = seat
                return False
        for seat in order:
            owed = state.mulligan_counts[seat]
            player = state.players[seat]
            if len(player.hand) > MAX_HAND_SIZE - owed:
                state.pending = "bottom"
                state.decision_player = seat
                return False
        state.pending = "priority"
        state.step = Step.UNTAP
        state.turn = 1
        state.active = state.on_the_play
        state.priority = state.active
        state.decision_player = state.active
        self._begin_step()
        return True


def _card_matches(game: Game, flt: filters.Filter, obj: GameObject, seat: int) -> bool:
    """Match a filter against a card in a hidden zone (library, hand) — the
    zone restriction of the filter does not apply there."""
    if not any(filters._base_matches(game, obj, base) for base in flt.bases):
        return False
    for pred in flt.predicates:
        if filters._predicate(game, obj, pred, seat, None) == pred.negate:
            return False
    return True


_TOKEN_CACHE: dict[TokenSpec, CardSpec] = {}


def _token_card(token: TokenSpec) -> CardSpec:
    """The card a token is made from. Treasure, Food and equipment tokens carry
    their rules text here."""
    cached = _TOKEN_CACHE.get(token)
    if cached is not None:
        return cached
    from .effects import Attach, GainLife  # local: effects imports nothing from here
    abilities: tuple[ActivatedAbility, ...] = ()
    statics: tuple[Static, ...] = ()
    if token.kind == "treasure":
        abilities = (ActivatedAbility(effects=(AddMana(("*",)),), tap_cost=True,
                                      sacrifice_self=True, is_mana_ability=True,
                                      text="{T}, Sacrifice this: Add one mana of any color."),)
    elif token.kind == "food":
        abilities = (ActivatedAbility(effects=(GainLife(3),), mana_cost=ManaCost(2),
                                      tap_cost=True, sacrifice_self=True,
                                      text="{2}, {T}, Sacrifice this: You gain 3 life."),)
    elif token.kind == "equipment":
        statics = (Static(affects="equipped", power=token.equip_power,
                          toughness=token.equip_toughness),)
        abilities = (ActivatedAbility(effects=(Attach(),), mana_cost=ManaCost.parse(
            token.equip_cost or "0"), targets=(TargetSpec("creature:yours"),),
            sorcery_speed=True, is_equip=True, text=f"Equip {{{token.equip_cost}}}"),)
    types = frozenset(CardType(t) for t in token.types)
    spec = CardSpec(name=token.name, types=types, subtypes=token.subtypes,
                    power=token.power if CardType.CREATURE in types else None,
                    toughness=token.toughness if CardType.CREATURE in types else None,
                    keywords=token.keywords, colors=frozenset(token.colors),
                    abilities=abilities, statics=statics)
    _TOKEN_CACHE[token] = spec
    return spec


__all__ = ["Game", "IllegalAction", "ARMY", "TREASURE"]
