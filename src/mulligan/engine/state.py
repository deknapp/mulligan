"""The mutable game state: a plain container, with no rules in it.

Rules live in :mod:`mulligan.engine.game`. Keeping them out of here means the
state can be inspected, rendered, diffed or serialised without any risk of
accidentally advancing the game.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .card import GameObject, Player, StackItem
from .types import Step


@dataclass
class GameState:
    players: list[Player]
    objects: dict[int, GameObject] = field(default_factory=dict)
    stack: list[StackItem] = field(default_factory=list)
    step: Step = Step.UNTAP
    turn: int = 0
    active: int = 0
    on_the_play: int = 0
    priority: int = 0
    passes: int = 0
    over: bool = False
    winner: int | None = None
    result_reason: str = ""
    rng: random.Random = field(default_factory=random.Random)
    log: list[str] = field(default_factory=list)

    # What kind of decision is outstanding, and whose it is. One of:
    # "mulligan", "bottom", "priority", "attackers", "blockers", "discard",
    # "trigger_targets".
    pending: str = "mulligan"
    decision_player: int = 0

    # Scratch space for multi-part decisions.
    mulligan_counts: list[int] = field(default_factory=list)
    mulligan_decided: list[bool] = field(default_factory=list)
    pending_trigger: StackItem | None = None
    attackers_declared: list[int] = field(default_factory=list)
    attack_targets: dict[int, int] = field(default_factory=dict)
    blocks_declared: list[tuple[int, int]] = field(default_factory=list)
    # Declaring no blocks is a real declaration. Without a flag distinguishing
    # "has not declared yet" from "declared nothing", the engine re-asks
    # forever.
    blockers_done: bool = False
    decisions: int = 0
    creature_died_this_turn: bool = False

    def opponent(self, seat: int) -> int:
        return 1 - seat

    def obj(self, obj_id: int) -> GameObject:
        return self.objects[obj_id]

    def zone_objects(self, seat: int, zone: str) -> list[GameObject]:
        return [self.objects[i] for i in self.players[seat].zone(zone)]

    def record(self, message: str) -> None:
        self.log.append(f"T{self.turn}/{self.step.value}: {message}")
