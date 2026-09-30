"""Which card to take: the simulator's card win rates and 17Lands' real ones,
blended, then adjusted for the cards you already have.

A pick's score, in win-rate points, is how much it adds to the deck you'll
likely end up with:

* your pool as ten two-color decks: each pair is worth what its best 23
  cards add over a card that wouldn't make the deck (a card outside the pair
  counts only as a splash: one light off-color pip, and only for strong
  cards). So quality counts, not how many cards share a color: a first-pick
  bomb makes its pairs worth more at once;
* which pair you'll end in is uncertain: a softmax over the pairs' values,
  wide early (most picks are still to come) and narrow by pack three. The
  pick's score is the gain in that soft maximum from adding it. Early, a card
  that fits many pairs (mono-colored, colorless) keeps options open; a strong
  card in your best pairs counts in full; a gold card pulls toward its pair
  only as far as it's good;
* your pair (with simulator data): how much better or worse the card does in
  simulated decks of each pair, weighted by how likely you end in it;
* synergy with your whole pool: the card's pair interaction (``limited.synergy``)
  with every card you've taken, each weighted by how likely that card is to
  make your deck, times the chance the two are drawn in the same game, at half
  weight (simulated pair effects only loosely match real ones);
* lands: a land tapping for both colors of a pair is worth a late pick in
  it, more when it lets you splash a strong card you've taken.

The website's pick helper (``site/tools.js``) ports the pool adjustments
above line for line (``tests/test_site_pick.py`` holds the two to the same
ranking); its card ratings differ in that it swaps in a 17Lands number once it
has 500 games, where this blends the two, and it averages in expert grades
until real data arrives. The
simulator is treated as a prior worth
``REAL_PRIOR_GAMES`` real games, so on release day a card is rated by the
simulator alone, at 500 real games half and half, and at 5,000 almost entirely
by real players. Real win rates run higher than simulated ones (17Lands users
are better than average), so they are shifted onto the simulator's scale first,
by the median gap over cards both sides have.

Data: the newest simulated draft of the set (from this clone's ``blog/data``,
or the published site's copy, whichever run is newer) and 17Lands' live card
ratings, fetched at most every ``REAL_MAX_AGE`` seconds and cached.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.request
from dataclasses import dataclass, field

from .paths import cache_dir

SITE_DATA = "https://deknapp.github.io/mulligan/data"
REAL_PRIOR_GAMES = 500       # the simulator counts as this many real games
REAL_MIN_SHARED = 20         # cards needed on both sides before real data is used at all
REAL_MAX_AGE = 3 * 3600
COMMIT_PICKS = 14            # picks until the compass calls your colors settled
PAIR_MIN_GAMES = 150
PAIR_PRIOR_GAMES = 300       # a card's record in one pair is shrunk toward its overall one
PAIR_WEIGHT = 0.5            # how much of that difference counts
DRAWN_TOGETHER = 0.4         # chance a deck card is drawn in a game you draw the pick
SYNERGY_WEIGHT = 0.5         # simulated pair effects track real ones only loosely (HOB r=+0.24)
PLAYABLE_Z = -1.0            # rated at least this (about a C-) counts as a playable
UNPLAYABLE_IN_DECK = 0.3
TOTAL_PICKS = 42             # three packs of 14
DECK_SPELLS = 23             # a pair's value counts its best this many cards
FLOOR = -0.02                # a card that wouldn't make the deck, vs. the average card
SPREAD_START = 0.025         # pool-value gap that makes one pair e (2.7) times likelier, pick 1
SPREAD_END = 0.006           # ... and at the last pick
SPLASH_BAR = 0.02            # only a card this much better than average is worth splashing ...
SPLASH_SHARE = 0.5           # ... and counts this share of the rest
SPLASH_FIXED = 0.8           # ... or this share with a dual for it in your pool
TIEBREAK = 0.02              # of a card's own rating, so unplayables still rank
DUAL_LAND = 0.008            # a dual in your final colors
SPLASH_LAND = 0.015          # a dual that lets you splash a strong card you have
COLORS = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
PAIR_NAMES = {"WU": "Azorius", "UB": "Dimir", "BR": "Rakdos", "RG": "Gruul", "WG": "Selesnya",
              "WB": "Orzhov", "UR": "Izzet", "BG": "Golgari", "WR": "Boros", "UG": "Simic"}
GRADES = [(2, "A+"), (1.5, "A"), (1, "A-"), (0.6, "B+"), (0.25, "B"), (-0.1, "B-"),
          (-0.45, "C+"), (-0.8, "C"), (-1.2, "C-"), (-1.6, "D"), (-math.inf, "F")]


def _get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "mulligan/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def load_sim(set_code: str, fetch: bool = True) -> dict | None:
    """The newest simulated draft, as the website's tools see it."""
    candidates = []
    try:
        from .site.build import BLOG
        from .site.tools import export, latest_run
        run = latest_run(BLOG, set_code)
        if run is not None:
            candidates.append(export(run))
    except Exception:  # not a clone, or the set moved on since the run
        pass
    cache = cache_dir("site", f"{set_code}.json")
    try:
        if not fetch:
            raise OSError("offline")
        published = _get_json(f"{SITE_DATA}/{set_code}.json")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(published))
        candidates.append(published)
    except Exception:
        if cache.exists():
            candidates.append(json.loads(cache.read_text()))
    return max(candidates, key=lambda d: d.get("run", "")) if candidates else None


def load_real(set_code: str, fmt: str = "PremierDraft", fetch: bool = True) -> dict | None:
    """17Lands' live card ratings for the set, cached for a few hours."""
    from .site.live import fetch as fetch_17lands
    cache = cache_dir("17lands_live", f"{set_code}-{fmt}.json")
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < REAL_MAX_AGE
    if fetch and not fresh:
        try:
            data = fetch_17lands(set_code, fmt)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
            return data
        except Exception:
            pass
    return json.loads(cache.read_text()) if cache.exists() else None


@dataclass
class Card:
    name: str
    colors: str = ""
    rarity: str = ""
    removal: bool = False
    sim: float | None = None          # shrunk simulated win rate when drawn
    sim_games: int = 0
    real: float | None = None         # 17Lands GIH WR, as reported
    real_games: int = 0
    ata: float | None = None          # average pick: real if known, else simulated
    est: float | None = None          # the blend, on the simulator's scale
    z: float | None = None
    grade: str | None = None
    unsupported: str | None = None
    pairs: dict[str, list[float]] = field(default_factory=dict)
    pips: dict[str, float] = field(default_factory=dict)
    makes: str = ""                   # colors a land can tap for
    ez: float | None = None           # expert grades, each host standardized, averaged
    ex_grade: str | None = None       # the hosts' average letter
    expert_grades: list[tuple[str, str]] = field(default_factory=list)   # (host, grade)
    cost: str = ""                    # mana cost, {1}{W}


@dataclass
class Ratings:
    cards: dict[str, Card]
    mean: float
    sim_mean: float
    shift: float
    real_used: int                    # cards whose rating includes real games
    real_rows: int                    # cards 17Lands has a win rate for
    sim_run: str = ""
    real_fetched: str = ""
    real_start: str = ""
    synergy: dict[tuple[str, str], float] = field(default_factory=dict)
    pair_mean: dict[str, float] = field(default_factory=dict)
    sd: float = 1.0
    experts: bool = False             # expert grades are averaged into each card's rating

    def syn(self, a: str, b: str) -> float:
        return self.synergy.get((a, b) if a < b else (b, a), 0.0)

    def get(self, name: str) -> Card:
        return self.cards.get(name) or Card(name)


def pips(cost: str) -> dict[str, float]:
    """Colored pips per color; a hybrid pip counts half toward each side, a
    {2/W} pip nothing (generic mana pays for it)."""
    out: dict[str, float] = {}
    for sym in re.findall(r"\{([^}]+)\}", cost or ""):
        parts = [p for p in sym.split("/") if p in "WUBRG" and p]
        if sym.startswith("2/") or not parts:
            continue
        for k in parts:
            out[k] = out.get(k, 0) + 1 / len(parts)
    return out


def land_colors(oracle: str) -> str:
    """The colors a land's mana abilities make: "{T}: Add {R} or {W}." -> "WR"."""
    made = set()
    for clause in re.findall(r"Add ([^.]*)", oracle or ""):
        made |= set(re.findall(r"\{([WUBRG])\}", clause))
        if "any color" in clause or "chosen color" in clause:
            made |= set("WUBRG")
    return "".join(k for k in "WUBRG" if k in made)


LETTERS = ["F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]
EXPERTS_UNTIL = 20            # cards with REAL_PRIOR_GAMES real games; then experts drop out


def load_experts(set_code: str, fetch: bool = True) -> dict | None:
    """The podcast hosts' card grades (``mulligan experts``): this clone's copy,
    else the published site's, cached."""
    try:
        from .site.build import BLOG
        local = BLOG / "data" / f"{set_code}-experts.json"
        if local.exists():
            return json.loads(local.read_text())
    except Exception:
        pass
    cache = cache_dir("site", f"{set_code}-experts.json")
    try:
        if not fetch:
            raise OSError("offline")
        data = _get_json(f"{SITE_DATA}/{set_code}-experts.json")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
        return data
    except Exception:
        return json.loads(cache.read_text()) if cache.exists() else None


def add_experts(ratings: Ratings, experts: dict | None) -> None:
    """Expert grades, as the website's pick helper uses them (``addExperts`` in
    site/tools.js): each host's letters standardized (shows use the scale
    differently: Limited Resources' B is Limited Level-Ups' C+), then averaged
    per card. They count until 17Lands has real win rates for enough cards."""
    if not experts:
        return
    by_host: dict[str, list[int]] = {}
    graded = []
    for name, x in (experts.get("cards") or {}).items():
        c = ratings.cards.get(name)
        if c is None:
            continue
        grades = [g for g in x.get("grades", []) if g.get("grade") in LETTERS]
        if not grades:
            continue
        graded.append((c, grades))
        for g in grades:
            by_host.setdefault(f"{g['show']}|{g['host']}", []).append(LETTERS.index(g["grade"]))
    norm = {}
    for h, xs in by_host.items():
        m = sum(xs) / len(xs)
        norm[h] = (m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0)
    for c, grades in graded:
        idx = [LETTERS.index(g["grade"]) for g in grades]
        c.ex_grade = LETTERS[math.floor(sum(idx) / len(idx) + 0.5)]
        c.ez = sum((LETTERS.index(g["grade"]) - norm[f"{g['show']}|{g['host']}"][0])
                   / norm[f"{g['show']}|{g['host']}"][1] for g in grades) / len(grades)
        c.expert_grades = [(g["host"], g["grade"]) for g in grades]
    real_enough = sum(1 for c in ratings.cards.values()
                      if c.real is not None and c.real_games >= REAL_PRIOR_GAMES)
    ratings.experts = bool(graded) and real_enough < EXPERTS_UNTIL


def build_ratings(sim: dict | None, real: dict | None) -> Ratings:
    cards: dict[str, Card] = {}
    sim_mean = sim["mean"] if sim else 0.5
    prior = sim["prior"] if sim else 200
    for c in (sim or {}).get("cards", []):
        card = Card(c["n"], c.get("c") or "", c.get("r", ""), bool(c.get("rm")),
                    unsupported=c.get("un"), ata=c.get("ata") or None,
                    pairs=(sim.get("pc") or {}).get(c["n"], {}), pips=pips(c.get("cost", "")),
                    cost=c.get("cost", ""))
        if "Land" in c.get("t", ""):
            card.makes = land_colors(c.get("o", ""))
        if c.get("g"):
            card.sim = (c["w"] + prior * sim_mean) / (c["g"] + prior)
            card.sim_games = c["g"]
        if "Land" in c.get("t", "") and not card.colors:
            card.unsupported = card.unsupported or "land"
        cards[card.name] = card
    for name, r in ((real or {}).get("cards") or {}).items():
        if r.get("gih") is None:
            continue
        card = cards.setdefault(name, Card(name))
        card.real, card.real_games = r["gih"], r.get("gih_n") or 0
        if r.get("ata"):
            card.ata = r["ata"]
    shared = [c for c in cards.values() if c.real is not None and c.sim is not None]
    shift, used = 0.0, 0
    if len(shared) >= REAL_MIN_SHARED:
        # The median gap: one card real players love (or hate) can't move the scale.
        shift = statistics.median(c.real - c.sim for c in shared)
    # A set with no simulation at all runs on 17Lands alone, unshifted.
    enough = len(shared) >= REAL_MIN_SHARED or not (sim or {}).get("cards")
    for c in cards.values():
        real_ok = c.real is not None and enough
        if c.sim is not None and real_ok:
            n = c.real_games
            c.est = (c.sim * REAL_PRIOR_GAMES + (c.real - shift) * n) / (REAL_PRIOR_GAMES + n)
        elif real_ok:
            c.est = c.real - shift
        else:
            c.est = c.sim
        used += real_ok and c.real_games > 0
    rated = [c.est for c in cards.values() if c.est is not None and c.unsupported != "land"]
    mean = sum(rated) / len(rated) if rated else sim_mean
    sd = math.sqrt(sum((x - mean) ** 2 for x in rated) / len(rated)) if rated else 1.0
    for c in cards.values():
        if c.est is not None and sd:
            c.z = (c.est - mean) / sd
            c.grade = next(g for cut, g in GRADES if c.z >= cut)
    real_rows = sum(c.real is not None for c in cards.values())
    names = [c["n"] for c in (sim or {}).get("cards", [])]
    flat = ((sim or {}).get("syn") or {}).get("a") or []
    synergy = {}
    for i in range(0, len(flat) - 2, 3):
        a, b = sorted((names[flat[i]], names[flat[i + 1]]))
        synergy[a, b] = flat[i + 2] / 1000
    # The average card's win rate when drawn inside each pair's decks: the
    # yardstick for one card's record there (deck records run lower).
    sums: dict[str, list[float]] = {}
    for by_pair in ((sim or {}).get("pc") or {}).values():
        for p, (w, g) in by_pair.items():
            acc = sums.setdefault(p, [0.0, 0.0])
            acc[0] += w
            acc[1] += g
    pair_mean = {p: w / g for p, (w, g) in sums.items() if g}
    return Ratings(cards, mean, sim_mean, shift, used, real_rows, (sim or {}).get("run", ""),
                   (real or {}).get("fetched", ""), (real or {}).get("start", ""),
                   synergy, pair_mean, sd or 1.0)


# What a letter grade is worth, in win-rate points against a C+ card (F .. A+).
# Grades are spaced evenly but win rates are not: real sets' 17Lands numbers
# put an A+ bomb some ten points over a C+ card and a B+ under four, while C
# and C- sit about a point apart.
GRADE_POINTS = [-8.0, -6.5, -5.0, -3.5, -2.0, -1.0, 0.0, 1.0, 2.0, 3.5, 5.0, 7.0, 9.5]


def grade_points(index: float) -> float:
    """A (possibly fractional) index into LETTERS, in win-rate points."""
    index = min(max(index, 0.0), len(LETTERS) - 1.0)
    lo = min(int(index), len(LETTERS) - 2)
    return GRADE_POINTS[lo] + (index - lo) * (GRADE_POINTS[lo + 1] - GRADE_POINTS[lo])


def expert_ratings(sim: dict | None, experts: dict | None) -> Ratings:
    """Ratings from the podcast hosts' grades alone: the simulated draft file
    supplies only card facts (colors, cost, rarity, what lands make), never a
    win rate, pair record or synergy. Each host's letters are shifted so every
    host averages the same (Limited Resources grades higher than Limited
    Level-Ups), averaged per card, and read off GRADE_POINTS, so a bomb stands
    as far above a good uncommon as it does in real win rates. Ungraded cards
    go unrated."""
    base = build_ratings(sim, None)
    for c in base.cards.values():
        c.sim, c.sim_games, c.est, c.z, c.grade = None, 0, None, None, None
        c.pairs, c.ata = {}, None
    add_experts(base, experts)
    by_host: dict[str, list[int]] = {}
    for name, x in ((experts or {}).get("cards") or {}).items():
        for g in x.get("grades", []):
            if g.get("grade") in LETTERS and name in base.cards:
                by_host.setdefault(f"{g['show']}|{g['host']}", []).append(
                    LETTERS.index(g["grade"]))
    pooled = [i for xs in by_host.values() for i in xs]
    center = sum(pooled) / len(pooled) if pooled else 0.0
    offset = {h: center - sum(xs) / len(xs) for h, xs in by_host.items()}
    names = {h.split("|", 1)[1]: h for h in by_host}
    for c in base.cards.values():
        if c.ez is None:
            continue
        idx = [LETTERS.index(g) + offset[names[h]] for h, g in c.expert_grades
               if g in LETTERS and h in names]
        c.est = grade_points(sum(idx) / len(idx)) / 100
        c.z, c.grade = c.ez, c.ex_grade
    rated = [c.est for c in base.cards.values() if c.est is not None]
    base.synergy, base.pair_mean = {}, {}
    base.mean = sum(rated) / len(rated) if rated else 0.0
    base.sd, base.shift, base.real_used, base.real_rows = 0.03, 0.0, 0, 0
    base.experts = False          # est already is the experts' rating; no blending
    return base


def lane(ratings: Ratings, picks: list[str]) -> tuple[dict[str, float], list[str]]:
    """The two colors your picks point at, weighted by quality."""
    weight = dict.fromkeys("WUBRG", 0.0)
    for name in picks:
        c = ratings.get(name)
        q = max(0.2, 1 + (c.z or 0))           # good cards pull harder
        for k in c.colors:
            weight[k] += q / len(c.colors)
    order = sorted(weight, key=lambda k: -weight[k])
    return weight, [k for k in order[:2] if weight[k] > 0]


@dataclass
class Advice:
    card: Card
    score: float | None
    why: list[str]


def _pts(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}{abs(100 * x):.1f}"


def commitment(weight: dict[str, float], picks: int) -> float:
    """0 to 1: how settled your colors are. Grows with picks, and only as far
    as your picks actually concentrate in two colors."""
    total = sum(weight.values())
    if not total:
        return 0.0
    share = sum(sorted(weight.values())[-2:]) / total
    return min(1.0, picks / COMMIT_PICKS) * min(1.0, max(0.0, (share - 0.5) / 0.3))


PAIRS = ["WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG"]


def _value(ratings: Ratings, c: Card) -> float | None:
    """A card's rating in win-rate points against the average card, or None."""
    if c.makes and len(c.makes) >= 2:
        return None
    blend = ratings.experts and c.ez is not None
    if c.est is None:
        return c.ez * ratings.sd if blend else None
    if blend:
        # Half the card's own rating, half the experts' (on the same scale).
        return ((c.est - ratings.mean) / ratings.sd + c.ez) / 2 * ratings.sd
    return c.est - ratings.mean


def _worth(c: Card, v: float, pair: str, fixes: set[str]) -> float:
    """What a card adds to a deck of ``pair``: its edge over a card that
    wouldn't make the deck if it's in the colors, part of what it has beyond
    SPLASH_BAR if one light off-color pip lets you splash it, else nothing."""
    off = [k for k in c.colors if k not in pair]
    if not off:
        return max(0.0, v - FLOOR)
    if len(off) == 1 and c.pips.get(off[0], 1) <= 1:
        return (SPLASH_FIXED if off[0] in fixes else SPLASH_SHARE) * max(0.0, v - SPLASH_BAR)
    return 0.0


def spread(picks: int) -> float:
    """How far apart two pairs' pool values must be before the draft is
    likely to end in the better one: wide early (most picks are still to
    come), narrow by the last pack."""
    left = max(0, TOTAL_PICKS - 1 - picks) / (TOTAL_PICKS - 1)
    return SPREAD_END + (SPREAD_START - SPREAD_END) * math.sqrt(left)


@dataclass
class Lanes:
    """Your pool as ten possible two-color decks. ``value`` per pair: the sum
    of what your best DECK_SPELLS cards add to that deck (so quality counts,
    not just how many cards share a color); ``prob``: the chance the draft
    ends in each pair, a softmax over those values at the current spread."""
    worths: dict[str, list[float]]
    value: dict[str, float]
    prob: dict[str, float]
    temp: float
    fixes: set[str]

    def expected(self, value: dict[str, float] | None = None) -> float:
        """The value of the deck you end up with, allowing for the picks still
        to come (a soft maximum over pairs)."""
        xs = [x / self.temp for x in (value or self.value).values()]
        m = max(xs)
        return self.temp * (m + math.log(sum(math.exp(x - m) for x in xs)))

    def with_card(self, c: Card, v: float) -> tuple[dict[str, float], dict[str, float]]:
        """Pair values and probabilities after taking a card."""
        value = {p: _top(self.worths[p] + [_worth(c, v, p, self.fixes)]) for p in PAIRS}
        return value, _softmax(value, self.temp)


def _top(xs: list[float]) -> float:
    return sum(sorted(xs, reverse=True)[:DECK_SPELLS])


def _softmax(value: dict[str, float], temp: float) -> dict[str, float]:
    m = max(value.values())
    e = {p: math.exp((x - m) / temp) for p, x in value.items()}
    total = sum(e.values())
    return {p: x / total for p, x in e.items()}


def lanes(ratings: Ratings, picks: list[str]) -> Lanes:
    fixes = {k for p in picks for c in [ratings.get(p)] if c.makes and len(c.makes) >= 2
             for k in c.makes}
    worths: dict[str, list[float]] = {p: [] for p in PAIRS}
    for name in picks:
        c = ratings.get(name)
        v = _value(ratings, c)
        if v is None:
            continue
        for p in PAIRS:
            w = _worth(c, v, p, fixes)
            if w > 0:
                worths[p].append(w)
    value = {p: _top(worths[p]) for p in PAIRS}
    temp = spread(len(picks))
    return Lanes(worths, value, _softmax(value, temp), temp, fixes)


def in_deck(ratings: Ratings, c: Card, prob: dict[str, float]) -> float:
    """Rough chance a card you've taken ends up in your deck."""
    if c.makes or c.unsupported == "land":
        return 0.0
    fit = sum(q for p, q in prob.items() if all(k in p for k in c.colors))
    return fit * (1.0 if (c.z or 0) >= PLAYABLE_Z else UNPLAYABLE_IN_DECK)


def pool_synergy(ratings: Ratings, name: str, picks: list[str],
                 prob: dict[str, float]) -> tuple[float, list[tuple[str, float]]]:
    """The pick's synergy with everything you've taken, on the win-rate scale,
    and each pool card's share of it (before counting whether the pick itself
    makes the deck)."""
    parts: dict[str, float] = {}
    for p in picks:
        e = ratings.syn(name, p)
        if e:
            parts[p] = parts.get(p, 0.0) + SYNERGY_WEIGHT * DRAWN_TOGETHER * e * in_deck(
                ratings, ratings.get(p), prob)
    return sum(parts.values()), sorted(parts.items(), key=lambda kv: -abs(kv[1]))


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def advise(ratings: Ratings, pack: list[str], picks: list[str],
           pick_number: int = 0) -> list[Advice]:
    """The pack, best pick first. A pick's score is how much it raises the
    expected value of the deck you'll end up with (``Lanes.expected``): its
    own rating counts in full in every pair it fits, and the pairs your best
    cards already point at are the likeliest. A bomb early therefore pulls the
    next picks toward its colors in proportion to how good it is."""
    ln = lanes(ratings, picks)
    now = ln.expected()
    likely = max(ln.prob, key=ln.prob.get)
    splash = {k: p for p in picks for c in [ratings.get(p)] for v in [_value(ratings, c)]
              if v is not None and v >= SPLASH_BAR for k in c.colors}
    out = []
    for name in pack:
        c = ratings.get(name)
        why = []
        if c.makes and len(c.makes) >= 2:
            out.append(_land(c, ln, splash))
            continue
        v = _value(ratings, c)
        if v is None:
            why.append(f"Not simulated ({c.unsupported})." if c.unsupported else
                       "Not in the set data.")
            out.append(Advice(c, None, why))
            continue
        value, prob = ln.with_card(c, v)
        fit = sum(q for p, q in prob.items() if all(k in p for k in c.colors))
        # A card that wouldn't make any deck adds nothing; still take the best of those.
        score = ln.expected(value) - now + TIEBREAK * v * fit
        blend = ratings.experts and c.ez is not None
        why.append(f"{_pts(v)} pts vs. the average card"
                   + (" (with the experts averaged in)." if blend else "."))
        splashed = sum(q for p, q in prob.items() if not all(k in p for k in c.colors)
                       and _worth(c, v, p, ln.fixes) > 0)
        if not c.colors:
            why.append("Colorless: fits any deck.")
        elif picks:
            note = f"In your final colors {_pct(fit)} of the time"
            if splashed >= 0.05:
                note += f", splashed {_pct(splashed)}"
            why.append(note + f": adds {_pts(score)} pts to your likely deck.")

        bonus, shown = 0.0, None
        for pair, q in prob.items():
            if pair in c.pairs and c.pairs[pair][1] >= PAIR_MIN_GAMES and c.sim is not None \
                    and all(k in pair for k in c.colors):
                w, g = c.pairs[pair]
                in_pair = (w + PAIR_PRIOR_GAMES * c.sim) / (g + PAIR_PRIOR_GAMES)
                delta = ((in_pair - ratings.pair_mean.get(pair, ratings.sim_mean))
                         - (c.sim - ratings.sim_mean))
                bonus += PAIR_WEIGHT * q * delta
                if pair == likely:
                    shown = (pair, w, g)
        score += bonus
        if shown and abs(bonus) >= 0.001:
            pair, w, g = shown
            why.append(f"In simulated {pair} decks: {100 * w / g:.1f}% ({g:,} games), "
                       f"{_pts(bonus)} pts for your likely pairs.")
        elif len(c.colors) == 1 and c.pairs:
            best = max(((p, (r[0] + 100 * ratings.sim_mean) / (r[1] + 100))
                        for p, r in c.pairs.items() if r[1] >= PAIR_MIN_GAMES),
                       key=lambda x: x[1], default=None)
            if best:
                why.append(f"Best in {PAIR_NAMES.get(best[0], best[0])} ({best[0]}).")

        syn, parts = pool_synergy(ratings, name, picks, ln.prob)
        syn *= fit
        score += syn
        if abs(syn) >= 0.001:
            names = ", ".join(p for p, x in parts[:3] if x * syn > 0)
            why.append(f"Synergy with your pool: {_pts(syn)} pts ({names}).")

        if c.removal:
            why.append("Removal.")
        if c.expert_grades:
            hosts = ", ".join(f"{h} {g}" for h, g in c.expert_grades)
            why.append(f"Experts: {c.ex_grade} ({hosts}).")
        if pick_number and c.ata and len(pack) > 8 and c.ata >= pick_number + 8.5:
            why.append(f"Usually taken around pick {c.ata:.0f}: may come back.")
        out.append(Advice(c, score, why))
    return sorted(out, key=lambda a: -math.inf if a.score is None else a.score, reverse=True)


def _land(c: Card, ln: Lanes, splash: dict[str, str]) -> Advice:
    """A land that taps for two or more colors: worth a little in the pairs
    it covers, more where it also lets you splash a strong card you've taken."""
    made = set(c.makes)
    score, splashes = 0.0, set()
    for p, q in ln.prob.items():
        extra = {k for k in made - set(p) if k in splash}
        if set(p) <= made:
            score += q * (DUAL_LAND + (SPLASH_LAND - DUAL_LAND if extra else 0.0))
        elif made & set(p) and extra:
            score += q * SPLASH_LAND
        else:
            continue
        splashes |= extra
    fit = sum(q for p, q in ln.prob.items() if set(p) <= made)
    why = [f"Land ({c.makes}): taps for both your final colors {_pct(fit)} of the time."]
    for k in sorted(splashes):
        why.append(f"Lets you splash {splash[k]}.")
    return Advice(c, score, why)
