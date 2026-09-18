"""The complete set of choices an agent can ever be asked to make.

Every action an agent takes is one of these, and the engine only ever hands an
agent actions it has already checked. An agent cannot cheat, cannot make an
illegal play, and cannot get a rules interaction wrong — it can only choose
badly. That is the property the whole benchmark rests on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Target


@dataclass(frozen=True)
class Action:
    """Base class. Actions are values: hashable, comparable, and loggable."""


@dataclass(frozen=True)
class Pass(Action):
    """Pass priority. With an empty stack and both players passing, the game
    moves to the next step."""


@dataclass(frozen=True)
class PlayLand(Action):
    card_id: int


@dataclass(frozen=True)
class CastSpell(Action):
    """Cast a card. ``face`` is ``""`` for the card itself, ``"adventure"`` for
    its Adventure half, or ``"flashback"``. ``mode`` indexes a modal spell's
    modes; ``extra`` indexes its additional-cost options."""

    card_id: int
    targets: tuple[Target, ...] = ()
    mode: int = -1
    face: str = ""
    kicked: bool = False
    extra: int = -1


@dataclass(frozen=True)
class ActivateAbility(Action):
    source_id: int
    index: int
    targets: tuple[Target, ...] = ()


@dataclass(frozen=True)
class DeclareAttacker(Action):
    creature_id: int


@dataclass(frozen=True)
class DeclareBlocker(Action):
    blocker_id: int
    attacker_id: int


@dataclass(frozen=True)
class FinishDeclaring(Action):
    """End the attack or block declaration. Only offered when the declaration
    made so far is legal, so an agent cannot declare an illegal block."""


@dataclass(frozen=True)
class ChooseTargets(Action):
    """Choose targets for a triggered ability going on the stack."""

    targets: tuple[Target, ...] = ()


@dataclass(frozen=True)
class Discard(Action):
    card_id: int


@dataclass(frozen=True)
class Mulligan(Action):
    pass


@dataclass(frozen=True)
class KeepHand(Action):
    pass


@dataclass(frozen=True)
class PutOnBottom(Action):
    card_id: int
