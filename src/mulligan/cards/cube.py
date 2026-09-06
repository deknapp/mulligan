"""A hand-written pool of classic cards.

Every card here is expressed in exactly the declarative form the set compiler
emits, so the compiler has a hand-verified reference to be checked against, and
the engine has a fixture whose behaviour can be confirmed by reading it.

Only cards whose full behaviour the engine implements are included. A card that
would need a rules feature the engine lacks is left out rather than approximated
— an approximated card is a silently wrong benchmark.
"""

from __future__ import annotations

from ..engine.card import ActivatedAbility, CardSpec
from ..engine.effects import (
    AddMana,
    CounterTargetSpell,
    CreateToken,
    DealDamage,
    DestroyAll,
    DestroyTarget,
    DrawCards,
    Fight,
    GainLife,
    LoseLife,
    Pump,
    ReturnTargetToHand,
)
from ..engine.types import CardType, Keyword, ManaCost, TargetSpec

CREATURE = frozenset({CardType.CREATURE})
INSTANT = frozenset({CardType.INSTANT})
SORCERY = frozenset({CardType.SORCERY})
LAND = frozenset({CardType.LAND})

ANY = TargetSpec("any_target", "any target")
CREATURE_TARGET = TargetSpec("creature", "target creature")
YOURS = TargetSpec("creature_you_control", "target creature you control")
THEIRS = TargetSpec("creature_you_dont_control", "target creature you don't control")
PLAYER = TargetSpec("player", "target player")
SPELL = TargetSpec("spell", "target spell")
BIG = TargetSpec("creature_power_4_or_greater", "target creature with power 4 or greater")


def _mana_ability(symbol: str) -> ActivatedAbility:
    return ActivatedAbility(effects=(AddMana((symbol,)),), tap_cost=True, is_mana_ability=True)


def basic_land(name: str, symbol: str) -> CardSpec:
    return CardSpec(name=name, types=LAND, subtypes=(name,), abilities=(_mana_ability(symbol),))


PLAINS = basic_land("Plains", "W")
ISLAND = basic_land("Island", "U")
SWAMP = basic_land("Swamp", "B")
MOUNTAIN = basic_land("Mountain", "R")
FOREST = basic_land("Forest", "G")

BASICS = {"W": PLAINS, "U": ISLAND, "B": SWAMP, "R": MOUNTAIN, "G": FOREST}


def _creature(name: str, cost: str, power: int, toughness: int, subtypes: tuple[str, ...],
              keywords: frozenset[Keyword] = frozenset(), **kwargs) -> CardSpec:
    return CardSpec(name=name, cost=ManaCost.parse(cost), types=CREATURE, subtypes=subtypes,
                    power=power, toughness=toughness, keywords=keywords, **kwargs)


# --------------------------------------------------------------------- white

SAVANNAH_LIONS = _creature("Savannah Lions", "W", 2, 1, ("Cat",))
SUNTAIL_HAWK = _creature("Suntail Hawk", "W", 1, 1, ("Bird",), frozenset({Keyword.FLYING}))
SERRA_ANGEL = _creature("Serra Angel", "3WW", 4, 4, ("Angel",),
                        frozenset({Keyword.FLYING, Keyword.VIGILANCE}))
ANGEL_OF_MERCY = _creature("Angel of Mercy", "4W", 3, 3, ("Angel",), frozenset({Keyword.FLYING}),
                           on_etb=(GainLife(3),))
WRATH_OF_GOD = CardSpec(name="Wrath of God", cost=ManaCost.parse("2WW"), types=SORCERY,
                        on_resolve=(DestroyAll(),))
SMITE_THE_MONSTROUS = CardSpec(name="Smite the Monstrous", cost=ManaCost.parse("3W"),
                               types=INSTANT, targets=(BIG,), on_resolve=(DestroyTarget(),))
RAISE_THE_ALARM = CardSpec(
    name="Raise the Alarm", cost=ManaCost.parse("1W"), types=INSTANT,
    on_resolve=(CreateToken("Soldier", 1, 1, ("Soldier",), count=2),))

# ---------------------------------------------------------------------- blue

MERFOLK = _creature("Merfolk of the Pearl Trident", "U", 1, 1, ("Merfolk",))
WIND_DRAKE = _creature("Wind Drake", "2U", 2, 2, ("Drake",), frozenset({Keyword.FLYING}))
AIR_ELEMENTAL = _creature("Air Elemental", "3UU", 4, 4, ("Elemental",),
                          frozenset({Keyword.FLYING}))
MAHAMOTI_DJINN = _creature("Mahamoti Djinn", "4UU", 5, 6, ("Djinn",), frozenset({Keyword.FLYING}))
MAN_O_WAR = _creature("Man-o'-War", "2U", 2, 2, ("Jellyfish",),
                      etb_targets=(CREATURE_TARGET,), on_etb=(ReturnTargetToHand(),))
COUNTERSPELL = CardSpec(name="Counterspell", cost=ManaCost.parse("UU"), types=INSTANT,
                        targets=(SPELL,), on_resolve=(CounterTargetSpell(),))
DIVINATION = CardSpec(name="Divination", cost=ManaCost.parse("2U"), types=SORCERY,
                      on_resolve=(DrawCards(2),))
UNSUMMON = CardSpec(name="Unsummon", cost=ManaCost.parse("U"), types=INSTANT,
                    targets=(CREATURE_TARGET,), on_resolve=(ReturnTargetToHand(),))

# --------------------------------------------------------------------- black

WALKING_CORPSE = _creature("Walking Corpse", "1B", 2, 2, ("Zombie",))
VAMPIRE_NIGHTHAWK = _creature("Vampire Nighthawk", "1BB", 2, 3, ("Vampire",),
                              frozenset({Keyword.FLYING, Keyword.DEATHTOUCH, Keyword.LIFELINK}))
ZOMBIE_GOLIATH = _creature("Zombie Goliath", "4B", 5, 3, ("Zombie",))
MURDER = CardSpec(name="Murder", cost=ManaCost.parse("1BB"), types=INSTANT,
                  targets=(CREATURE_TARGET,), on_resolve=(DestroyTarget(),))
SIGN_IN_BLOOD = CardSpec(name="Sign in Blood", cost=ManaCost.parse("BB"), types=SORCERY,
                         targets=(PLAYER,),
                         on_resolve=(DrawCards(2, who="target"), LoseLife(2, who="target")))

# ----------------------------------------------------------------------- red

RAGING_GOBLIN = _creature("Raging Goblin", "R", 1, 1, ("Goblin",), frozenset({Keyword.HASTE}))
GOBLIN_PIKER = _creature("Goblin Piker", "1R", 2, 1, ("Goblin",))
FLAMETONGUE_KAVU = _creature("Flametongue Kavu", "3R", 4, 2, ("Kavu",),
                             etb_targets=(CREATURE_TARGET,), on_etb=(DealDamage(4),))
SHIVAN_DRAGON = _creature(
    "Shivan Dragon", "4RR", 5, 5, ("Dragon",), frozenset({Keyword.FLYING}),
    abilities=(ActivatedAbility(effects=(Pump(power=1, self_target=True),),
                                mana_cost=ManaCost.parse("R"), text="{R}: it gets +1/+0."),))
SHOCK = CardSpec(name="Shock", cost=ManaCost.parse("R"), types=INSTANT, targets=(ANY,),
                 on_resolve=(DealDamage(2),))
LIGHTNING_BOLT = CardSpec(name="Lightning Bolt", cost=ManaCost.parse("R"), types=INSTANT,
                          targets=(ANY,), on_resolve=(DealDamage(3),))
VOLCANIC_HAMMER = CardSpec(name="Volcanic Hammer", cost=ManaCost.parse("1R"), types=SORCERY,
                           targets=(ANY,), on_resolve=(DealDamage(3),))
PYROCLASM = CardSpec(name="Pyroclasm", cost=ManaCost.parse("1R"), types=SORCERY,
                     on_resolve=(DealDamage(2, scope="all_creatures"),))

# --------------------------------------------------------------------- green

LLANOWAR_ELVES = _creature("Llanowar Elves", "G", 1, 1, ("Elf", "Druid"),
                           abilities=(_mana_ability("G"),))
GRIZZLY_BEARS = _creature("Grizzly Bears", "1G", 2, 2, ("Bear",))
ELVISH_VISIONARY = _creature("Elvish Visionary", "1G", 1, 1, ("Elf",), on_etb=(DrawCards(1),))
GIANT_SPIDER = _creature("Giant Spider", "3G", 2, 4, ("Spider",), frozenset({Keyword.REACH}))
CRAW_WURM = _creature("Craw Wurm", "4GG", 6, 4, ("Wurm",))
COLOSSAL_DREADMAW = _creature("Colossal Dreadmaw", "5G", 6, 6, ("Dinosaur",),
                              frozenset({Keyword.TRAMPLE}))
GIANT_GROWTH = CardSpec(name="Giant Growth", cost=ManaCost.parse("G"), types=INSTANT,
                        targets=(CREATURE_TARGET,), on_resolve=(Pump(3, 3),))
PREY_UPON = CardSpec(name="Prey Upon", cost=ManaCost.parse("G"), types=SORCERY,
                     targets=(YOURS, THEIRS), on_resolve=(Fight(),))

CUBE: dict[str, CardSpec] = {
    spec.name: spec
    for spec in [
        PLAINS, ISLAND, SWAMP, MOUNTAIN, FOREST,
        SAVANNAH_LIONS, SUNTAIL_HAWK, SERRA_ANGEL, ANGEL_OF_MERCY, WRATH_OF_GOD,
        SMITE_THE_MONSTROUS, RAISE_THE_ALARM,
        MERFOLK, WIND_DRAKE, AIR_ELEMENTAL, MAHAMOTI_DJINN, MAN_O_WAR, COUNTERSPELL,
        DIVINATION, UNSUMMON,
        WALKING_CORPSE, VAMPIRE_NIGHTHAWK, ZOMBIE_GOLIATH, MURDER, SIGN_IN_BLOOD,
        RAGING_GOBLIN, GOBLIN_PIKER, FLAMETONGUE_KAVU, SHIVAN_DRAGON, SHOCK,
        LIGHTNING_BOLT, VOLCANIC_HAMMER, PYROCLASM,
        LLANOWAR_ELVES, GRIZZLY_BEARS, ELVISH_VISIONARY, GIANT_SPIDER, CRAW_WURM,
        COLOSSAL_DREADMAW, GIANT_GROWTH, PREY_UPON,
    ]
}


def card(name: str) -> CardSpec:
    return CUBE[name]


def build_deck(spell_counts: dict[str, int], lands: dict[str, int]) -> list[CardSpec]:
    deck: list[CardSpec] = []
    for name, count in spell_counts.items():
        deck.extend([CUBE[name]] * count)
    for symbol, count in lands.items():
        deck.extend([BASICS[symbol]] * count)
    return deck


DECK_LISTS: dict[str, tuple[dict[str, int], dict[str, int]]] = {
    "boros-aggro": (
        {"Savannah Lions": 4, "Raging Goblin": 4, "Goblin Piker": 4, "Suntail Hawk": 4,
         "Lightning Bolt": 4, "Shock": 4, "Volcanic Hammer": 3, "Serra Angel": 2,
         "Flametongue Kavu": 2, "Raise the Alarm": 3},
        {"R": 13, "W": 13},
    ),
    "dimir-control": (
        {"Counterspell": 4, "Murder": 4, "Divination": 4, "Unsummon": 3, "Man-o'-War": 4,
         "Vampire Nighthawk": 4, "Air Elemental": 3, "Mahamoti Djinn": 2, "Sign in Blood": 2},
        {"U": 15, "B": 15},
    ),
    "gruul-midrange": (
        {"Llanowar Elves": 4, "Grizzly Bears": 4, "Giant Spider": 3, "Craw Wurm": 2,
         "Colossal Dreadmaw": 2, "Giant Growth": 4, "Prey Upon": 3, "Lightning Bolt": 4,
         "Flametongue Kavu": 3, "Shivan Dragon": 2},
        {"G": 15, "R": 14},
    ),
    "azorius-skies": (
        {"Suntail Hawk": 4, "Wind Drake": 4, "Air Elemental": 3, "Serra Angel": 3,
         "Angel of Mercy": 2, "Counterspell": 4, "Unsummon": 3, "Divination": 3,
         "Smite the Monstrous": 3},
        {"W": 15, "U": 16},
    ),
    "golgari-grind": (
        {"Llanowar Elves": 4, "Elvish Visionary": 4, "Walking Corpse": 4,
         "Vampire Nighthawk": 4, "Zombie Goliath": 2, "Craw Wurm": 2, "Murder": 4,
         "Sign in Blood": 3, "Prey Upon": 3},
        {"G": 15, "B": 15},
    ),
    "mono-red-burn": (
        {"Raging Goblin": 4, "Goblin Piker": 4, "Lightning Bolt": 4, "Shock": 4,
         "Volcanic Hammer": 4, "Pyroclasm": 3, "Flametongue Kavu": 4, "Shivan Dragon": 2},
        {"R": 31},
    ),
}

DECKS: dict[str, list[CardSpec]] = {
    name: build_deck(spells, lands) for name, (spells, lands) in DECK_LISTS.items()
}
