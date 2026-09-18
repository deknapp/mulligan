"""Card definitions (immutable) and the game objects made from them (mutable)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .effects import Effect
from .types import CardType, Keyword, ManaCost, ManaPool, TargetSpec


@dataclass(frozen=True)
class ActivatedAbility:
    """``cost: effect``.

    Mana abilities do not use the stack and cannot be responded to (CR 605.3),
    which is why ``is_mana_ability`` exists rather than being inferred: an
    ability that produces mana *and* does something else is not a mana ability,
    and getting that wrong would silently change what can be responded to.
    """

    effects: tuple[Effect, ...]
    mana_cost: ManaCost = ManaCost()
    tap_cost: bool = False
    targets: tuple[TargetSpec, ...] = ()
    is_mana_ability: bool = False
    sorcery_speed: bool = False
    text: str = ""

    def describe(self) -> str:
        if self.text:
            return self.text
        cost_parts = []
        if self.tap_cost:
            cost_parts.append("{T}")
        if self.mana_cost.mana_value:
            cost_parts.insert(0, str(self.mana_cost))
        cost = "".join(cost_parts) or "{0}"
        return f"{cost}: " + ", ".join(e.describe() for e in self.effects) + "."


@dataclass(frozen=True)
class CardSpec:
    """A card as printed. Shared by every copy; never mutated during a game."""

    name: str
    cost: ManaCost = ManaCost()
    types: frozenset[CardType] = frozenset()
    subtypes: tuple[str, ...] = ()
    power: int | None = None
    toughness: int | None = None
    keywords: frozenset[Keyword] = frozenset()
    targets: tuple[TargetSpec, ...] = ()
    on_resolve: tuple[Effect, ...] = ()
    etb_targets: tuple[TargetSpec, ...] = ()
    on_etb: tuple[Effect, ...] = ()
    abilities: tuple[ActivatedAbility, ...] = ()
    enters_tapped: bool = False
    flavor_note: str = ""

    @property
    def is_permanent(self) -> bool:
        return bool(self.types & {CardType.LAND, CardType.CREATURE, CardType.ARTIFACT,
                                  CardType.ENCHANTMENT})

    @property
    def is_creature(self) -> bool:
        return CardType.CREATURE in self.types

    @property
    def is_land(self) -> bool:
        return CardType.LAND in self.types

    @property
    def type_line(self) -> str:
        order = [CardType.LAND, CardType.ARTIFACT, CardType.ENCHANTMENT, CardType.CREATURE,
                 CardType.INSTANT, CardType.SORCERY]
        line = " ".join(t.value for t in order if t in self.types)
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
        for ability in self.abilities:
            lines.append(ability.describe())
        if self.on_etb:
            body = ", ".join(e.describe() for e in self.on_etb)
            lines.append(f"When {self.name} enters the battlefield, {body}.")
        if self.on_resolve:
            lines.append(", ".join(e.describe() for e in self.on_resolve).capitalize() + ".")
        return "\n".join(lines)


class GameObject:
    """A single physical card (or token) and everything true of it right now."""

    __slots__ = ("id", "spec", "owner", "controller", "zone", "tapped", "damage",
                 "summoning_sick", "attacking", "blocking", "blocked_by", "counters",
                 "temp_power", "temp_toughness", "granted_keywords", "is_token",
                 "targets", "entered_turn", "was_blocked", "deathtouched", "attached_to")

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
        self.mulligans = 0
        self.lost = False
        self.loss_reason = ""

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
