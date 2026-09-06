"""The rules engine.

The contract with an agent is one method pair:

    actions = game.legal_actions()   # never empty while the game is running
    game.apply(actions[i])           # only ever an action the engine offered

Everything else — priority, the stack, triggers, combat, state-based actions,
tapping lands for mana — is the engine's job. Agents choose; they never assert.
"""

from __future__ import annotations

import itertools
import random
from collections import Counter

from . import actions as act
from .card import ActivatedAbility, CardSpec, GameObject, Player, StackItem
from .effects import AddMana, Context, Effect
from .state import GameState
from .types import (
    MANA_SYMBOLS,
    TURN_SEQUENCE,
    CardType,
    Keyword,
    ManaCost,
    ManaPool,
    Step,
    Target,
    TargetSpec,
)

MAX_HAND_SIZE = 7
MAX_MULLIGANS = 6
TARGET_COMBINATION_CAP = 64
"""Multi-target spells enumerate every combination of legal targets. The cap
stops a pathological board from producing a five-figure action list; no card in
the current pool has more than two targets, so it is never hit in practice."""


class IllegalAction(Exception):
    """Raised when an agent submits an action the engine did not offer."""


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

    def all_creatures(self) -> list[GameObject]:
        return [o for o in self.state.objects.values()
                if o.zone == "battlefield" and o.spec.is_creature]

    def creatures_of(self, seat: int) -> list[GameObject]:
        return [o for o in self.state.zone_objects(seat, "battlefield") if o.spec.is_creature]

    def power_of(self, obj: GameObject) -> int:
        return max(0, (obj.spec.power or 0) + obj.temp_power + obj.counters)

    def toughness_of(self, obj: GameObject) -> int:
        return (obj.spec.toughness or 0) + obj.temp_toughness + obj.counters

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
        if not is_setup:
            self.state.record(f"{player.name} draws a card")

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

    def deal_damage(self, target: Target, amount: int, source_id: int | None = None) -> None:
        if amount <= 0:
            return
        source = self.object_by_id(source_id)
        if target.kind == "player":
            self.state.players[target.id].life -= amount
            self.state.record(
                f"{source.name if source else 'an effect'} deals {amount} to "
                f"{self.state.players[target.id].name}"
            )
        else:
            obj = self.object_by_id(target.id)
            if obj is None or obj.zone != "battlefield":
                return
            obj.damage += amount
            if source is not None and source.has(Keyword.DEATHTOUCH):
                obj.deathtouched = True
            self.state.record(
                f"{source.name if source else 'an effect'} deals {amount} to {obj.name}"
            )
        if source is not None and source.has(Keyword.LIFELINK):
            self.gain_life(source.controller, amount)

    def pump(self, obj_id: int, power: int, toughness: int,
             keywords: frozenset[Keyword], until_end_of_turn: bool) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield":
            return
        if until_end_of_turn:
            obj.temp_power += power
            obj.temp_toughness += toughness
            obj.granted_keywords |= set(keywords)
        else:
            obj.counters += power  # only symmetric +1/+1 counters exist in this pool
        self.state.record(f"{obj.name} gets {power:+d}/{toughness:+d}")

    def destroy(self, obj_id: int) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None or obj.zone != "battlefield":
            return
        self.state.record(f"{obj.name} is destroyed")
        self.move_to_zone(obj_id, "graveyard")

    def move_to_zone(self, obj_id: int, zone: str) -> None:
        obj = self.object_by_id(obj_id)
        if obj is None:
            return
        owner = self.state.players[obj.owner]
        controller = self.state.players[obj.controller]
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
        obj.counters = 0
        obj.controller = obj.owner
        if obj.is_token and zone != "battlefield":
            # A token that leaves the battlefield ceases to exist (CR 111.7).
            obj.zone = "nonexistent"
            return
        obj.zone = zone
        owner.zone(zone).append(obj_id)

    def counter_spell(self, obj_id: int) -> None:
        for index, item in enumerate(self.state.stack):
            if item.obj_id == obj_id and item.kind == "spell":
                self.state.stack.pop(index)
                self.state.record(f"{item.name} is countered")
                self.move_to_zone(obj_id, "graveyard")
                return

    def create_token(self, seat: int, effect) -> None:
        spec = CardSpec(
            name=effect.name,
            types=frozenset({CardType.CREATURE}),
            subtypes=effect.subtypes,
            power=effect.power,
            toughness=effect.toughness,
            keywords=effect.keywords,
        )
        obj = self._new_object(spec, seat, is_token=True)
        self._put_onto_battlefield(obj, seat)

    def _put_onto_battlefield(self, obj: GameObject, seat: int) -> None:
        obj.zone = "battlefield"
        obj.controller = seat
        obj.entered_turn = self.state.turn
        obj.summoning_sick = obj.spec.is_creature and not obj.has(Keyword.HASTE)
        obj.tapped = obj.spec.enters_tapped
        self.state.players[seat].battlefield.append(obj.id)
        self.state.record(f"{obj.name} enters the battlefield under {self.state.players[seat].name}")
        if obj.spec.on_etb:
            self._trigger_queue.append(StackItem(
                obj_id=None, controller=seat, name=f"{obj.name} (ETB)", kind="triggered",
                effects=obj.spec.on_etb, source_id=obj.id,
                extra={"target_specs": obj.spec.etb_targets},
            ))

    # ------------------------------------------------------------------ mana

    def _mana_sources(self, seat: int) -> list[tuple[GameObject, int, tuple[str, ...]]]:
        """Untapped permanents that can be tapped for mana right now."""
        found = []
        for obj in self.state.zone_objects(seat, "battlefield"):
            if obj.tapped:
                continue
            if obj.spec.is_creature and obj.summoning_sick:
                continue  # a {T} cost needs the creature to have been around
            for index, ability in enumerate(obj.spec.abilities):
                if not (ability.is_mana_ability and ability.tap_cost):
                    continue
                if ability.mana_cost.mana_value:
                    continue  # filter lands would need a cost solve of their own
                symbols = tuple(s for e in ability.effects if isinstance(e, AddMana)
                                for s in e.symbols)
                if symbols:
                    found.append((obj, index, symbols))
        return found

    def _solve_payment(self, seat: int, cost: ManaCost):
        """Find a set of sources to tap that pays ``cost``, or None.

        Colored pips are matched first by search, because a source that can only
        make one needed color is forced; the generic remainder is then filled
        from whatever is left, where any mana is interchangeable. Lands are
        preferred over creatures, since tapping a creature also costs an attack.
        """
        pool = ManaPool(self.state.players[seat].pool)
        sources = self._mana_sources(seat)
        order = sorted(range(len(sources)),
                       key=lambda i: (sources[i][0].spec.is_creature, len(sources[i][2])))
        pips: list[str] = []
        for symbol, count in cost.pips:
            pips.extend([symbol] * count)

        used: set[int] = set()
        floating = Counter(pool)

        def pay_pips(index: int) -> bool:
            if index == len(pips):
                return True
            symbol = pips[index]
            if floating[symbol] > 0:
                floating[symbol] -= 1
                if pay_pips(index + 1):
                    return True
                floating[symbol] += 1
            for i in order:
                if i in used:
                    continue
                _, _, symbols = sources[i]
                if symbol not in symbols:
                    continue
                used.add(i)
                spare = Counter(symbols)
                spare[symbol] -= 1
                floating.update(spare)
                if pay_pips(index + 1):
                    return True
                floating.subtract(spare)
                used.discard(i)
            return False

        if not pay_pips(0):
            return None

        owed = cost.generic
        spend_floating: Counter[str] = Counter()
        for symbol in sorted(MANA_SYMBOLS, key=lambda s: -floating[s]):
            if owed <= 0:
                break
            take = min(owed, floating[symbol])
            if take:
                spend_floating[symbol] += take
                floating[symbol] -= take
                owed -= take
        for i in order:
            if owed <= 0:
                break
            if i in used:
                continue
            used.add(i)
            owed -= len(sources[i][2])
        if owed > 0:
            return None
        return [(sources[i][0], sources[i][1]) for i in sorted(used)]

    def can_pay(self, seat: int, cost: ManaCost) -> bool:
        return self._solve_payment(seat, cost) is not None

    def _pay_cost(self, seat: int, cost: ManaCost) -> None:
        plan = self._solve_payment(seat, cost)
        if plan is None:
            raise IllegalAction(f"cannot pay {cost}")
        for obj, index in plan:
            obj.tapped = True
            ability = obj.spec.abilities[index]
            for effect in ability.effects:
                effect.resolve(self, Context(controller=seat, source_id=obj.id,
                                             source_name=obj.name))
        self.state.players[seat].pool.pay(cost)

    # -------------------------------------------------------------- targeting

    def legal_targets(self, spec: TargetSpec, controller: int) -> list[Target]:
        selector = spec.selector
        found: list[Target] = []
        if selector in ("any_target", "player", "opponent", "any_player"):
            seats = [controller, 1 - controller] if selector in ("any_target", "player",
                                                                 "any_player") else [1 - controller]
            found.extend(Target("player", s) for s in seats)
        if selector in ("any_target", "creature", "creature_you_control",
                        "creature_you_dont_control", "permanent", "nonland_permanent",
                        "tapped_creature", "creature_power_4_or_greater",
                        "attacking_or_blocking_creature"):
            for obj in self.state.objects.values():
                if obj.zone != "battlefield":
                    continue
                if selector == "permanent":
                    pass
                elif selector == "nonland_permanent":
                    if obj.spec.is_land:
                        continue
                else:
                    if not obj.spec.is_creature:
                        continue
                    if selector == "creature_you_control" and obj.controller != controller:
                        continue
                    if selector == "creature_you_dont_control" and obj.controller == controller:
                        continue
                    if selector == "tapped_creature" and not obj.tapped:
                        continue
                    if selector == "creature_power_4_or_greater" and self.power_of(obj) < 4:
                        continue
                    if selector == "attacking_or_blocking_creature" and not (
                            obj.attacking or obj.blocking is not None):
                        continue
                found.append(Target("object", obj.id))
        if selector == "spell":
            found.extend(Target("object", item.obj_id) for item in self.state.stack
                         if item.kind == "spell" and item.obj_id is not None)
        return found

    def _target_combinations(self, specs: tuple[TargetSpec, ...],
                             controller: int) -> list[tuple[Target, ...]]:
        if not specs:
            return [()]
        options = [self.legal_targets(spec, controller) for spec in specs]
        if any(not opt for opt in options):
            return []
        combos = []
        for combo in itertools.product(*options):
            if len({(t.kind, t.id) for t in combo}) != len(combo):
                continue  # "target X and target Y" must be different objects
            combos.append(combo)
            if len(combos) >= TARGET_COMBINATION_CAP:
                break
        return combos

    def _target_still_legal(self, item: StackItem) -> bool:
        specs = item.extra.get("target_specs") or ()
        if not item.targets:
            return True
        for spec, target in zip(specs, item.targets, strict=False):
            if target not in self.legal_targets(spec, item.controller):
                return False
        return True

    # ---------------------------------------------------------- legal actions

    def legal_actions(self) -> list[act.Action]:
        if self.state.over:
            return []
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
            return [act.ChooseTargets(combo)
                    for combo in self._target_combinations(specs, trigger.controller)]
        if pending == "attackers":
            return self._attacker_options(seat)
        if pending == "blockers":
            return self._blocker_options(seat)
        return self._priority_options(seat)

    def _priority_options(self, seat: int) -> list[act.Action]:
        options: list[act.Action] = [act.Pass()]
        player = self.state.players[seat]
        sorcery_speed = (
            not self.state.stack
            and self.state.active == seat
            and self.state.step in (Step.PRECOMBAT_MAIN, Step.POSTCOMBAT_MAIN)
        )
        for obj in self.state.zone_objects(seat, "hand"):
            spec = obj.spec
            if spec.is_land:
                if sorcery_speed and player.lands_played < 1:
                    options.append(act.PlayLand(obj.id))
                continue
            instant_speed = CardType.INSTANT in spec.types
            if not instant_speed and not sorcery_speed:
                continue
            if not self.can_pay(seat, spec.cost):
                continue
            for combo in self._target_combinations(spec.targets, seat):
                options.append(act.CastSpell(obj.id, combo))
        for obj in self.state.zone_objects(seat, "battlefield"):
            for index, ability in enumerate(obj.spec.abilities):
                if ability.is_mana_ability:
                    continue  # the engine taps for mana; see docs/DESIGN.md
                if ability.sorcery_speed and not sorcery_speed:
                    continue
                if ability.tap_cost and (obj.tapped or
                                         (obj.spec.is_creature and obj.summoning_sick)):
                    continue
                if not self.can_pay(seat, ability.mana_cost):
                    continue
                for combo in self._target_combinations(ability.targets, seat):
                    options.append(act.ActivateAbility(obj.id, index, combo))
        return options

    def _attacker_options(self, seat: int) -> list[act.Action]:
        options: list[act.Action] = [act.FinishDeclaring()]
        for obj in self.creatures_of(seat):
            if obj.id in self.state.attackers_declared:
                continue
            if obj.tapped or obj.summoning_sick or obj.has(Keyword.DEFENDER):
                continue
            options.append(act.DeclareAttacker(obj.id))
        return options

    def _can_block(self, blocker: GameObject, attacker: GameObject) -> bool:
        if attacker.has(Keyword.FLYING) and not (blocker.has(Keyword.FLYING)
                                                 or blocker.has(Keyword.REACH)):
            return False
        return True

    def _blocker_options(self, seat: int) -> list[act.Action]:
        assigned = {b for b, _ in self.state.blocks_declared}
        options: list[act.Action] = []
        for blocker in self.creatures_of(seat):
            if blocker.tapped or blocker.id in assigned:
                continue
            for attacker_id in self.state.attackers_declared:
                attacker = self.state.obj(attacker_id)
                if attacker.zone != "battlefield":
                    continue
                if self._can_block(blocker, attacker):
                    options.append(act.DeclareBlocker(blocker.id, attacker_id))
        if self._blocks_are_legal():
            options.append(act.FinishDeclaring())
        return options

    def _blocks_are_legal(self) -> bool:
        """Menace is the only blocking restriction in this pool that a partial
        declaration can violate, so ``FinishDeclaring`` is withheld until the
        declaration as a whole would be legal."""
        counts = Counter(a for _, a in self.state.blocks_declared)
        for attacker_id in self.state.attackers_declared:
            attacker = self.state.obj(attacker_id)
            if attacker.has(Keyword.MENACE) and counts[attacker_id] == 1:
                return False
        return True

    # ----------------------------------------------------------------- apply

    def apply(self, action: act.Action) -> None:
        if self.state.over:
            raise IllegalAction("the game is over")
        if action not in self.legal_actions():
            raise IllegalAction(f"{action!r} is not legal right now")
        self.state.decisions += 1
        self._perform(action)
        self.advance()

    def _perform(self, action: act.Action) -> None:
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
            state.record(f"{player.name} mulligans to {MAX_HAND_SIZE - state.mulligan_counts[seat]}")
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
            player.hand.remove(action.card_id)
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
            player.hand.remove(action.card_id)
            player.lands_played += 1
            self._put_onto_battlefield(state.obj(action.card_id), seat)
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
        self.state.players[seat].hand.remove(obj.id)
        obj.zone = "stack"
        self._pay_cost(seat, obj.spec.cost)
        item = StackItem(
            obj_id=obj.id, controller=seat, name=obj.name, kind="spell",
            effects=obj.spec.on_resolve, targets=action.targets,
            is_permanent_spell=obj.spec.is_permanent, source_id=obj.id,
            extra={"target_specs": obj.spec.targets},
        )
        self.state.stack.append(item)
        target_note = ""
        if action.targets:
            target_note = " targeting " + ", ".join(self.describe_target(t) for t in action.targets)
        self.state.record(f"{self.state.players[seat].name} casts {obj.name}{target_note}")
        self.state.passes = 0
        self.state.priority = seat

    def _activate(self, seat: int, action: act.ActivateAbility) -> None:
        obj = self.state.obj(action.source_id)
        ability: ActivatedAbility = obj.spec.abilities[action.index]
        if ability.tap_cost:
            obj.tapped = True
        self._pay_cost(seat, ability.mana_cost)
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
        if not self._target_still_legal(item):
            self.state.record(f"{item.name} is countered on resolution (no legal targets)")
            if item.obj_id is not None:
                self.move_to_zone(item.obj_id, "graveyard")
            return
        if item.is_permanent_spell and item.obj_id is not None:
            self._put_onto_battlefield(self.state.obj(item.obj_id), item.controller)
            return
        ctx = Context(controller=item.controller, targets=tuple(item.targets),
                      source_id=item.source_id, source_name=item.name)
        for effect in item.effects:
            effect.resolve(self, ctx)
        self.state.record(f"{item.name} resolves")
        if item.kind == "spell" and item.obj_id is not None:
            self.move_to_zone(item.obj_id, "graveyard")

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

    def _begin_step(self) -> None:
        state = self.state
        step = state.step
        if step is Step.UNTAP:
            if state.turn > self.max_turns:
                state.over = True
                state.result_reason = f"draw: turn limit of {self.max_turns} reached"
                return
            player = state.players[state.active]
            player.lands_played = 0
            for obj in self.state.zone_objects(state.active, "battlefield"):
                obj.tapped = False
                obj.summoning_sick = False
            state.record(f"--- {player.name}'s turn {state.turn} ---")
            self._next_step()
        elif step is Step.DRAW:
            first_turn_on_the_play = state.turn == 1 and state.active == state.on_the_play
            if not first_turn_on_the_play:
                self.draw_card(state.active)
        elif step is Step.DECLARE_ATTACKERS:
            state.attackers_declared = []
            state.blocks_declared = []
            state.blockers_done = False
            state.pending = "attackers"
            state.decision_player = state.active
        elif step is Step.COMBAT_DAMAGE:
            self._combat_damage()
        elif step is Step.END_COMBAT:
            for obj in self.state.objects.values():
                obj.clear_combat()
        elif step is Step.CLEANUP:
            for obj in self.state.objects.values():
                obj.reset_end_of_turn()
            if len(state.players[state.active].hand) > MAX_HAND_SIZE:
                state.pending = "discard"
                state.decision_player = state.active
            else:
                self._next_step()

    def _finish_attackers(self) -> None:
        state = self.state
        for attacker_id in state.attackers_declared:
            obj = state.obj(attacker_id)
            obj.attacking = True
            if not obj.has(Keyword.VIGILANCE):
                obj.tapped = True
        if state.attackers_declared:
            names = ", ".join(state.obj(i).name for i in state.attackers_declared)
            state.record(f"{state.players[state.active].name} attacks with {names}")
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
                    and (o.has(Keyword.FIRST_STRIKE) or o.has(Keyword.DOUBLE_STRIKE))]
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
            has_fs = obj.has(Keyword.FIRST_STRIKE)
            has_ds = obj.has(Keyword.DOUBLE_STRIKE)
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
            lethal_needed = 1 if attacker.has(Keyword.DEATHTOUCH) else None
            for blocker in blockers:
                if remaining <= 0:
                    break
                need = lethal_needed or max(1, self.toughness_of(blocker) - blocker.damage)
                give = min(remaining, need)
                assignments.append((Target("object", blocker.id), give, attacker.id))
                remaining -= give
            if remaining > 0:
                if attacker.has(Keyword.TRAMPLE):
                    assignments.append((Target("player", defender), remaining, attacker.id))
                elif blockers:
                    assignments.append((Target("object", blockers[-1].id), remaining, attacker.id))

        for blocker in [o for o in self.all_creatures() if o.blocking is not None]:
            if not strikes_now(blocker):
                continue
            attacker = self.object_by_id(blocker.blocking)
            if attacker is None or attacker.zone != "battlefield":
                continue
            assignments.append((Target("object", attacker.id), self.power_of(blocker), blocker.id))

        for target, amount, source_id in assignments:
            self.deal_damage(target, amount, source_id=source_id)

    # ------------------------------------------------------ state-based actions

    def _state_based_actions(self) -> None:
        state = self.state
        changed = True
        while changed and not state.over:
            changed = False
            for obj in list(state.objects.values()):
                if obj.zone != "battlefield" or not obj.spec.is_creature:
                    continue
                toughness = self.toughness_of(obj)
                if toughness <= 0:
                    self.state.record(f"{obj.name} is put into the graveyard (0 toughness)")
                    self.move_to_zone(obj.id, "graveyard")
                    changed = True
                elif obj.deathtouched and obj.damage > 0:
                    self.state.record(f"{obj.name} is destroyed (deathtouch)")
                    self.move_to_zone(obj.id, "graveyard")
                    changed = True
                elif obj.damage >= toughness:
                    self.state.record(f"{obj.name} is destroyed (lethal damage)")
                    self.move_to_zone(obj.id, "graveyard")
                    changed = True
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

    # -------------------------------------------------------------- advancing

    def advance(self) -> None:
        """Run the game forward until a player faces a genuine choice.

        A player with exactly one legal action has no decision to make, so the
        engine takes it. This keeps agent transcripts free of thousands of
        forced passes, which matters enormously when each decision costs an LLM
        call.
        """
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
                return
            if not options:  # pragma: no cover - defensive
                raise RuntimeError(f"no legal actions in state {self.state.pending}")
            self.state.decisions += 0  # forced actions are not decisions
            self._perform(options[0])

    def _enter_pending_phase(self) -> bool:
        """Set up whatever decision is outstanding. Returns True if the state
        changed and the caller should re-examine it."""
        state = self.state
        if state.pending in ("mulligan", "bottom"):
            return self._advance_mulligans()
        if state.pending == "attackers":
            return False
        if state.pending == "blockers":
            return False
        if state.pending == "trigger_targets":
            return False
        if self._trigger_queue:
            trigger = self._trigger_queue.pop(0)
            specs = trigger.extra.get("target_specs") or ()
            if specs:
                if not self._target_combinations(specs, trigger.controller):
                    state.record(f"{trigger.name} is removed (no legal targets)")
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

    def _finish_turn_discard(self) -> None:
        state = self.state
        player = state.players[state.active]
        if len(player.hand) > MAX_HAND_SIZE:
            state.pending = "discard"
            state.decision_player = state.active

    def describe_target(self, target: Target) -> str:
        if target.kind == "player":
            return self.state.players[target.id].name
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
