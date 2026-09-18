"""How good is a card, before anyone has played the set?

``static_rating`` reads a card's compiled definition — stats for mana, evasion,
what its effects do — and returns a number on a rough "points" scale. It knows
nothing about any particular set, which is the point: on release day it is all
there is. Once games have been simulated (``mulligan rate``) or 17Lands data
exists, a ``ratings`` dict replaces it; the deckbuilder takes either.
"""

from __future__ import annotations

from ..agents.heuristic import creature_value
from ..engine import effects as fx
from ..engine.card import CardSpec
from ..engine.types import CardType

REMOVAL_VALUE = 5.0


def _effect_value(effect, targets_opponent: bool) -> float:
    if isinstance(effect, fx.If):
        return 0.6 * sum(_effect_value(e, targets_opponent) for e in effect.then)
    if isinstance(effect, (fx.Destroy, fx.Exile)):
        return REMOVAL_VALUE if not getattr(effect, "to", "").startswith("all:") else 4.0
    if isinstance(effect, fx.DealDamage):
        amount = effect.amount if isinstance(effect.amount, int) else 2
        if effect.to.startswith("all:"):
            return 1.2 * amount if "theirs" in effect.to else 0.8 * amount
        if effect.to in ("each_opponent",):
            return 0.5 * amount
        return min(REMOVAL_VALUE, 1.0 + 1.1 * amount)
    if isinstance(effect, fx.Fight):
        return 3.5
    if isinstance(effect, (fx.ReturnToHand, fx.PutOnLibrary)):
        return 2.0 if not isinstance(effect, fx.PutOnLibrary) else 3.5
    if isinstance(effect, fx.Pump):
        power = effect.power if isinstance(effect.power, int) else 2
        tough = effect.toughness if isinstance(effect.toughness, int) else 2
        if tough < 0:
            return min(REMOVAL_VALUE, -tough * 1.0 + 0.5)
        mass = 2.0 if effect.to.startswith("all:") else 1.0
        return mass * (0.4 * (power + tough) + 0.4 * len(effect.keywords) + 0.3)
    if isinstance(effect, fx.AddCounters):
        n = effect.count if isinstance(effect.count, int) else 2
        return (2.5 if effect.to.startswith("all:") else 1.0) * 0.8 * n
    if isinstance(effect, fx.DrawCards):
        n = effect.count if isinstance(effect.count, int) else 2
        return 1.4 * n
    if isinstance(effect, fx.Loot):
        return 0.6 * effect.draw
    if isinstance(effect, fx.Recruit):
        return 1.3
    if isinstance(effect, fx.CreateToken):
        t = effect.token
        n = effect.count if isinstance(effect.count, int) else 2
        if "Creature" in t.types:
            return 0.45 * n * creature_value(t.power, t.toughness, t.keywords)
        return 0.6 * n
    if isinstance(effect, fx.Amass):
        n = effect.count if isinstance(effect.count, int) else 2
        return 0.9 * n
    if isinstance(effect, fx.Sacrifice):
        return 3.0 if effect.who != "you" else -1.0
    if isinstance(effect, fx.Discard):
        return 1.0 if effect.who != "you" else -0.5
    if isinstance(effect, (fx.GainLife,)):
        return 0.15 * (effect.amount if isinstance(effect.amount, int) else 2)
    if isinstance(effect, fx.LoseLife):
        amount = effect.amount if isinstance(effect.amount, int) else 2
        return 0.3 * amount if effect.who != "you" else -0.15 * amount
    if isinstance(effect, fx.CounterSpell):
        return 2.5 if not effect.unless_pay else 1.5
    if isinstance(effect, (fx.Scry, fx.SearchLibrary, fx.LookAtTop, fx.Impulse)):
        return 0.8
    if isinstance(effect, fx.ReturnToBattlefield):
        return 3.0
    return 0.3


def _effects(effects) -> float:
    return sum(_effect_value(e, True) for e in effects)


def static_rating(spec: CardSpec) -> float:
    """Points a card is worth in a Limited deck, relative to its cost."""
    mv = spec.cost.mana_value
    value = 0.0
    if spec.is_creature:
        power = spec.power if spec.power is not None else 3
        tough = spec.toughness if spec.toughness is not None else 3
        value += creature_value(power, tough, spec.keywords) + 0.6 * spec.ward
        value -= 1.25 * mv  # a body is only as good as its rate
        value += 2.5  # having a body at all
    for trigger in spec.triggers:
        weight = 1.0 if trigger.when in ("etb", "dies", "landfall") else 0.7
        value += weight * _effects(trigger.effects)
    for static in spec.statics:
        p = static.power if isinstance(static.power, int) else 2
        t = static.toughness if isinstance(static.toughness, int) else 2
        scale = 2.5 if static.affects.startswith("all:") else 1.0
        cond = 0.5 if static.condition is not None else 1.0
        value += cond * scale * (0.5 * (p + t) + 0.5 * len(static.keywords)
                                 + (1.0 if "unblockable" in static.flags else 0.0))
        if static.affects == "enchanted" and {"loses_abilities", "doesnt_untap"} & set(
                static.flags):
            value += REMOVAL_VALUE - 1.0
    for ability in spec.abilities:
        if ability.is_mana_ability or ability.is_equip:
            continue
        value += 0.4 * _effects(ability.effects)
    if spec.equip_like:
        value += 1.0
    if not spec.is_creature:
        value += _effects(spec.on_resolve)
        if spec.modes:
            value += max(_effects(m.effects) for m in spec.modes) + 0.5
        for chapter in spec.chapters:
            value += 0.8 * _effects(chapter.effects)
        value -= 0.35 * max(0, mv - 2)
    if spec.adventure is not None:
        value += 0.7 * max(_effects(spec.adventure.on_resolve), 0.5) + 0.5
    if spec.flashback is not None:
        value += 1.0
    if CardType.LAND in spec.types:
        value = 0.0
    return round(value, 2)
