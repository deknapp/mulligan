"""Card definitions (immutable) and the game objects made from them (mutable).

A ``CardSpec`` is pure data: costs, types, and lists of declarative abilities
built from ``effects``. Everything a real set needs is expressed with a handful
of shapes:

* ``Trigger``   — "When/Whenever/At ... , do ..." (``when`` names the event)
* ``Static``    — "Creatures you control get +1/+1", "Equipped creature has
  menace", "As long as ..., this has vigilance"
* ``ActivatedAbility`` — "cost: effect", including equip and cycling
* ``Mode``      — one choice of a modal spell ("Choose one —")
* ``chapters``  — a Saga's numbered abilities

The event names a ``Trigger`` can use are listed in ``TRIGGER_EVENTS``; the
engine raises them in ``Game._fire``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .effects import Condition, Effect
from .types import CardType, Keyword, ManaCost, ManaPool, TargetSpec

TRIGGER_EVENTS = {
    "etb": "this permanent enters",
    "other_etb": "another permanent matching ``filter`` enters under your control",
    "landfall": "a land enters under your control",
    "dies": "this permanent is put into a graveyard from the battlefield",
    "other_dies": "another creature matching ``filter`` you control dies",
    "any_dies": "another creature (any controller) dies",
    "attacks": "this creature attacks",
    "you_attack": "you attack with one or more creatures",
    "combat_damage_player": "this creature deals combat damage to a player",
    "upkeep": "the beginning of your upkeep",
    "begin_combat": "the beginning of combat on your turn",
    "first_main": "the beginning of your precombat main phase",
    "end_step": "the beginning of your end step",
    "cast_noncreature": "you cast a noncreature spell",
    "cast_creature": "you cast a creature spell",
    "cast_spell": "you cast a spell",
    "opp_cast_noncreature": "an opponent casts a noncreature spell",
    "draw_second": "you draw your second card each turn",
    "opp_draw_second": "an opponent draws their second card each turn",
    "draw": "you draw a card",
    "counters_placed": "you put +1/+1 counters on a permanent matching ``filter``",
    "creature_leaves_graveyard": "a creature card leaves your graveyard",
    "gain_life": "you gain life",
    "scry_or_surveil": "you scry or surveil",
    "loyalty_counters": "you put loyalty counters on a planeswalker",
    "opponent_noncombat_damaged": "an opponent is dealt noncombat damage",
    "any_discard": "a player discards a card",
    "discarded": "this card is discarded (it triggers from the graveyard)",
    "cast_prepared": "you cast a prepared spell (a copy of a prepare creature's spell)",
}


@dataclass(frozen=True)
class Cost:
    """Everything that can be paid to cast or activate something."""

    mana: ManaCost = ManaCost()
    tap: bool = False
    sacrifice_self: bool = False
    sacrifice: str = ""      # a filter: sacrifice one matching permanent (the engine picks)
    discard: int = 0         # discard this many cards (the engine picks)
    discard_self: bool = False
    life: int = 0
    behold: str = ""         # a filter: control a matching permanent or reveal one from hand

    @property
    def is_free(self) -> bool:
        return (self.mana.mana_value == 0 and not (self.tap or self.sacrifice_self
                                                   or self.sacrifice or self.discard
                                                   or self.discard_self or self.life))


@dataclass(frozen=True)
class ActivatedAbility:
    """``cost: effect``.

    Mana abilities do not use the stack and cannot be responded to (CR 605.3),
    which is why ``is_mana_ability`` exists rather than being inferred: an
    ability that produces mana *and* does something else is not a mana ability,
    and getting that wrong would silently change what can be responded to.

    ``zone`` is where the ability works from: ``battlefield``, or ``hand`` for
    cycling-style abilities, or ``graveyard``.
    """

    effects: tuple[Effect, ...]
    mana_cost: ManaCost = ManaCost()
    tap_cost: bool = False
    targets: tuple[TargetSpec, ...] = ()
    is_mana_ability: bool = False
    sorcery_speed: bool = False
    text: str = ""
    sacrifice_self: bool = False
    sacrifice: str = ""
    discard: int = 0
    discard_self: bool = False
    life: int = 0
    zone: str = "battlefield"
    once_per_turn: bool = False
    is_equip: bool = False
    # A planeswalker's loyalty ability: +N / -N (0 is "0:"); None otherwise.
    loyalty: int | None = None
    exile_self: bool = False   # "Exile this card from your graveyard: ..."
    only_if: Condition | None = None  # "Activate only if ..."
    crew: int = 0   # "Crew N": tap other creatures with total power N or more
    exhaust: bool = False  # "Exhaust —": activate only once per game

    @property
    def cost(self) -> Cost:
        return Cost(self.mana_cost, self.tap_cost, self.sacrifice_self, self.sacrifice,
                    self.discard, self.discard_self, self.life)

    def describe(self) -> str:
        if self.text:
            return self.text
        cost_parts = []
        if self.mana_cost.mana_value:
            cost_parts.append(str(self.mana_cost))
        if self.tap_cost:
            cost_parts.append("{T}")
        if self.sacrifice_self:
            cost_parts.append("sacrifice it")
        if self.sacrifice:
            cost_parts.append(f"sacrifice a {self.sacrifice}")
        if self.life:
            cost_parts.append(f"pay {self.life} life")
        cost = ", ".join(cost_parts) or "{0}"
        return f"{cost}: " + ", ".join(e.describe() for e in self.effects) + "."


@dataclass(frozen=True)
class Mode:
    effects: tuple[Effect, ...]
    targets: tuple[TargetSpec, ...] = ()
    text: str = ""


@dataclass(frozen=True)
class Trigger:
    when: str
    effects: tuple[Effect, ...]
    targets: tuple[TargetSpec, ...] = ()
    filter: str = ""
    condition: Condition | None = None
    once_per_turn: bool = False
    text: str = ""
    # "Choose one —" triggers: the controller picks a mode as it goes on the stack.
    modes: tuple[Mode, ...] = ()
    # Where the source must be for this to trigger: "battlefield" or "graveyard".
    zone: str = "battlefield"

    def describe(self) -> str:
        return self.text or (f"{TRIGGER_EVENTS.get(self.when, self.when)}: "
                             + ", ".join(e.describe() for e in self.effects))


@dataclass(frozen=True)
class Static:
    """A continuous effect. ``affects`` is ``self``, ``equipped``,
    ``enchanted``, or ``all:<filter>``. ``flags`` are rules restrictions such as
    ``cant_block``, ``cant_attack``, ``unblockable``, ``doesnt_untap``,
    ``loses_abilities``, ``cant_be_blocked_by:<filter>``."""

    affects: str = "self"
    power: int | str = 0
    toughness: int | str = 0
    keywords: frozenset[Keyword] = frozenset()
    flags: frozenset[str] = frozenset()
    ward: int = 0
    condition: Condition | None = None
    text: str = ""
    # Activated abilities granted to what it affects ("Planeswalkers you
    # control have '[-2]: ...'").
    abilities: tuple[ActivatedAbility, ...] = ()
    # Spell cost changes: generic mana added to (or, negative, removed from)
    # spells matching ``spell_filter`` cast by you / by your opponents.
    your_spells: int = 0
    opponent_spells: int = 0
    spell_filter: str = "card"
    # "is a creature with base power and toughness 5/5 in addition to ..."
    add_types: frozenset[str] = frozenset()
    base_power: int | None = None
    base_toughness: int | None = None


@dataclass(frozen=True)
class Chapter:
    effects: tuple[Effect, ...]
    targets: tuple[TargetSpec, ...] = ()


@dataclass(frozen=True)
class CardSpec:
    """A card as printed. Shared by every copy; never mutated during a game."""

    name: str
    cost: ManaCost = ManaCost()
    types: frozenset[CardType] = frozenset()
    subtypes: tuple[str, ...] = ()
    supertypes: frozenset[str] = frozenset()
    power: int | None = None
    toughness: int | None = None
    # "*" stats: an amount expression evaluated continuously (see Game.amount).
    power_expr: str = ""
    toughness_expr: str = ""
    keywords: frozenset[Keyword] = frozenset()
    colors: frozenset[str] | None = None  # None: derived from the mana cost
    # Instants and sorceries: targets + on_resolve, or a list of modes.
    targets: tuple[TargetSpec, ...] = ()
    on_resolve: tuple[Effect, ...] = ()
    modes: tuple[Mode, ...] = ()
    triggers: tuple[Trigger, ...] = ()
    statics: tuple[Static, ...] = ()
    abilities: tuple[ActivatedAbility, ...] = ()
    enters_tapped: bool = False
    ward: int = 0
    # Auras: what they enchant (the target chosen on cast).
    enchant: TargetSpec | None = None
    # Alternative and additional casting.
    flashback: ManaCost | None = None
    kicker: ManaCost | None = None
    additional_costs: tuple[Cost, ...] = ()   # choose one of these, if any
    cost_reduction: int | str = 0
    cost_reduction_if: Condition | None = None
    adventure: CardSpec | None = None
    chapters: tuple[Chapter, ...] = ()
    storied: bool = False
    loyalty: int | None = None
    # A prepare card's spell: while the creature is prepared you may cast a copy.
    prepare: CardSpec | None = None
    enters_prepared: bool = False
    enters_tapped_unless: Condition | None = None
    # "You can't cast this spell unless ..."
    cast_if: Condition | None = None
    # "This creature enters with N +1/+1 counters" (an amount, e.g. "x"): part of
    # entering, not a trigger, so a 0-toughness body survives.
    enters_with_counters: int | str = 0
    flavor_note: str = ""
    # For the set compiler's coverage report: what (if anything) was left out.
    approximations: tuple[str, ...] = ()

    @property
    def is_permanent(self) -> bool:
        return bool(self.types & {CardType.LAND, CardType.CREATURE, CardType.ARTIFACT,
                                  CardType.ENCHANTMENT, CardType.PLANESWALKER})

    @property
    def is_creature(self) -> bool:
        return CardType.CREATURE in self.types

    @property
    def is_land(self) -> bool:
        return CardType.LAND in self.types

    @property
    def equip_like(self) -> bool:
        """An Equipment or Aura: its value is what it grants."""
        return any(a.is_equip for a in self.abilities) or self.enchant is not None

    @property
    def is_instant(self) -> bool:
        return CardType.INSTANT in self.types or Keyword.FLASH in self.keywords

    @property
    def color_set(self) -> frozenset[str]:
        if self.colors is not None:
            return self.colors
        return frozenset(c.value for c in self.cost.colors)

    @property
    def etb_effects(self) -> tuple[Effect, ...]:
        return tuple(e for t in self.triggers if t.when == "etb" for e in t.effects)

    @property
    def type_line(self) -> str:
        order = [CardType.LAND, CardType.ARTIFACT, CardType.ENCHANTMENT, CardType.CREATURE,
                 CardType.INSTANT, CardType.SORCERY]
        line = " ".join(t.value for t in order if t in self.types)
        line = " ".join(sorted(self.supertypes)) + (" " if self.supertypes else "") + line
        return f"{line} — {' '.join(self.subtypes)}" if self.subtypes else line

    def oracle_text(self) -> str:
        """Rules text, generated from the card's own effects.

        Generating it means the text an agent reads and the behaviour the engine
        executes cannot drift apart — a printed reminder line that lies about
        what a card does would poison every agent that reads it.
        """
        lines: list[str] = []
        if self.keywords:
            lines.append(", ".join(k.value.capitalize() for k in
                                   sorted(self.keywords, key=lambda k: k.value)))
        if self.ward:
            lines.append(f"Ward {{{self.ward}}}")
        for static in self.statics:
            lines.append(static.text or f"static: {static.affects} {static.power}/"
                         f"{static.toughness} {sorted(k.value for k in static.keywords)}"
                         f" {sorted(static.flags)}")
        for trigger in self.triggers:
            lines.append(trigger.describe())
        for ability in self.abilities:
            lines.append(ability.describe())
        if self.on_resolve:
            lines.append(", ".join(e.describe() for e in self.on_resolve).capitalize() + ".")
        for mode in self.modes:
            lines.append("• " + (mode.text or ", ".join(e.describe() for e in mode.effects)))
        for index, chapter in enumerate(self.chapters, 1):
            lines.append(f"{index} — " + ", ".join(e.describe() for e in chapter.effects))
        if self.flashback:
            lines.append(f"Flashback {self.flashback}")
        if self.adventure:
            lines.append(f"Adventure — {self.adventure.name} {self.adventure.cost}: "
                         + self.adventure.oracle_text().replace("\n", " "))
        return "\n".join(lines)


class GameObject:
    """A single physical card (or token) and everything true of it right now."""

    __slots__ = ("id", "spec", "owner", "controller", "zone", "tapped", "damage",
                 "summoning_sick", "attacking", "blocking", "blocked_by", "counters",
                 "temp_power", "temp_toughness", "granted_keywords", "is_token",
                 "targets", "entered_turn", "was_blocked", "deathtouched", "attached_to",
                 "temp_flags", "base_override", "lore", "on_adventure", "playable_until",
                 "linked_to", "activations", "cast_face", "loyalty", "stun", "prepared",
                 "attack_target", "x_paid", "chosen", "finality", "temp_types",
                 "exhausted")

    def __init__(self, obj_id: int, spec: CardSpec, owner: int, *, is_token: bool = False):
        self.id = obj_id
        self.spec = spec
        self.owner = owner
        self.controller = owner
        self.zone = "library"
        self.tapped = False
        self.damage = 0
        self.summoning_sick = True
        self.attacking = False
        self.blocking: int | None = None
        self.blocked_by: list[int] = []
        self.counters = 0
        self.temp_power = 0
        self.temp_toughness = 0
        self.granted_keywords: set[Keyword] = set()
        self.is_token = is_token
        self.targets: tuple = ()
        self.entered_turn = -1
        self.was_blocked = False
        # Damage from a deathtouch source is lethal regardless of amount, so it
        # has to be remembered rather than compared against toughness later.
        self.deathtouched = False
        # Id of the permanent an Aura or Equipment is attached to, or None.
        self.attached_to: int | None = None
        self.temp_flags: set[str] = set()
        self.base_override: tuple[int, int] | None = None
        self.lore = 0
        # An Adventure card in exile that may now be cast as its creature.
        self.on_adventure = False
        # An impulse-exiled card: playable until this turn number's end.
        self.playable_until: int | None = None
        # Exiled "until X leaves the battlefield": the id of X.
        self.linked_to: int | None = None
        # Activations this turn, by ability index (for "only once each turn").
        self.activations: dict[int, int] = {}
        # Which face is on the stack: "" (the card) or "adventure".
        self.cast_face = ""
        self.loyalty = 0
        self.stun = 0
        self.prepared = False
        self.attack_target: int | None = None  # a planeswalker it is attacking
        self.x_paid = 0          # X chosen when it was cast ("enters with X counters")
        self.chosen = ""         # a creature type chosen as it entered / resolved
        self.finality = False    # a finality counter: exiled instead of dying
        self.temp_types: set[CardType] = set()  # e.g. a crewed Vehicle: until end of turn
        self.exhausted: set[int] = set()  # exhaust abilities already used, by index

    @property
    def name(self) -> str:
        return self.spec.name

    def keywords(self) -> frozenset[Keyword]:
        return frozenset(self.spec.keywords) | frozenset(self.granted_keywords)

    def has(self, keyword: Keyword) -> bool:
        return keyword in self.spec.keywords or keyword in self.granted_keywords

    def reset_end_of_turn(self) -> None:
        self.damage = 0
        self.deathtouched = False
        self.temp_power = 0
        self.temp_toughness = 0
        self.granted_keywords.clear()
        self.temp_flags.clear()
        self.base_override = None
        self.temp_types.clear()

    def clear_combat(self) -> None:
        self.attacking = False
        self.blocking = None
        self.blocked_by = []
        self.was_blocked = False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name}#{self.id} {self.zone}{' tapped' if self.tapped else ''}>"


class Player:
    """A seat at the table."""

    def __init__(self, seat: int, name: str, starting_life: int = 20):
        self.seat = seat
        self.name = name
        self.life = starting_life
        self.library: list[int] = []
        self.hand: list[int] = []
        self.battlefield: list[int] = []
        self.graveyard: list[int] = []
        self.exile: list[int] = []
        self.pool = ManaPool()
        self.lands_played = 0
        self.extra_land_drops = 0
        self.mulligans = 0
        self.lost = False
        self.loss_reason = ""
        self.draws_this_turn = 0
        # Every card that reached this hand by drawing (opening hand included):
        # what "games in hand" win rates are computed over.
        self.seen: set[int] = set()
        self.enduring_story = False
        self.spells_cast_this_turn = 0
        self.life_gained_this_turn = 0
        self.noncreature_cast_this_turn = 0
        self.noncombat_damage_this_turn = 0
        self.copy_next: str = ""  # "when you next cast <filter> this turn, copy it"
        self.surveilled_this_turn = False
        # Cards put into this graveyard from this library this turn: what
        # "cards milled this turn" counts (Cruel Calculations).
        self.milled_this_turn = 0

    def zone(self, name: str) -> list[int]:
        return getattr(self, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Player {self.name} life={self.life}>"


@dataclass
class StackItem:
    """A spell or ability waiting to resolve."""

    obj_id: int | None
    controller: int
    name: str
    kind: str  # "spell" | "activated" | "triggered"
    effects: tuple[Effect, ...] = ()
    targets: tuple = ()
    is_permanent_spell: bool = False
    source_id: int | None = None
    extra: dict = field(default_factory=dict)
