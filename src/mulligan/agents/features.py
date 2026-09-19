"""Features for the learned correction: what an action is, in context.

Each legal action becomes a sparse set of named features. Two kinds:

* **General** — action type crossed with the game situation (ahead or behind
  on life and board, turn, cards in hand, mana left). These transfer between
  sets.
* **Card-specific** — ``cast:<name>``, ``attack:<name>``, ``block:<name>``,
  ``land:<name>``… These are how a model learns the particulars of one set:
  that a card is better or worse than general principles make it look, or
  should be held rather than played on curve.

Features are read only from the ``PlayerView``, so a learned agent obeys the
same information rules as every other agent.
"""

from __future__ import annotations

from ..engine import actions as act
from ..engine.types import Step
from ..engine.view import PlayerView

Features = dict[str, float]


def situation(view: PlayerView) -> Features:
    """A handful of numbers describing who is winning. Also the inputs to the
    value baseline during training."""
    me, opp = view.seat, view.opponent
    my_power = sum(c.power for c in view.creatures(me))
    opp_power = sum(c.power for c in view.creatures(opp))
    return {
        "bias": 1.0,
        "life_diff": (view.life(me) - view.life(opp)) / 10.0,
        "power_diff": (my_power - opp_power) / 10.0,
        "creature_diff": (len(view.creatures(me)) - len(view.creatures(opp))) / 5.0,
        "hand_diff": (view.hand_size(me) - view.hand_size(opp)) / 5.0,
        "land_diff": (len(view.lands(me)) - len(view.lands(opp))) / 5.0,
        "turn": min(view.turn, 20) / 10.0,
        "on_play": 1.0 if view.on_the_play else 0.0,
    }


def _context(kind: str, sit: Features) -> Features:
    return {f"{kind}|{k}": v for k, v in sit.items() if k != "on_play"}


def action_features(view: PlayerView, action: act.Action, sit: Features) -> Features:
    feats: Features = {}
    if isinstance(action, act.Pass):
        kind = "pass"
        feats[f"pass|step:{view.step.value}"] = 1.0
        feats["pass|stack"] = 1.0 if view.stack() else 0.0
        feats["pass|mana_left"] = view.mana_available() / 5.0
    elif isinstance(action, act.PlayLand):
        kind = "land"
        card = view.card(action.card_id)
        feats[f"land:{card.name}"] = 1.0
    elif isinstance(action, act.CastSpell):
        kind = "cast"
        card = view.card(action.card_id)
        spec = card.spec.adventure if action.face == "adventure" else card.spec
        name = spec.name + (f"#{action.mode}" if action.mode >= 0 else "")
        feats[f"cast:{name}"] = 1.0
        timing = ("main" if view.is_my_turn and view.step in (Step.PRECOMBAT_MAIN,
                                                               Step.POSTCOMBAT_MAIN)
                  else "combat" if view.step in (Step.DECLARE_ATTACKERS, Step.DECLARE_BLOCKERS)
                  else "other")
        feats[f"cast:{name}|{timing}"] = 1.0
        feats[f"cast|{timing}"] = 1.0
        feats["cast|mv"] = spec.cost.mana_value / 5.0
        for t in action.targets:
            if t.kind == "player":
                feats["cast|target_player_" + ("self" if t.id == view.seat else "opp")] = 1.0
            elif t.kind == "object":
                perm = view.permanent(t.id)
                if perm is not None:
                    side = "own" if perm.controller == view.seat else "opp"
                    feats[f"cast:{name}|target_{side}"] = 1.0
                    feats[f"cast|target_{side}_power"] = perm.power / 5.0 if perm.is_creature \
                        else 0.0
    elif isinstance(action, act.ActivateAbility):
        kind = "activate"
        card = view.card(action.source_id)
        feats[f"activate:{card.name}:{action.index}"] = 1.0
        feats[f"activate:{card.name}:{action.index}|my_turn"] = 1.0 if view.is_my_turn else 0.0
    elif isinstance(action, act.DeclareAttacker):
        kind = "attack"
        perm = view.permanent(action.creature_id)
        feats[f"attack:{perm.name}"] = 1.0
        blockers = [c for c in view.creatures(view.opponent) if view.could_block(c, perm)]
        feats["attack|blockers"] = len(blockers) / 3.0
        feats["attack|biggest_blocker_vs_toughness"] = (
            max((b.power for b in blockers), default=0) - perm.toughness) / 5.0
        feats["attack|power"] = perm.power / 5.0
    elif isinstance(action, act.DeclareBlocker):
        kind = "block"
        blocker = view.permanent(action.blocker_id)
        attacker = view.permanent(action.attacker_id)
        feats[f"block:{blocker.name}"] = 1.0
        feats[f"blocked:{attacker.name}"] = 1.0
        kills = attacker.toughness - attacker.damage <= blocker.power
        dies = blocker.toughness - blocker.damage <= attacker.power
        feats["block|kills"] = 1.0 if kills else 0.0
        feats["block|dies"] = 1.0 if dies else 0.0
        feats["block|chump"] = 1.0 if dies and not kills else 0.0
    elif isinstance(action, act.FinishDeclaring):
        kind = "finish_" + view.pending
        feats[f"{kind}|declared"] = (len(view.attackers()) if view.pending == "attackers"
                                     else len(view.blocks())) / 3.0
    elif isinstance(action, act.ChooseTargets):
        kind = "targets"
        trigger = view.pending_trigger()
        source = trigger.name.split(" (")[0] if trigger is not None else "?"
        if action.mode >= 0:
            feats[f"targets:{source}|mode{action.mode}"] = 1.0
        for t in action.targets:
            if t.kind == "object":
                perm = view.permanent(t.id)
                side = "own" if perm is not None and perm.controller == view.seat else "opp"
            elif t.kind == "player":
                side = "self" if t.id == view.seat else "opp"
            else:
                side = "none"
            feats[f"targets:{source}|{side}"] = 1.0
    elif isinstance(action, act.KeepHand):
        kind = "keep"
        hand = view.hand()
        lands = sum(1 for c in hand if c.spec.is_land)
        feats[f"keep|lands={min(lands, 6)}"] = 1.0
        feats["keep|mulligans"] = float(view.mulligans(view.seat))
    elif isinstance(action, act.Mulligan):
        kind = "mulligan"
    elif isinstance(action, (act.Discard, act.PutOnBottom)):
        kind = "discard"
        card = view.card(action.card_id)
        feats[f"discard:{card.name}"] = 1.0
        feats["discard|land"] = 1.0 if card.spec.is_land else 0.0
        feats["discard|mv"] = card.spec.cost.mana_value / 5.0
    else:  # pragma: no cover - every action type is handled above
        kind = type(action).__name__
    feats[kind] = 1.0
    feats.update(_context(kind, sit))
    return feats
