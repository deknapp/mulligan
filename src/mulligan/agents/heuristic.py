"""A hand-coded Magic player: the baseline every other agent is measured against.

It knows general Limited strategy and nothing about any particular set. Every
legal action gets a score and the highest one is played, so the whole policy is
one function, ``score``. That shape is deliberate: a learned agent keeps this
score as a prior and learns only a correction on top of it, which is why it can
learn a new set from self-play on a laptop instead of from scratch.

Principles encoded here, roughly in the order a Limited player learns them:

* Keep hands with a workable number of lands; bottom the worst cards.
* Play a land every turn, choosing the color the hand needs most.
* Spend mana: cast the most expensive useful spell first, and repeat.
* Point removal at the most valuable creature it actually deals with; never at
  your own things. Burn goes face only when that finishes the game.
* Attack when no blocker can eat the attacker for free, or when it is lethal;
  hold back enough to survive the crack-back.
* Block when the block wins or trades up; chump only to stay alive.
* Hold tricks and counters for when they change an outcome.

Only the ``PlayerView`` is used, so it plays under exactly the information rules
a human would.
"""

from __future__ import annotations

from collections import Counter

from ..engine import actions as act
from ..engine import effects as fx
from ..engine.card import CardSpec
from ..engine.types import CardType, Keyword, Step
from ..engine.view import PermanentView, PlayerView
from .base import Agent

KEYWORD_VALUE = {
    Keyword.FLYING: 1.5, Keyword.DEATHTOUCH: 1.5, Keyword.LIFELINK: 1.0,
    Keyword.FIRST_STRIKE: 1.0, Keyword.MENACE: 0.8, Keyword.TRAMPLE: 0.5,
    Keyword.VIGILANCE: 0.5, Keyword.HASTE: 0.5, Keyword.REACH: 0.3,
}


def creature_value(power: int, toughness: int, keywords) -> float:
    value = 1.5 * power + toughness
    for kw in keywords:
        value += KEYWORD_VALUE.get(kw, 0.0)
        if kw == Keyword.DOUBLE_STRIKE:
            value += 1.5 * power
        if kw == Keyword.DEFENDER:
            value -= 1.5 * power
    return max(value, 0.5)


def permanent_value(perm: PermanentView) -> float:
    if perm.is_creature:
        return creature_value(perm.power, perm.toughness, perm.keywords)
    if perm.is_land:
        return 1.0
    return 1.0 + perm.spec.cost.mana_value * 0.5


def spec_value(spec: CardSpec) -> float:
    if spec.is_creature:
        return creature_value(spec.power or 0, spec.toughness or 0, spec.keywords)
    return 1.0 + spec.cost.mana_value * 0.6


# ------------------------------------------------------------------- combat


def _lethal_to(damage: int, victim: PermanentView, source_deathtouch: bool) -> bool:
    return damage > 0 and (source_deathtouch or damage >= victim.toughness - victim.damage)


def combat_outcome(attacker: PermanentView, blocker: PermanentView) -> tuple[bool, bool]:
    """(attacker dies, blocker dies) if ``blocker`` alone blocks ``attacker``."""
    a_fs = attacker.has(Keyword.FIRST_STRIKE) or attacker.has(Keyword.DOUBLE_STRIKE)
    b_fs = blocker.has(Keyword.FIRST_STRIKE) or blocker.has(Keyword.DOUBLE_STRIKE)
    a_kills = _lethal_to(attacker.power, blocker, attacker.has(Keyword.DEATHTOUCH))
    b_kills = _lethal_to(blocker.power, attacker, blocker.has(Keyword.DEATHTOUCH))
    if a_fs and not b_fs and a_kills:
        return False, True
    if b_fs and not a_fs and b_kills:
        return True, False
    return b_kills, a_kills


def can_block(blocker: PermanentView, attacker: PermanentView) -> bool:
    if not blocker.can_block:
        return False
    if attacker.has(Keyword.FLYING):
        return blocker.has(Keyword.FLYING) or blocker.has(Keyword.REACH)
    return True


# ------------------------------------------------------------------ effects

HARMFUL = (fx.DestroyTarget, fx.ExileTarget, fx.ReturnTargetToHand, fx.TapTarget,
           fx.DealDamage, fx.Fight)


def _removes(effect, perm: PermanentView) -> bool:
    """Whether ``effect`` aimed at ``perm`` takes it off the battlefield."""
    if isinstance(effect, (fx.DestroyTarget, fx.ExileTarget, fx.ReturnTargetToHand)):
        return True
    if isinstance(effect, fx.DealDamage):
        return perm.is_creature and effect.amount >= perm.toughness - perm.damage
    return False


class HeuristicAgent(Agent):
    """Scores every legal action with general Limited heuristics."""

    def __init__(self, name: str = "heuristic"):
        self.name = name

    def choose(self, view: PlayerView, options: list[act.Action]) -> act.Action:
        if view.pending == "attackers":
            self._plan = self._plan_attacks(view)
        elif view.pending == "blockers":
            self._plan = self._plan_blocks(view)
        return max(options, key=lambda a: self.score(view, a))

    # The whole policy. A learned agent adds a correction to this number.
    def score(self, view: PlayerView, action: act.Action) -> float:
        if isinstance(action, act.KeepHand):
            return 1.0 if self._keepable(view) else -1.0
        if isinstance(action, act.Mulligan):
            return 0.0
        if isinstance(action, (act.PutOnBottom, act.Discard)):
            return -self._keep_value(view, action.card_id)
        if isinstance(action, act.PlayLand):
            return 100.0 + self._land_need(view, action.card_id)
        if isinstance(action, act.CastSpell):
            return self._score_cast(view, action)
        if isinstance(action, act.ActivateAbility):
            return self._score_activation(view, action)
        if isinstance(action, act.ChooseTargets):
            return self._score_trigger_targets(view, action)
        if isinstance(action, act.DeclareAttacker):
            return 1.0 if action.creature_id in self._plan else -1.0
        if isinstance(action, act.DeclareBlocker):
            pair = (action.blocker_id, action.attacker_id)
            return 1.0 if pair in self._plan else -1.0
        if isinstance(action, act.FinishDeclaring):
            return 0.0
        return 0.0  # Pass

    # ------------------------------------------------------------ mulligans

    def _keepable(self, view: PlayerView) -> bool:
        hand = view.hand()
        lands = sum(1 for c in hand if c.spec.is_land)
        size = len(hand) - view.mulligans(view.seat)
        if view.mulligans(view.seat) >= 2:
            return True
        low, high = (2, 5) if size >= 7 else (1, 4)
        return low <= lands <= high

    def _keep_value(self, view: PlayerView, card_id: int) -> float:
        hand = view.hand()
        card = next(c for c in hand if c.id == card_id)
        lands_in_hand = sum(1 for c in hand if c.spec.is_land)
        lands_out = len(view.lands(view.seat))
        if card.spec.is_land:
            # Lands are worth a lot until there are enough of them.
            return 8.0 if lands_in_hand + lands_out <= 4 else 1.0
        castable_soon = card.spec.cost.mana_value <= lands_in_hand + lands_out + 1
        return spec_value(card.spec) + (2.0 if castable_soon else 0.0)

    def _land_need(self, view: PlayerView, card_id: int) -> float:
        """Prefer the land that produces the color the hand is shortest on."""
        land = next(c for c in view.hand() if c.id == card_id)
        produced = {s for ab in land.spec.abilities for e in ab.effects
                    if isinstance(e, fx.AddMana) for s in e.symbols}
        have: Counter[str] = Counter()
        for perm in view.lands(view.seat):
            for ab in perm.spec.abilities:
                for e in ab.effects:
                    if isinstance(e, fx.AddMana):
                        have.update(e.symbols)
        want: Counter[str] = Counter()
        for card in view.hand():
            for sym, n in card.spec.cost.pips:
                want[sym] += n
        need = sum(max(0, want[s] - have[s]) for s in produced)
        return need - (5.0 if land.spec.enters_tapped else 0.0)

    # --------------------------------------------------------------- casting

    def _score_cast(self, view: PlayerView, action: act.CastSpell) -> float:
        card = view.card(action.card_id)
        spec = card.spec
        mv = spec.cost.mana_value
        instant = CardType.INSTANT in spec.types
        main_phase = view.is_my_turn and view.step in (Step.PRECOMBAT_MAIN,
                                                       Step.POSTCOMBAT_MAIN)
        if spec.is_permanent and not spec.targets:
            if not main_phase and not instant:
                return -1.0
            return 10.0 + spec_value(spec) + mv
        value = self._effects_value(view, spec.on_resolve, action.targets)
        if value <= 0:
            return -1.0
        if self._is_trick(spec) and not self._trick_now(view, spec, action.targets):
            return -1.0
        # Sorceries and removal get used in the main phase; instants on the
        # opponent's turn are only worth it when they answer something.
        return 10.0 + value + 0.5 * mv

    def _is_trick(self, spec: CardSpec) -> bool:
        return any(isinstance(e, fx.Pump) and not e.self_target for e in spec.on_resolve)

    def _trick_now(self, view: PlayerView, spec: CardSpec, targets) -> bool:
        """A pump spell is worth casting only when it wins a combat right now."""
        if view.step != Step.DECLARE_BLOCKERS or not view.blocks():
            return False
        pump = next(e for e in spec.on_resolve if isinstance(e, fx.Pump))
        for target in targets:
            perm = view.permanent(target.id) if target.kind == "object" else None
            if perm is None or perm.controller != view.seat:
                continue
            for blocker_id, attacker_id in view.blocks():
                other_id = blocker_id if perm.id == attacker_id else (
                    attacker_id if perm.id == blocker_id else None)
                if other_id is None:
                    continue
                other = view.permanent(other_id)
                if other is None:
                    continue
                dies_now = _lethal_to(other.power, perm, other.has(Keyword.DEATHTOUCH))
                survives_after = other.power < perm.toughness + pump.toughness - perm.damage
                kills_after = perm.power + pump.power >= other.toughness - other.damage
                if (dies_now and survives_after) or kills_after:
                    return True
        return False

    def _effects_value(self, view: PlayerView, effects, targets) -> float:
        """What resolving ``effects`` against ``targets`` is worth to the viewer."""
        me, opp = view.seat, view.opponent
        total = 0.0
        for effect in effects:
            if isinstance(effect, fx.CounterTargetSpell):
                for t in targets:
                    item = next((s for s in view.stack() if s.obj_id == t.id), None)
                    if item is None or item.controller == me:
                        return -10.0
                    total += 2.0 + (spec_value(item.spec) if item.spec else 2.0)
                continue
            if isinstance(effect, fx.Fight) and len(targets) == 2:
                mine = view.permanent(targets[0].id)
                theirs = view.permanent(targets[1].id)
                if mine is None or theirs is None:
                    continue
                if mine.power >= theirs.toughness - theirs.damage:
                    total += permanent_value(theirs)
                if theirs.power >= mine.toughness - mine.damage:
                    total -= permanent_value(mine)
                continue
            if isinstance(effect, fx.DealDamage) and effect.scope != "targets":
                total += self._sweep_value(view, effect)
                continue
            if isinstance(effect, fx.DestroyAll):
                mine = sum(permanent_value(c) for c in view.creatures(me))
                theirs = sum(permanent_value(c) for c in view.creatures(opp))
                total += theirs - (0 if effect.only_opponents else mine) - 2.0
                continue
            if isinstance(effect, HARMFUL):
                for t in targets:
                    if t.kind == "player":
                        if t.id == me:
                            return -10.0
                        amount = getattr(effect, "amount", 0)
                        total += 100.0 if amount >= view.life(opp) else 0.4 * amount
                        continue
                    perm = view.permanent(t.id)
                    if perm is None:
                        continue
                    if perm.controller == me:
                        return -10.0
                    if _removes(effect, perm):
                        bonus = 0.5 if isinstance(effect, fx.ReturnTargetToHand) else 1.0
                        total += bonus * permanent_value(perm)
                    elif isinstance(effect, fx.TapTarget):
                        total += 0.5
                    else:
                        total += 0.2
                continue
            if isinstance(effect, fx.Pump):
                for t in targets:
                    perm = view.permanent(t.id) if t.kind == "object" else None
                    if perm is None or perm.controller != me:
                        return -10.0
                    total += 1.0 + effect.power + effect.toughness
                if effect.self_target:
                    total += 0.5
                continue
            if isinstance(effect, fx.DrawCards):
                total += 2.0 * effect.count
            elif isinstance(effect, fx.GainLife):
                total += 0.3 * effect.amount
            elif isinstance(effect, fx.LoseLife):
                if effect.who == "each_opponent":
                    total += 100.0 if effect.amount >= view.life(opp) else 0.4 * effect.amount
                else:
                    for t in targets or ():
                        sign = -1 if (t.kind == "player" and t.id == me) else 1
                        total += sign * 0.3 * effect.amount
                    if effect.who == "you":
                        total -= 0.3 * effect.amount
            elif isinstance(effect, fx.CreateToken):
                total += effect.count * creature_value(effect.power, effect.toughness,
                                                       effect.keywords)
            else:
                total += 1.0  # an effect this heuristic does not model: assume mildly good
        return total

    def _sweep_value(self, view: PlayerView, effect: fx.DealDamage) -> float:
        def killed(seat: int) -> float:
            return sum(permanent_value(c) for c in view.creatures(seat)
                       if effect.amount >= c.toughness - c.damage)
        value = killed(view.opponent)
        if effect.scope == "all_creatures":
            value -= killed(view.seat)
        if effect.scope == "each_opponent":
            value = 100.0 if effect.amount >= view.life(view.opponent) else 0.4 * effect.amount
        return value - 1.0

    def _score_activation(self, view: PlayerView, action: act.ActivateAbility) -> float:
        source = view.permanent(action.source_id)
        ability = source.spec.abilities[action.index]
        value = self._effects_value(view, ability.effects, action.targets)
        if any(isinstance(e, fx.Pump) and e.self_target for e in ability.effects):
            # Firebreathing: only while it is attacking unblocked or in a fight.
            return 1.0 if (source.attacking and view.step == Step.DECLARE_BLOCKERS) else -1.0
        return value - 0.5 if value > 0 else -1.0

    def _score_trigger_targets(self, view: PlayerView, action: act.ChooseTargets) -> float:
        # The trigger's effects are not in the view directly; score the targets
        # by whether they belong to us, using the triggering card's spec when a
        # permanent's ETB is what is being targeted.
        score = 0.0
        for t in action.targets:
            if t.kind == "player":
                score += 1.0 if t.id == view.opponent else -1.0
                continue
            perm = view.permanent(t.id)
            if perm is None:
                continue
            sign = -1.0 if perm.controller == view.seat else 1.0
            score += sign * permanent_value(perm)
        return score * self._trigger_polarity(view)

    def _trigger_polarity(self, view: PlayerView) -> float:
        pending = view.pending_trigger()
        if pending is None:
            return 1.0
        helpful = any(isinstance(e, fx.Pump) for e in pending.effects)
        return -1.0 if helpful else 1.0

    # ---------------------------------------------------------------- combat

    def _plan_attacks(self, view: PlayerView) -> set[int]:
        me, opp = view.seat, view.opponent
        mine = [c for c in view.creatures(me) if not c.tapped and not c.summoning_sick
                and not c.has(Keyword.DEFENDER)]
        blockers = [c for c in view.creatures(opp) if c.can_block]
        opp_life = view.life(opp)

        def blockable_by(att: PermanentView) -> list[PermanentView]:
            found = [b for b in blockers if can_block(b, att)]
            return found if not att.has(Keyword.MENACE) or len(found) >= 2 else []

        # Alpha strike when the unblockable damage alone is lethal, or when the
        # total exceeds what the blockers can soak.
        evasive = sum(c.power for c in mine if not blockable_by(c))
        if evasive >= opp_life:
            return {c.id for c in mine}
        if mine and len(blockers) < len(mine):
            by_power = sorted((c.power for c in mine), reverse=True)
            if sum(by_power[len(blockers):]) >= opp_life:
                return {c.id for c in mine}

        plan: set[int] = set()
        for att in mine:
            options = blockable_by(att)
            if not options:
                plan.add(att.id)
                continue
            safe = True
            for blk in options:
                att_dies, blk_dies = combat_outcome(att, blk)
                if att_dies and not blk_dies:
                    safe = False
                    break
                if att_dies and blk_dies and permanent_value(blk) < permanent_value(att) - 1:
                    safe = False
                    break
            if safe:
                plan.add(att.id)

        # Keep back enough to survive the crack-back.
        threat = sum(c.power for c in view.creatures(opp))
        defenders = [c for c in mine if c.id not in plan or c.has(Keyword.VIGILANCE)]
        defenders += [c for c in view.creatures(me) if c not in mine and not c.tapped]
        for att in sorted((c for c in mine if c.id in plan), key=lambda c: c.toughness,
                          reverse=True):
            if threat - self._soak(defenders) < view.life(me):
                break
            plan.discard(att.id)
            defenders.append(att)
        return plan

    @staticmethod
    def _soak(defenders: list[PermanentView]) -> int:
        """A crude upper bound on attacking power the defenders can absorb."""
        return sum(max(d.toughness, 2) for d in defenders)

    def _plan_blocks(self, view: PlayerView) -> set[tuple[int, int]]:
        me = view.seat
        attackers = sorted(view.attackers(), key=lambda a: a.power, reverse=True)
        declared = set(view.blocks())
        used = {b for b, _ in declared}
        pool = [c for c in view.creatures(me) if c.can_block and c.id not in used]
        plan: set[tuple[int, int]] = set(declared)
        blocked: set[int] = {a for _, a in declared}

        for att in attackers:
            if att.id in blocked or att.has(Keyword.MENACE):
                continue
            candidates = [b for b in pool if can_block(b, att)]
            good = [b for b in candidates if combat_outcome(att, b) == (True, False)]
            trade = [b for b in candidates if combat_outcome(att, b) == (True, True)
                     and permanent_value(b) <= permanent_value(att)]
            safe = [b for b in candidates if combat_outcome(att, b) == (False, False)]
            choice = None
            for group in (good, trade, safe):
                if group:
                    choice = min(group, key=permanent_value)
                    break
            if choice is not None:
                plan.add((choice.id, att.id))
                blocked.add(att.id)
                pool.remove(choice)

        def unblocked_damage() -> int:
            return sum(a.power for a in attackers if a.id not in blocked)

        # Chump the biggest hits until the damage is survivable.
        for att in attackers:
            if unblocked_damage() < view.life(me):
                break
            if att.id in blocked:
                continue
            candidates = [b for b in pool if can_block(b, att)]
            need = 2 if att.has(Keyword.MENACE) else 1
            if len(candidates) < need:
                continue
            chosen = sorted(candidates, key=permanent_value)[:need]
            for b in chosen:
                plan.add((b.id, att.id))
                pool.remove(b)
            blocked.add(att.id)
        return plan

    _plan: set = set()
