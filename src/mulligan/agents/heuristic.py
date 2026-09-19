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
    if perm.is_planeswalker and not perm.is_creature:
        return 2.0 + 0.8 * perm.loyalty
    if perm.is_creature:
        return creature_value(perm.power, perm.toughness, perm.keywords)
    if perm.is_land:
        return 1.0
    return 1.0 + perm.spec.cost.mana_value * 0.5


def _produces(spec: CardSpec) -> set[str]:
    colors: set[str] = set()
    for ability in spec.abilities:
        for effect in ability.effects:
            if isinstance(effect, fx.AddMana):
                for unit in effect.symbols:
                    colors |= set("WUBRG") if unit == "*" else set(unit.split("/"))
    return colors


def spec_value(spec: CardSpec) -> float:
    if spec.is_creature:
        # "*" stats (e.g. "equal to the lands you control"): assume a midgame 3.
        power = spec.power if spec.power is not None else (3 if spec.power_expr else 0)
        tough = spec.toughness if spec.toughness is not None else (
            3 if spec.toughness_expr else 0)
        return creature_value(power, tough, spec.keywords)
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


def can_block(view: PlayerView, blocker: PermanentView, attacker: PermanentView) -> bool:
    return view.could_block(blocker, attacker)


# ------------------------------------------------------------------ effects

REMOVAL = (fx.Destroy, fx.Exile, fx.ReturnToHand, fx.PutOnLibrary, fx.ShuffleIntoLibrary)
HELPFUL_TO_OBJECT = (fx.AddCounters, fx.Attach)
CARD_ADVANTAGE = {fx.Recruit: 2.0, fx.Scry: 0.4, fx.SearchLibrary: 1.2, fx.LookAtTop: 1.5,
                  fx.Impulse: 1.5, fx.AdditionalLand: 0.3}


def _touched(ref: str, targets) -> list[int]:
    """Indexes of ``targets`` that an effect's ``to`` reference touches."""
    if ref == "target":
        return list(range(len(targets)))
    if ref.startswith("target") and ref[6:].isdigit():
        index = int(ref[6:])
        return [index] if index < len(targets) else []
    return []


def _side(ref: str) -> str:
    """For an ``all:`` reference: whose permanents it hits."""
    if "theirs" in ref:
        return "theirs"
    if "yours" in ref:
        return "yours"
    return "both"


class HeuristicAgent(Agent):
    """Scores every legal action with general Limited heuristics."""

    def __init__(self, name: str = "heuristic", card_values: dict[str, float] | None = None):
        """``card_values`` is an optional per-card adjustment learned for a set
        (see ``learn_values``): points added to the general value of a card,
        which steers casting order, removal targets, trades and blocks."""
        self.name = name
        self.card_values = card_values or {}

    def _pv(self, perm: PermanentView) -> float:
        return max(0.5, permanent_value(perm) + self.card_values.get(perm.name, 0.0))

    def _sv(self, spec: CardSpec) -> float:
        return max(0.5, spec_value(spec) + self.card_values.get(spec.name, 0.0))

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
            trigger = view.pending_trigger()
            effects = trigger.effects if trigger is not None else ()
            if trigger is not None and action.mode >= 0:
                effects = trigger.modes[action.mode].effects
            return self._effects_value(view, effects, action.targets,
                                       source_id=trigger.source_id if trigger else None)
        if isinstance(action, act.DeclareAttacker):
            target = self._plan.get(action.creature_id, None) if isinstance(
                self._plan, dict) else None
            return 1.0 if target is not None and target == action.target else -1.0
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
        return self._sv(card.spec) + (2.0 if castable_soon else 0.0)

    def _land_need(self, view: PlayerView, card_id: int) -> float:
        """Prefer the land that produces the color the hand is shortest on."""
        land = next(c for c in view.hand() + view.exile(view.seat) if c.id == card_id)
        produced = _produces(land.spec)
        have: Counter[str] = Counter()
        for perm in view.lands(view.seat):
            have.update(_produces(perm.spec))
        want: Counter[str] = Counter()
        for card in view.hand():
            for sym, n in card.spec.cost.pips:
                for part in sym.split("/"):
                    want[part] += n
        need = sum(max(0, want[s] - have[s]) for s in produced)
        return need - (5.0 if land.spec.enters_tapped else 0.0)

    # --------------------------------------------------------------- casting

    def _score_cast(self, view: PlayerView, action: act.CastSpell) -> float:
        self._x = action.x
        card = view.card(action.card_id)
        spec = (card.spec.adventure if action.face == "adventure" else
                card.spec.prepare if action.face == "prepared" else card.spec)
        mv = spec.cost.mana_value
        main_phase = view.is_my_turn and view.step in (Step.PRECOMBAT_MAIN,
                                                       Step.POSTCOMBAT_MAIN)
        if action.mode >= 0:
            effects, targets = spec.modes[action.mode].effects, action.targets
        else:
            effects, targets = spec.on_resolve, action.targets
        bonus = 0.5 if action.kicked else 0.0
        if spec.is_permanent and action.face not in ("adventure", "prepared"):
            if not main_phase and CardType.CREATURE in spec.types:
                # Flash creatures: hold them for the opponent's end step.
                if not (view.step == Step.END_STEP and not view.is_my_turn):
                    return -1.0
            if spec.enchant is not None:
                aura = self._aura_value(view, spec, targets)
                return 10.0 + aura + mv if aura > 0 else -1.0
            etb = self._etb_value(view, spec)
            if etb < -5:
                return -1.0
            return (10.0 + self._sv(spec) + etb + mv + bonus + 1.2 * action.x
                    - self._extra_cost(view, spec, action))
        value = self._effects_value(view, effects, targets, source_id=action.card_id)
        value -= self._extra_cost(view, spec, action)
        if value <= 0:
            return -1.0
        if self._is_trick(effects) and not self._trick_now(view, effects, targets):
            return -1.0
        if action.face == "adventure":
            value += 2.0  # the creature half stays available: casting the adventure is free value
        return 10.0 + value + 0.5 * mv + bonus

    def _extra_cost(self, view: PlayerView, spec: CardSpec, action: act.CastSpell) -> float:
        """What an additional cost gives up: a sacrifice costs the cheapest
        permanent that could pay it (tokens and Treasure are cheap)."""
        if action.extra < 0 or action.extra >= len(spec.additional_costs):
            return 0.0
        cost = spec.additional_costs[action.extra]
        total = 1.8 * cost.discard + 0.3 * cost.life
        if cost.sacrifice:
            fodder = [p for p in view.battlefield(view.seat) if not p.is_land
                      and p.id != action.card_id]
            cheapest = min((0.5 if p.is_token and not p.is_creature else self._pv(p)
                            for p in fodder), default=5.0)
            total += 0.5 + cheapest
        return total

    def _etb_value(self, view: PlayerView, spec: CardSpec) -> float:
        """Untargeted ETB effects (targeted ones are valued when their targets
        are chosen)."""
        total = 0.0
        for trigger in spec.triggers:
            if trigger.when == "etb" and not trigger.targets:
                total += self._effects_value(view, trigger.effects, ())
        return total

    def _aura_value(self, view: PlayerView, spec: CardSpec, targets) -> float:
        perm = view.permanent(targets[0].id) if targets and targets[0].kind == "object" else None
        if perm is None:
            return -1.0
        buff = sum(self._static_amount(s.power) + self._static_amount(s.toughness)
                   + len(s.keywords) for s in spec.statics if s.affects == "enchanted")
        lockdown = any({"loses_abilities", "doesnt_untap", "cant_attack", "cant_block"}
                       & set(s.flags) for s in spec.statics if s.affects == "enchanted")
        if perm.controller == view.seat:
            return buff if buff > 0 and not lockdown else -1.0
        if lockdown or buff < 0:
            return self._pv(perm) * 0.8
        return -1.0

    @staticmethod
    def _static_amount(value) -> int:
        return value if isinstance(value, int) else 1

    def _is_trick(self, effects) -> bool:
        return any(isinstance(e, fx.Pump) and e.to.startswith("target")
                   and (not isinstance(e.power, int) or e.power >= 0) for e in effects)

    def _trick_now(self, view: PlayerView, effects, targets) -> bool:
        """A pump spell is worth casting only when it wins a combat right now."""
        if view.step != Step.DECLARE_BLOCKERS or not view.blocks():
            return False
        pump = next(e for e in effects if isinstance(e, fx.Pump))
        power = pump.power if isinstance(pump.power, int) else 2
        tough = pump.toughness if isinstance(pump.toughness, int) else 2
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
                survives_after = other.power < perm.toughness + tough - perm.damage
                kills_after = perm.power + power >= other.toughness - other.damage
                if (dies_now and survives_after) or kills_after:
                    return True
        return False

    def _effects_value(self, view: PlayerView, effects, targets,
                       source_id: int | None = None) -> float:
        """What resolving ``effects`` against ``targets`` is worth to the viewer.

        Every effect is classified as harmful or helpful to what it touches;
        pointing a harmful effect at your own permanent (or a helpful one at an
        opponent's) is heavily penalised, which is what keeps removal pointed
        the right way on any set without per-card rules.
        """
        me, opp = view.seat, view.opponent
        total = 0.0
        for effect in effects:
            if isinstance(effect, fx.If):
                total += 0.7 * self._effects_value(view, effect.then, targets, source_id)
                continue
            if isinstance(effect, fx.MayPay):
                total += 0.8 * self._effects_value(view, effect.then, targets, source_id)
                continue
            if (isinstance(effect, (fx.ReturnToHand, fx.ReturnToBattlefield))
                    and effect.to == "self" and source_id is not None):
                card = view.card(source_id)
                if card is not None and card.zone == "graveyard":
                    total += self._sv(card.spec)  # buying back your own card
                    continue
            if isinstance(effect, (fx.CounterSpell, fx.ReturnSpellToHand)):
                for t in targets:
                    if t.kind != "object":
                        continue
                    item = next((s for s in view.stack() if s.obj_id == t.id), None)
                    if item is None or item.controller == me:
                        return -10.0
                    worth = self._sv(item.spec) if item.spec else 2.0
                    if isinstance(effect, fx.CounterSpell) and effect.unless_pay:
                        worth *= 0.5
                    total += 2.0 + worth
                continue
            if isinstance(effect, fx.Fight):
                a = self._target_perm(view, effect.a, targets)
                b = self._target_perm(view, effect.b, targets)
                if a is None or b is None:
                    continue
                if a.controller != me or b.controller == me:
                    return -10.0
                if a.power >= b.toughness - b.damage:
                    total += self._pv(b)
                if not effect.one_sided and b.power >= a.toughness - a.damage:
                    total -= self._pv(a)
                continue
            to = getattr(effect, "to", "")
            if to.startswith("all:"):
                total += self._mass_value(view, effect, to)
                continue
            touched = _touched(to, targets) if to else []
            if isinstance(effect, (fx.DealDamage, fx.Pump, fx.Tap)) or isinstance(
                    effect, REMOVAL) or isinstance(effect, HELPFUL_TO_OBJECT) or isinstance(
                    effect, (fx.SetBasePT, fx.RemoveCounters)):
                for index in touched:
                    t = targets[index]
                    value = self._target_effect_value(view, effect, t)
                    if value <= -10:
                        return -10.0
                    total += value
                if not touched and to in ("self", "it"):
                    total += 0.5
                if isinstance(effect, fx.DealDamage) and to in ("each_opponent",):
                    amount = self._amount(effect.amount)
                    total += 100.0 if amount >= view.life(opp) else 0.4 * amount
                continue
            if isinstance(effect, fx.DrawCards):
                count = self._amount(effect.count)
                if effect.who in ("target_player", "target") and targets:
                    seat = next((t.id for t in targets if t.kind == "player"), me)
                    total += 2.0 * count if seat == me else -2.0 * count
                else:
                    total += 2.0 * count if effect.who == "you" else -1.0 * count
            elif isinstance(effect, fx.Loot):
                total += 0.8 * effect.draw
            elif type(effect) in CARD_ADVANTAGE:
                total += CARD_ADVANTAGE[type(effect)]
            elif isinstance(effect, fx.GainLife):
                total += 0.3 * self._amount(effect.amount) * self._who_sign(view, effect.who,
                                                                            targets)
            elif isinstance(effect, fx.LoseLife):
                amount = self._amount(effect.amount)
                sign = -self._who_sign(view, effect.who, targets)
                if sign > 0 and amount >= view.life(opp):
                    total += 100.0
                else:
                    total += sign * 0.35 * amount
            elif isinstance(effect, fx.Discard):
                total += 1.5 * self._amount(effect.count) * (-1 if effect.who == "you" else 1)
            elif isinstance(effect, fx.Mill):
                total += 0.1 * self._amount(effect.count)
            elif isinstance(effect, fx.Sacrifice):
                if effect.who == "you":
                    total -= 2.0
                else:
                    theirs = view.creatures(opp)
                    total += min((self._pv(c) for c in theirs), default=-3.0)
            elif isinstance(effect, fx.CreateToken):
                t = effect.token
                per = creature_value(t.power, t.toughness, t.keywords) if "Creature" in t.types \
                    else 1.0
                total += self._amount(effect.count) * per
            elif isinstance(effect, fx.Amass):
                total += 1.5 * self._amount(effect.count)
            elif isinstance(effect, (fx.ReturnToBattlefield,)):
                total += 4.0
            elif isinstance(effect, (fx.SacrificeSelf,)):
                total -= 1.0
            else:
                total += 0.5  # an effect this heuristic does not model: assume mildly good
        return total

    _x = 0

    def _amount(self, value) -> int:
        if value == "x":
            return self._x
        return value if isinstance(value, int) else 2

    def _who_sign(self, view: PlayerView, who: str, targets) -> float:
        if who == "you":
            return 1.0
        if who in ("each_opponent",):
            return -1.0
        if who in ("target_player", "target"):
            seat = next((t.id for t in targets if t.kind == "player"), view.seat)
            return 1.0 if seat == view.seat else -1.0
        return 0.0

    def _target_perm(self, view: PlayerView, ref: str, targets) -> PermanentView | None:
        index = _touched(ref, targets)
        if len(index) != 1:
            return None
        t = targets[index[0]]
        return view.permanent(t.id) if t.kind == "object" else None

    def _target_effect_value(self, view: PlayerView, effect, t) -> float:
        me, opp = view.seat, view.opponent
        if t.kind == "none":
            return 0.0
        if t.kind == "player":
            if isinstance(effect, fx.DealDamage):
                if t.id == me:
                    return -10.0
                amount = self._amount(effect.amount)
                return 100.0 if amount >= view.life(opp) else 0.4 * amount
            return 0.0
        perm = view.permanent(t.id)
        if perm is None:
            card = view.card(t.id)  # a card in a graveyard, e.g. reanimation
            if card is None:
                return 0.0
            return self._sv(card.spec) if card.owner == me else 0.5
        mine = perm.controller == me
        harmful = isinstance(effect, REMOVAL) or isinstance(effect, fx.DealDamage) or (
            isinstance(effect, fx.Tap) and not effect.untap) or (
            isinstance(effect, fx.Pump) and isinstance(effect.toughness, int)
            and effect.toughness < 0) or isinstance(effect, fx.RemoveCounters)
        if harmful:
            if mine:
                return -10.0
            if self._removes(effect, perm):
                bonus = 0.5 if isinstance(effect, fx.ReturnToHand) else 1.0
                return bonus * self._pv(perm)
            if isinstance(effect, fx.Tap):
                return 0.5 if perm.is_creature and not perm.tapped else -1.0
            return 0.2
        # Helpful effects: pump, counters, untap, attach.
        if not mine:
            return -10.0
        if isinstance(effect, fx.Pump):
            p = effect.power if isinstance(effect.power, int) else 2
            q = effect.toughness if isinstance(effect.toughness, int) else 2
            return 1.0 + p + q + len(effect.keywords)
        if isinstance(effect, fx.AddCounters):
            return 1.5 * self._amount(effect.count) + (1.0 if perm.is_creature else -2.0)
        if isinstance(effect, fx.Tap):
            return 0.5 if perm.tapped else -0.5
        return 1.0

    def _removes(self, effect, perm: PermanentView) -> bool:
        if isinstance(effect, REMOVAL):
            return True
        if isinstance(effect, fx.DealDamage):
            amount = self._amount(effect.amount)
            if perm.is_planeswalker and not perm.is_creature:
                return amount >= perm.loyalty
            return perm.is_creature and amount >= perm.toughness - perm.damage
        if isinstance(effect, fx.Pump) and isinstance(effect.toughness, int):
            return perm.is_creature and perm.toughness + effect.toughness <= 0
        return False

    def _mass_value(self, view: PlayerView, effect, ref: str) -> float:
        side = _side(ref)
        creatures_only = "creature" in ref

        def pool(seat: int):
            perms = view.creatures(seat) if creatures_only else view.battlefield(seat)
            return [p for p in perms if not p.is_land]

        def worth(seat: int) -> float:
            total = 0.0
            for perm in pool(seat):
                if isinstance(effect, fx.DealDamage):
                    if self._amount(effect.amount) >= perm.toughness - perm.damage:
                        total += self._pv(perm)
                elif isinstance(effect, REMOVAL):
                    total += self._pv(perm)
                elif isinstance(effect, (fx.Pump, fx.AddCounters)):
                    power = getattr(effect, "power", getattr(effect, "count", 1))
                    total += 1.0 + (power if isinstance(power, int) else 1)
                else:
                    total += 0.5
            return total

        helpful = isinstance(effect, (fx.Pump, fx.AddCounters)) and not (
            isinstance(effect, fx.Pump) and isinstance(effect.toughness, int)
            and effect.toughness < 0)
        mine, theirs = view.seat, view.opponent
        if helpful:
            if isinstance(effect, fx.Pump) and not effect.permanent and not (
                    view.step == Step.DECLARE_BLOCKERS and view.attackers()):
                return -1.0  # a team pump is a combat trick
            return worth(mine) if side != "theirs" else -worth(theirs)
        gain = worth(theirs) if side != "yours" else 0.0
        loss = worth(mine) if side != "theirs" else 0.0
        return gain - loss - 1.0

    def _activation_cost(self, view: PlayerView, ability, source) -> float:
        """What paying an ability's non-mana costs gives up."""
        cost = 1.8 * ability.discard + 0.3 * ability.life
        if ability.sacrifice:
            fodder = [p for p in view.battlefield(view.seat) if p.id != source.id
                      and not p.is_land]
            cost += 1.0 + min((self._pv(p) for p in fodder), default=3.0)
        if ability.sacrifice_self and getattr(source, "is_creature", False):
            cost += self._pv(source)
        if ability.sacrifice_self and getattr(source, "is_land", False):
            cost += 2.0 if len(view.lands(view.seat)) < 6 else 0.5
        if (ability.tap_cost and getattr(source, "is_creature", False) and view.is_my_turn
                and view.step in (Step.UPKEEP, Step.DRAW, Step.PRECOMBAT_MAIN,
                                  Step.BEGIN_COMBAT)):
            cost += 1.0 + 0.3 * getattr(source, "power", 0)  # it could have attacked
        return cost

    @staticmethod
    def _utility_window(view: PlayerView) -> bool:
        """Own second main phase, or the opponent's end step: mana that is
        still up then would otherwise be wasted."""
        if view.is_my_turn:
            return view.step == Step.POSTCOMBAT_MAIN
        return view.step == Step.END_STEP

    def _score_activation(self, view: PlayerView, action: act.ActivateAbility) -> float:
        source = view.card(action.source_id)
        ability = view.abilities(action.source_id)[action.index]
        if ability.loyalty is not None:
            # A planeswalker's loyalty is a resource: spending it costs a
            # little, adding it is worth a little.
            value = self._effects_value(view, ability.effects, action.targets,
                                        source_id=action.source_id)
            value += 0.35 * ability.loyalty
            return value if value > 0 else -1.0
        if ability.is_equip:
            target = view.permanent(action.targets[0].id) if action.targets else None
            if target is None or target.controller != view.seat:
                return -1.0
            if source.attached_to is not None:  # already on something: re-equip rarely
                return -1.0
            return 3.0 + self._pv(target) * 0.1
        if any(isinstance(e, fx.Pump) and e.to == "self" for e in ability.effects):
            # Firebreathing: only while it is attacking unblocked or in a fight.
            attacking = getattr(source, "attacking", False)
            return 1.0 if (attacking and view.step == Step.DECLARE_BLOCKERS) else -1.0
        if ability.discard_self:  # cycling: only when the card is dead weight
            lands = len(view.lands(view.seat))
            if source.spec.is_land and lands < 5:
                return -1.0
            if not source.spec.is_land and source.spec.cost.mana_value <= lands + 1:
                return -1.0
            return 1.0
        value = self._effects_value(view, ability.effects, action.targets,
                                    source_id=action.source_id)
        value -= self._activation_cost(view, ability, source)
        if not action.targets and not self._utility_window(view):
            return -1.0  # card-flow abilities wait until the mana would otherwise go unused
        return value - 0.5 if value > 0.5 else -1.0

    # ---------------------------------------------------------------- combat

    def _plan_attacks(self, view: PlayerView) -> dict[int, int]:
        return self._assign_targets(view, self._plan_attackers(view))

    def _plan_attackers(self, view: PlayerView) -> set[int]:
        me, opp = view.seat, view.opponent
        mine = [c for c in view.creatures(me) if not c.tapped and not c.summoning_sick
                and not c.has(Keyword.DEFENDER)]
        blockers = [c for c in view.creatures(opp) if c.can_block]
        opp_life = view.life(opp)

        def blockable_by(att: PermanentView) -> list[PermanentView]:
            found = [b for b in blockers if can_block(view, b, att)]
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
                if att_dies and blk_dies and self._pv(blk) < self._pv(att) - 1:
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

    def _assign_targets(self, view: PlayerView, attackers: set[int]) -> dict[int, int]:
        """Everyone attacks the player, except that the smallest attacker able
        to finish off an opposing planeswalker goes at it (or, failing that,
        the smallest attacker chips at the most loyal one)."""
        plan = dict.fromkeys(attackers, -1)
        walkers = [p for p in view.battlefield(view.opponent)
                   if p.is_planeswalker and not p.is_creature]
        if not walkers or not attackers:
            return plan
        ours = sorted((view.permanent(i) for i in attackers), key=lambda c: c.power)
        for walker in sorted(walkers, key=lambda w: -w.loyalty):
            free = [c for c in ours if plan[c.id] == -1]
            if not free:
                break
            finisher = next((c for c in free if c.power >= walker.loyalty), None)
            chosen = finisher or (free[0] if walker.loyalty >= 4 else None)
            if chosen is not None:
                plan[chosen.id] = walker.id
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
            candidates = [b for b in pool if can_block(view, b, att)]
            good = [b for b in candidates if combat_outcome(att, b) == (True, False)]
            trade = [b for b in candidates if combat_outcome(att, b) == (True, True)
                     and self._pv(b) <= self._pv(att)]
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
            candidates = [b for b in pool if can_block(view, b, att)]
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
