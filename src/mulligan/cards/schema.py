"""Card data files: the JSON form of ``CardSpec``, and a strict loader.

A compiled set is a JSON file of cards written in this vocabulary. It is data,
never code: anyone can read a card's entry next to its oracle text and check
it, and the engine is the only place behaviour lives. The loader rejects
unknown keys and unknown effect names outright, so a typo in a compiled card is
an error at load time rather than a card that silently does nothing.

Shape of one card (every key but ``name`` is optional)::

    {
      "name": "Dori, Bearer of Friends",
      "cost": "{2}{R}",
      "types": ["Creature"], "supertypes": ["Legendary"], "subtypes": ["Dwarf", "Warrior"],
      "power": 3, "toughness": 2,          # or "power_expr": "count:creature:yours"
      "keywords": ["trample"], "ward": 2,
      "mana": ["W/U"],                     # shorthand: "{T}: Add {W} or {U}."
      "enters_tapped": true,
      "targets": [...], "effects": [...],  # an instant or sorcery
      "modes": [{"targets": [...], "effects": [...], "text": "..."}],
      "triggers": [{"when": "etb", "targets": [...], "effects": [...],
                    "filter": "...", "if": {...}, "once_per_turn": true,
                    "modes": [...],            # "choose one" triggers
                    "zone": "graveyard"}],     # triggers while in the graveyard
      "statics": [{"affects": "equipped", "power": 1, "toughness": 2, "keywords": [...],
                   "flags": [...], "ward": 1, "if": {...}}],
      "abilities": [{"cost": "{3}{W}", "tap": true, "sacrifice_self": true,
                     "sacrifice": "<filter>", "discard": 1, "discard_self": true,
                     "life": 2, "zone": "hand", "sorcery": true, "once_per_turn": true,
                     "targets": [...], "effects": [...]}],
      "equip": "{2}",
      "enchant": "creature",
      "flashback": "{3}{R}", "kicker": "{2}{W}{W}",
      "additional_costs": [{"sacrifice": "artifact|creature"}, {"cost": "{4}"}],
      "cost_reduction": 3, "cost_reduction_if": {...},
      "adventure": { ...a card, the Adventure half... },
      "chapters": [{"targets": [...], "effects": [...]}, ...],
      "storied": true,
      "approximations": ["what, if anything, the entry leaves out"],
      "unsupported": "why the engine cannot play this card"
    }

A target is a selector string (see ``engine.filters``) or
``{"sel": "...", "optional": true, "desc": "..."}``. An effect is
``{"op": "<name>", ...params}``; the names are the keys of ``EFFECTS``.
"""

from __future__ import annotations

from typing import Any

from ..engine import effects as fx
from ..engine.card import (
    ActivatedAbility,
    CardSpec,
    Chapter,
    Cost,
    Mode,
    Static,
    Trigger,
)
from ..engine.effects import Condition, TokenSpec
from ..engine.types import CardType, Keyword, ManaCost, TargetSpec

EFFECTS: dict[str, type[fx.Effect]] = {
    "add_mana": fx.AddMana,
    "damage": fx.DealDamage,
    "gain_life": fx.GainLife,
    "lose_life": fx.LoseLife,
    "draw": fx.DrawCards,
    "discard": fx.Discard,
    "recruit": fx.Recruit,
    "loot": fx.Loot,
    "mill": fx.Mill,
    "scry": fx.Scry,
    "search": fx.SearchLibrary,
    "look": fx.LookAtTop,
    "impulse": fx.Impulse,
    "additional_land": fx.AdditionalLand,
    "destroy": fx.Destroy,
    "exile": fx.Exile,
    "bounce": fx.ReturnToHand,
    "reanimate": fx.ReturnToBattlefield,
    "put_on_library": fx.PutOnLibrary,
    "shuffle_into_library": fx.ShuffleIntoLibrary,
    "sacrifice": fx.Sacrifice,
    "sacrifice_self": fx.SacrificeSelf,
    "counter": fx.CounterSpell,
    "return_spell": fx.ReturnSpellToHand,
    "pump": fx.Pump,
    "set_base_pt": fx.SetBasePT,
    "counters": fx.AddCounters,
    "remove_counters": fx.RemoveCounters,
    "tap": fx.Tap,
    "fight": fx.Fight,
    "attach": fx.Attach,
    "token": fx.CreateToken,
    "amass": fx.Amass,
    "if": fx.If,
    "delayed": fx.Delayed,
    "may_pay": fx.MayPay,
    "copy_self": fx.CopySelf,
    "reveal_until": fx.RevealUntil,
    "choose_type": fx.ChooseCreatureType,
    "flicker": fx.Flicker,
    "surveil": fx.Surveil,
    "empower_jace": fx.EmpowerJace,
    "add_loyalty": fx.AddLoyalty,
    "set_prepared": fx.SetPrepared,
    "stun": fx.Stun,
}

NAMED_TOKENS = {
    "treasure": fx.TREASURE, "soldier": fx.SOLDIER,
    "food": TokenSpec("Food", types=("Artifact",), subtypes=("Food",), kind="food"),
    # Reality Fracture
    "cadet": TokenSpec("Cadet", 2, 2, subtypes=("Wizard", "Soldier")),
    "thopter": TokenSpec("Thopter", 1, 1, types=("Artifact", "Creature"),
                         subtypes=("Thopter",), keywords=frozenset({fx.Keyword.FLYING})),
    "heartwood": TokenSpec("Heartwood", types=("Artifact",), subtypes=("Heartwood",),
                           mana=("R/G",)),
    "beast": TokenSpec("Beast", 4, 4, subtypes=("Beast",), colors=("G",),
                       keywords=frozenset({fx.Keyword.TRAMPLE})),
    "illusion": TokenSpec("Illusion", 1, 1, subtypes=("Illusion",), colors=("U",)),
    "dragon": TokenSpec("Dragon", 5, 5, subtypes=("Dragon",), colors=("R",),
                        keywords=frozenset({fx.Keyword.FLYING})),
}

CARD_KEYS = {
    "name", "cost", "types", "subtypes", "supertypes", "power", "toughness", "power_expr",
    "toughness_expr", "keywords", "ward", "mana", "enters_tapped", "targets", "effects",
    "modes", "triggers", "statics", "abilities", "equip", "enchant", "flashback", "kicker",
    "additional_costs", "cost_reduction", "cost_reduction_if", "adventure", "chapters",
    "storied", "approximations", "unsupported", "colors", "loyalty", "prepare",
    "enters_prepared", "enters_tapped_unless", "cast_if", "enters_with_counters",
    # Provenance, carried through for reports; not used by the engine.
    "rarity", "oracle", "collector_number", "arena_id", "color_identity", "note",
}


class CardDataError(ValueError):
    pass


def _check_keys(data: dict, allowed: set[str], where: str) -> None:
    extra = set(data) - allowed
    if extra:
        raise CardDataError(f"{where}: unknown key(s) {sorted(extra)}")


def keywords(values) -> frozenset[Keyword]:
    try:
        return frozenset(Keyword(v.lower()) for v in values or ())
    except ValueError as exc:
        raise CardDataError(f"unknown keyword in {values}") from exc


def target(value) -> TargetSpec:
    if isinstance(value, str):
        return TargetSpec(value)
    _check_keys(value, {"sel", "optional", "desc"}, "target")
    return TargetSpec(value["sel"], value.get("desc", ""), bool(value.get("optional")))


def targets(values) -> tuple[TargetSpec, ...]:
    return tuple(target(v) for v in values or ())


CONDITIONS = {
    "cast_from_graveyard", "cast_this_turn", "control", "creature_died_this_turn",
    "drawn_this_turn", "enduring_story", "graveyard", "it_matches", "kicked",
    "noncreature_cast_this_turn",
    "life_gained_this_turn", "opponent_controls", "prepared", "surveilled_this_turn",
    "target_matches", "your_turn"
}


def condition(value) -> Condition | None:
    if value is None:
        return None
    _check_keys(value, {"kind", "n", "filter", "negate"}, "condition")
    if value["kind"] not in CONDITIONS:
        raise CardDataError(f"unknown condition kind {value['kind']!r}")
    return Condition(value["kind"], value.get("n", 1), value.get("filter", ""),
                     bool(value.get("negate")))


def token(value) -> TokenSpec:
    if isinstance(value, str):
        if value not in NAMED_TOKENS:
            raise CardDataError(f"unknown named token {value!r}")
        return NAMED_TOKENS[value]
    _check_keys(value, {"name", "power", "toughness", "types", "subtypes", "colors",
                        "keywords", "kind", "equip_cost", "equip_power", "equip_toughness",
                        "mana"}, "token")
    return TokenSpec(
        name=value["name"], power=value.get("power", 0), toughness=value.get("toughness", 0),
        types=tuple(value.get("types", ["Creature"])), subtypes=tuple(value.get("subtypes", ())),
        colors=tuple(value.get("colors", ())), keywords=keywords(value.get("keywords")),
        kind=value.get("kind", ""), equip_cost=value.get("equip_cost", ""),
        equip_power=value.get("equip_power", 0), equip_toughness=value.get("equip_toughness", 0),
        mana=tuple(value.get("mana", ())))


def effect(value: dict) -> fx.Effect:
    if not isinstance(value, dict) or "op" not in value:
        raise CardDataError(f"an effect must be an object with an 'op': {value!r}")
    op = value["op"]
    cls = EFFECTS.get(op)
    if cls is None:
        raise CardDataError(f"unknown effect op {op!r}")
    params: dict[str, Any] = {k: v for k, v in value.items() if k != "op"}
    if op == "if":
        params["condition"] = condition(params.pop("cond"))
        params["then"] = effects(params.get("then"))
        params["otherwise"] = effects(params.get("otherwise"))
    elif op == "delayed":
        params["effects"] = effects(params.get("effects"))
    elif op == "may_pay":
        params["then"] = effects(params.get("then"))
    if "keywords" in params:
        params["keywords"] = keywords(params["keywords"])
    if "flags" in params:
        params["flags"] = frozenset(params["flags"])
    if "token" in params:
        params["token"] = token(params["token"])
    if "symbols" in params:
        params["symbols"] = tuple(params["symbols"])
    try:
        return cls(**params)
    except TypeError as exc:
        raise CardDataError(f"bad parameters for {op!r}: {exc}") from exc


def effects(values) -> tuple[fx.Effect, ...]:
    return tuple(effect(v) for v in values or ())


def trigger(value: dict) -> Trigger:
    _check_keys(value, {"when", "targets", "effects", "filter", "if", "once_per_turn", "text",
                        "modes", "zone"}, "trigger")
    modes = tuple(Mode(effects(m.get("effects")), targets(m.get("targets")), m.get("text", ""))
                  for m in value.get("modes", ()))
    return Trigger(value["when"], effects(value.get("effects")), targets(value.get("targets")),
                   value.get("filter", ""), condition(value.get("if")),
                   bool(value.get("once_per_turn")), value.get("text", ""), modes,
                   value.get("zone", "battlefield"))


def static(value: dict) -> Static:
    _check_keys(value, {"affects", "power", "toughness", "keywords", "flags", "ward", "if",
                        "text", "abilities"}, "static")
    return Static(value.get("affects", "self"), value.get("power", 0),
                  value.get("toughness", 0), keywords(value.get("keywords")),
                  frozenset(value.get("flags", ())), value.get("ward", 0),
                  condition(value.get("if")), value.get("text", ""),
                  tuple(ability(a) for a in value.get("abilities", ())))


ABILITY_KEYS = {"cost", "tap", "sacrifice_self", "sacrifice", "discard", "discard_self",
                "life", "zone", "sorcery", "once_per_turn", "targets", "effects", "mana",
                "text", "loyalty", "exile_self"}


def ability(value: dict) -> ActivatedAbility:
    _check_keys(value, ABILITY_KEYS, "ability")
    return ActivatedAbility(
        effects=effects(value.get("effects")), mana_cost=ManaCost.parse(value.get("cost", "")),
        tap_cost=bool(value.get("tap")), targets=targets(value.get("targets")),
        is_mana_ability=bool(value.get("mana")), sorcery_speed=bool(value.get("sorcery")),
        text=value.get("text", ""), sacrifice_self=bool(value.get("sacrifice_self")),
        sacrifice=value.get("sacrifice", ""), discard=value.get("discard", 0),
        discard_self=bool(value.get("discard_self")), life=value.get("life", 0),
        zone=value.get("zone", "battlefield"), once_per_turn=bool(value.get("once_per_turn")),
        loyalty=value.get("loyalty"), exile_self=bool(value.get("exile_self")))


def cost(value: dict) -> Cost:
    _check_keys(value, {"cost", "sacrifice", "discard", "life", "behold"}, "additional cost")
    return Cost(mana=ManaCost.parse(value.get("cost", "")), sacrifice=value.get("sacrifice", ""),
                discard=value.get("discard", 0), life=value.get("life", 0),
                behold=value.get("behold", ""))


def card(data: dict) -> CardSpec:
    """Build a ``CardSpec`` from a card entry. Raises ``CardDataError``."""
    name = data.get("name", "?")
    try:
        _check_keys(data, CARD_KEYS, name)
        if data.get("unsupported"):
            raise CardDataError(f"{name} is marked unsupported: {data['unsupported']}")
        types = frozenset(CardType(t) for t in data.get("types", ()))
        abilities = [ability(a) for a in data.get("abilities", ())]
        if data.get("mana"):
            abilities.insert(0, ActivatedAbility(effects=(fx.AddMana(tuple(data["mana"])),),
                                                 tap_cost=True, is_mana_ability=True))
        if data.get("equip") is not None:
            abilities.append(ActivatedAbility(
                effects=(fx.Attach(),), mana_cost=ManaCost.parse(data["equip"]),
                targets=(TargetSpec("creature:yours"),), sorcery_speed=True, is_equip=True,
                text=f"Equip {data['equip']}"))
        chapters = tuple(Chapter(effects(c.get("effects")), targets(c.get("targets")))
                         for c in data.get("chapters", ()))
        modes = tuple(Mode(effects(m.get("effects")), targets(m.get("targets")),
                           m.get("text", "")) for m in data.get("modes", ()))
        return CardSpec(
            name=name,
            cost=ManaCost.parse(data.get("cost", "")),
            types=types,
            subtypes=tuple(data.get("subtypes", ())),
            supertypes=frozenset(data.get("supertypes", ())),
            power=data.get("power"),
            toughness=data.get("toughness"),
            power_expr=data.get("power_expr", ""),
            toughness_expr=data.get("toughness_expr", ""),
            keywords=keywords(data.get("keywords")),
            colors=frozenset(data["colors"]) if "colors" in data else None,
            targets=targets(data.get("targets")),
            on_resolve=effects(data.get("effects")),
            modes=modes,
            triggers=tuple(trigger(t) for t in data.get("triggers", ())),
            statics=tuple(static(s) for s in data.get("statics", ())),
            abilities=tuple(abilities),
            enters_tapped=bool(data.get("enters_tapped")),
            ward=data.get("ward", 0),
            enchant=target(data["enchant"]) if data.get("enchant") else None,
            flashback=ManaCost.parse(data["flashback"]) if data.get("flashback") else None,
            kicker=ManaCost.parse(data["kicker"]) if data.get("kicker") else None,
            additional_costs=tuple(cost(c) for c in data.get("additional_costs", ())),
            cost_reduction=data.get("cost_reduction", 0),
            cost_reduction_if=condition(data.get("cost_reduction_if")),
            adventure=card(data["adventure"]) if data.get("adventure") else None,
            chapters=chapters,
            storied=bool(data.get("storied")),
            loyalty=data.get("loyalty"),
            prepare=card(data["prepare"]) if data.get("prepare") else None,
            enters_prepared=bool(data.get("enters_prepared")),
            enters_tapped_unless=condition(data.get("enters_tapped_unless")),
            cast_if=condition(data.get("cast_if")),
            enters_with_counters=data.get("enters_with_counters", 0),
            approximations=tuple(data.get("approximations", ())),
        )
    except CardDataError:
        raise
    except (KeyError, ValueError, TypeError) as exc:
        raise CardDataError(f"{name}: {exc}") from exc
