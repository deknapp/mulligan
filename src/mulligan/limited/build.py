"""A deterministic Limited deckbuilder: pick colors, 23 spells, 17 lands.

Adapted from mtg_ai's sealed builder, minus its dependence on 17Lands: the card
ratings are a parameter, so the same builder works on release day (with
``static_rating``) and later (with simulated or real win rates).

1. Score every two-color pair by the total rating of its best 23 castable
   spells, nudged toward a playable creature count.
2. Take that pair's best 23 (colorless cards count for every pair).
3. Lands: the pool's on-color dual lands, then basics split by colored pips.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from ..cards.cube import BASICS
from ..cards.sets import SetData
from ..engine.card import CardSpec
from ..engine.effects import AddMana
from ..engine.types import pip_options
from .rating import static_rating

COLORS = "WUBRG"
SPELLS = 23
LANDS = 17
MIN_CREATURES = 13


@dataclass
class Deck:
    colors: str
    spells: list[CardSpec]
    lands: list[CardSpec]
    score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def cards(self) -> list[CardSpec]:
        return self.spells + self.lands

    def decklist(self) -> str:
        counts = Counter(c.name for c in self.cards)
        order = {c.name: (c.is_land, c.cost.mana_value, c.name) for c in self.cards}
        lines = ["Deck"] + [f"{n} {name}" for name, n in sorted(counts.items(),
                                                               key=lambda kv: order[kv[0]])]
        return "\n".join(lines) + "\n"


def castable_in(spec: CardSpec, colors: set[str]) -> bool:
    for symbol, _ in spec.cost.pips:
        if not any(part in colors or part == "C" for part in pip_options(symbol)):
            return False
    if spec.adventure is not None:
        return True  # the creature half decides; the adventure is a bonus
    return True


def _produced(spec: CardSpec) -> set[str]:
    colors: set[str] = set()
    for ability in spec.abilities:
        if not ability.is_mana_ability:
            continue
        for effect in ability.effects:
            if isinstance(effect, AddMana):
                for unit in effect.symbols:
                    colors |= set(COLORS) if unit == "*" else set(unit.split("/"))
    return colors


def build_deck(pool: list[str], data: SetData, ratings: dict[str, float] | None = None,
               lands: int = LANDS, colors: str | None = None) -> Deck:
    """Build the best 40-card deck the pool allows (``colors`` forces a pair)."""
    rate = (lambda s: ratings.get(s.name, static_rating(s))) if ratings else static_rating
    specs = [data.playable[n] for n in pool if n in data.playable]
    spells = [s for s in specs if not s.is_land]
    nonbasic_lands = [s for s in specs if s.is_land and "Basic" not in s.supertypes]
    n_spells = 40 - lands

    def best_for(pair: set[str]) -> tuple[float, list[CardSpec]]:
        playable = sorted((s for s in spells if castable_in(s, pair)), key=rate, reverse=True)
        chosen = playable[:n_spells]
        creatures = sum(1 for s in chosen if s.is_creature)
        if creatures < MIN_CREATURES:
            # Swap the weakest noncreatures for the best creatures left out.
            spare = [s for s in playable[n_spells:] if s.is_creature]
            for creature in spare:
                if creatures >= MIN_CREATURES:
                    break
                weakest = min((s for s in chosen if not s.is_creature), key=rate, default=None)
                if weakest is None or rate(creature) < rate(weakest) - 2.0:
                    break
                chosen.remove(weakest)
                chosen.append(creature)
                creatures += 1
        score = sum(rate(s) for s in chosen) - 1.5 * max(0, MIN_CREATURES - creatures)
        score -= 3.0 * max(0, n_spells - len(chosen))  # too few playables
        return score, chosen

    pairs = [colors] if colors else ["".join(p) for p in combinations(COLORS, 2)]
    scored = sorted(((best_for(set(p)), p) for p in pairs), key=lambda x: x[0][0],
                    reverse=True)
    (score, chosen), pair = scored[0]
    deck_lands = _manabase(chosen, set(pair), nonbasic_lands, lands)
    notes = [f"{p}: {s:.1f}" for (s, _), p in scored[:3]]
    return Deck(colors=pair, spells=chosen, lands=deck_lands, score=round(score, 2),
                notes=notes)


def _manabase(spells: list[CardSpec], colors: set[str], nonbasics: list[CardSpec],
              total: int) -> list[CardSpec]:
    pips: Counter[str] = Counter()
    for spec in spells:
        for symbol, n in spec.cost.pips:
            options = [p for p in pip_options(symbol) if p in colors]
            for part in options:
                pips[part] += n / len(options)
    duals = [land for land in nonbasics if _produced(land) and _produced(land) <= colors][:3]
    remaining = total - len(duals)
    used = [c for c in COLORS if c in colors and pips[c] > 0] or sorted(colors)
    weight = sum(pips[c] for c in used) or 1.0
    counts = {c: max(3 if pips[c] else 0, round(remaining * pips[c] / weight)) for c in used}
    while sum(counts.values()) > remaining:
        counts[max(counts, key=counts.get)] -= 1
    while sum(counts.values()) < remaining:
        counts[max(used, key=lambda c: pips[c] / max(1, counts[c]))] += 1
    basics = [BASICS[c] for c in used for _ in range(counts[c])]
    return duals + basics
