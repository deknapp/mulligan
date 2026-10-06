"""The shortcut simulator: games decided by real card win rates, not by play.

Nobody plays a turn here. Each simulated game shuffles both decks, looks at
the cards each player would see by the time a typical Limited game is decided
(opening seven plus the draws), and turns what they saw into a win
probability:

    logit P(A wins) = play bonus + Σ value(seen by A) − Σ value(seen by B)
                      − mana trouble(A) + mana trouble(B)

A card's value is how far its 17Lands games-in-hand win rate (GIH WR) sits
above or below the set's average, in log-odds, shrunk toward zero when the
card has few games. Lands are neutral; seeing too few of them early (screw)
or far too many (flood) costs a fixed amount. That is the whole model, so it
plays 100,000 games in about a second and needs no compiled card text: any
card 17Lands has rated works, including the ones the rules engine simplifies.

What it cannot see: synergies, curve, combat, or which cards are good
*against each other*. GIH WR is also biased toward cards that are drawn in
long games. ``validate`` measures how well it ranks real decks anyway.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

PLAY_BONUS = 0.19          # log-odds edge of being on the play (≈54.7%; HOB 17Lands games)
SHRINK_GAMES = 300         # a card with this many games keeps half its measured edge
# A card's GIH edge overstates what it does for a deck: good cards sit in good
# decks piloted by good players, and summing 23 cards multiplies that. Fitted
# by maximum likelihood on held-out real HOB decks' records (2,620 decks): at
# 1.0 the worst fifth of decks was predicted 51% against an actual 60%, the
# best 71% against 69% (0.40 at 15 cards seen; 0.38 at 16).
VALUE_SCALE = 0.38
SEEN_ON_PLAY = 16          # cards seen in a real HOB game: 16.7 on the play, 17.2 on the draw
EARLY = 10                 # cards seen by turn 3-4 on the play
SCREW_PER_LAND = 0.18      # per land short of 3 among the early cards (scaled with VALUE_SCALE)
FLOOD_PER_LAND = 0.05      # per land past 9 among the cards seen


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class CardValues:
    """Per-card log-odds edges from GIH WR, plus a fallback for unrated cards."""
    values: dict[str, float]
    mean_gih: float
    fallback: dict[str, float] = field(default_factory=dict)  # e.g. from expert grades
    source: str = ""

    @classmethod
    def from_gih(cls, gih: dict[str, tuple[float, int]], source: str = "",
                 shrink: int = SHRINK_GAMES) -> CardValues:
        """``gih``: card -> (GIH WR, games in hand)."""
        total = sum(n for _, n in gih.values())
        mean = sum(r * n for r, n in gih.values()) / total if total else 0.55
        values = {name: (_logit(rate) - _logit(mean)) * n / (n + shrink)
                  for name, (rate, n) in gih.items()}
        return cls(values, mean, source=source)

    def value(self, name: str) -> float:
        if name in self.values:
            return self.values[name]
        return self.fallback.get(name, 0.0)

    def known(self, name: str) -> bool:
        return name in self.values or name in self.fallback


def load_values(set_code: str, fmt: str = "PremierDraft") -> CardValues:
    """Full-history GIH WR from 17Lands' game files when they exist, else the
    live card-ratings endpoint (a rolling sample, fine once a set has been out
    a few days)."""
    from .validation import seventeen
    try:
        gih = seventeen.game_data_ratings(set_code, fmt)
        source = f"17Lands {set_code.upper()} {fmt} game data"
    except Exception:  # noqa: BLE001 - not published yet: fall back to the live endpoint
        gih = seventeen.fetch_ratings(set_code, fmt)
        # 17Lands' live view rates only well-sampled cards: a young Sealed
        # format can cover a third of the set while Premier Draft covers it all.
        if fmt != "PremierDraft" and len(gih) < 150:
            gih = seventeen.fetch_ratings(set_code, "PremierDraft")
            fmt = "PremierDraft"
        source = f"17Lands {set_code.upper()} {fmt} card ratings (live)"
    return CardValues.from_gih(gih, source)


@dataclass
class Deck:
    """A 40-ish card deck as a flat list: (value, is_land) per card."""
    cards: list[tuple[float, bool]]

    @classmethod
    def from_names(cls, names: dict[str, int], values: CardValues,
                   is_land) -> Deck:
        cards = []
        for name, n in names.items():
            land = is_land(name)
            cards.extend([(0.0 if land else values.value(name), land)] * n)
        return cls(cards)


def _seen_score(cards: list[tuple[float, bool]], seen: int) -> float:
    top = cards[:seen]
    early_lands = sum(1 for _, land in cards[:EARLY] if land)
    lands = sum(1 for _, land in top if land)
    score = VALUE_SCALE * sum(v for v, land in top if not land)
    score -= SCREW_PER_LAND * max(0, 3 - early_lands)
    score -= FLOOD_PER_LAND * max(0, lands - 9)
    return score


def head_to_head(a: Deck, b: Deck, games: int = 20000, seed: int = 0) -> float:
    """A's expected win rate against B, alternating who is on the play."""
    rng = random.Random(seed)
    da, db = list(a.cards), list(b.cards)
    total = 0.0
    for g in range(games):
        rng.shuffle(da)
        rng.shuffle(db)
        a_plays = g % 2 == 0
        sa = _seen_score(da, SEEN_ON_PLAY + (0 if a_plays else 1))
        sb = _seen_score(db, SEEN_ON_PLAY + (1 if a_plays else 0))
        total += _sigmoid((PLAY_BONUS if a_plays else -PLAY_BONUS) + sa - sb)
    return total / games


def vs_field(deck: Deck, games: int = 20000, seed: int = 0) -> float:
    """Win rate against an average deck (every card worth the set's mean)."""
    average = Deck([(0.0, False)] * 23 + [(0.0, True)] * 17)
    return head_to_head(deck, average, games, seed)


def deck_score(deck: Deck, games: int = 4000, seed: int = 0) -> float:
    """Expected log-odds edge per game; cheaper than ``vs_field`` for ranking builds."""
    rng = random.Random(seed)
    cards = list(deck.cards)
    total = 0.0
    for g in range(games):
        rng.shuffle(cards)
        total += _seen_score(cards, SEEN_ON_PLAY + g % 2)
    return total / games


BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def land_test(set_code: str):
    """name -> is it a land, from the compiled set's card types."""
    from .cards.sets import load_set
    data = load_set(set_code)
    lands = {n for n, e in data.entries.items() if "Land" in e.get("types", [])}
    return lambda name: name in BASIC_LANDS or name in lands


def validate(set_code: str = "hob", fmt: str = "PremierDraft", n_decks: int = 2000,
             seed: int = 0) -> dict:
    """Rank held-out real decks (the deck model's test drafts) by the shortcut
    simulator and correlate with their actual records. Card values use every
    game, held-out ones included, so this flatters it slightly."""
    from .deckmodel import DeckModel
    from .validation.decks import real_decks
    from .validation.seventeen import spearman
    values = load_values(set_code, fmt)
    is_land = land_test(set_code)
    pool = real_decks(set_code, fmt)
    rng = random.Random(seed)
    decks = rng.sample(pool, min(n_decks, len(pool)))
    fast = [deck_score(Deck.from_names(d.names, values, is_land), 1500, seed) for d in decks]
    actual = [d.win_rate for d in decks]
    out = {"decks": len(decks), "fast_rho": spearman(fast, actual),
           "skill_rho": spearman([d.skill for d in decks], actual)}
    try:
        model = DeckModel.load(set_code, fmt)
        out["model_rho"] = spearman([model.score(d.names) for d in decks], actual)
    except FileNotFoundError:
        pass
    return out
