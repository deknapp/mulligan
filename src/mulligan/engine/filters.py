"""One small language for "which objects": targets, triggers, statics, counts.

A filter is a string: a base, then comma-separated predicates.

    creature                         any creature on the battlefield
    creature:yours                   a creature you control
    creature:theirs,kw=flying        a flier an opponent controls
    creature:yours,other,subtype=Dwarf
    permanent:nonland,theirs
    artifact|enchantment             either type (``|`` in the base is "or")
    creature:power>=4
    creature:attacking|blocking      ``|`` inside a predicate is "or" too
    creature:zone=graveyard,yours    a creature card in your graveyard
    card:type=artifact|creature      any card of either type, in any zone
    land:basic
    creature:!token                  ``!`` negates a predicate

Real card text is compiled into these, so card data never contains code, and
"what counts as a Goblin you control" is decided in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from .types import CardType, Keyword

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .card import GameObject
    from .game import Game

TYPE_NAMES = {t.value.lower(): t for t in CardType}
BASES = set(TYPE_NAMES) | {"permanent", "card", "nonland", "any"}


@dataclass(frozen=True)
class Predicate:
    negate: bool
    key: str
    value: str = ""


@dataclass(frozen=True)
class Filter:
    text: str
    bases: tuple[str, ...]
    predicates: tuple[Predicate, ...]
    zone: str = "battlefield"

    def matches(self, game: Game, obj: GameObject, controller: int,
                source_id: int | None = None) -> bool:
        if self.zone != "any" and obj.zone != self.zone:
            return False
        if not any(_base_matches(game, obj, base) for base in self.bases):
            return False
        for pred in self.predicates:
            if _predicate(game, obj, pred, controller, source_id) == pred.negate:
                return False
        return True


@lru_cache(maxsize=4096)
def parse(text: str) -> Filter:
    base_text, _, pred_text = text.partition(":")
    bases = tuple(b.strip().lower() for b in base_text.split("|") if b.strip())
    for base in bases:
        if base not in BASES:
            raise ValueError(f"unknown filter base {base!r} in {text!r}")
    predicates: list[Predicate] = []
    zone = "any" if "card" in bases else "battlefield"
    for raw in (p.strip() for p in pred_text.split(",") if p.strip()):
        negate = raw.startswith("!")
        raw = raw.lstrip("!")
        for op in (">=", "<=", "="):
            if op in raw:
                key, value = raw.split(op, 1)
                key = key.strip() + ("" if op == "=" else op)
                break
        else:
            key, value = raw, ""
        if key == "zone":
            zone = value
            continue
        if key not in PREDICATES:
            raise ValueError(f"unknown filter predicate {raw!r} in {text!r}")
        predicates.append(Predicate(negate, key, value))
    return Filter(text, bases, tuple(predicates), zone)


def _base_matches(game: Game, obj: GameObject, base: str) -> bool:
    spec = obj.spec
    if base in ("any", "card"):
        return True
    if base == "permanent":
        return spec.is_permanent
    if base == "nonland":
        return not spec.is_land
    if base == "creature":
        return game.is_creature(obj)
    return TYPE_NAMES[base] in game.types_of(obj)


def _predicate(game: Game, obj: GameObject, pred: Predicate, controller: int,
               source_id: int | None) -> bool:
    key, value = pred.key, pred.value
    if key == "yours":
        return _controller(obj) == controller
    if key == "theirs":
        return _controller(obj) != controller
    if key == "other":
        return obj.id != source_id
    if key == "token":
        return obj.is_token
    if key == "tapped":
        return obj.tapped
    if key == "legendary":
        return "Legendary" in obj.spec.supertypes
    if key == "basic":
        return "Basic" in obj.spec.supertypes
    if key in ("attacking", "blocking", "attacking|blocking"):
        return (key != "blocking" and obj.attacking) or (key != "attacking"
                                                          and obj.blocking is not None)
    if key == "subtype":
        subtypes = game.subtypes_of(obj)
        if value == "chosen":  # the type chosen by the source ("Choose a creature type")
            source = game.object_by_id(source_id)
            return bool(source is not None and source.chosen and source.chosen in subtypes)
        return any(v in subtypes for v in value.split("|"))
    if key == "type":
        types = game.types_of(obj)
        return any(TYPE_NAMES[v.lower()] in types for v in value.split("|"))
    if key == "kw":
        return Keyword(value) in game.keywords_of(obj)
    if key == "entered_this_turn":
        return obj.zone == "battlefield" and obj.entered_turn == game.state.turn
    if key == "color":
        return bool(obj.spec.color_set & set(value.split("|")))
    if key == "name":
        return obj.spec.name == value
    if key == "equipped":
        return any(o.attached_to == obj.id for o in game.state.objects.values()
                   if o.zone == "battlefield")
    if key == "counters":
        return obj.counters > 0
    if key in ("power>=", "power<="):
        power = game.power_of(obj)
        return power >= int(value) if key == "power>=" else power <= int(value)
    if key in ("toughness>=", "toughness<="):
        tough = game.toughness_of(obj)
        return tough >= int(value) if key == "toughness>=" else tough <= int(value)
    if key in ("mv>=", "mv<="):
        mv = obj.spec.cost.mana_value
        return mv >= int(value) if key == "mv>=" else mv <= int(value)
    raise ValueError(f"unhandled predicate {key!r}")  # pragma: no cover


def _controller(obj: GameObject) -> int:
    # Cards off the battlefield are "yours" if you own them.
    return obj.controller if obj.zone == "battlefield" else obj.owner


PREDICATES = {"yours", "theirs", "other", "token", "tapped", "legendary", "basic",
              "attacking", "blocking", "attacking|blocking", "subtype", "type", "kw",
              "name", "equipped", "counters", "power>=", "power<=", "mv>=", "mv<=",
              "toughness>=", "toughness<=", "color", "entered_this_turn"}


def matches(game: Game, text: str, obj: GameObject, controller: int,
            source_id: int | None = None) -> bool:
    return parse(text).matches(game, obj, controller, source_id)
