"""Which card to take: the simulator's card win rates and 17Lands' real ones,
blended, then adjusted for the cards you already have.

A pick's score, in win-rate points against the average card:

* the card's own rating (below);
* colors: a penalty per color outside your two, heavier for a card needing two
  or more pips of it, halved when your pool already fixes for that color. How
  much it bites grows with the picks you've made and how settled they are,
  and into pack two an off-color card also loses most of its edge over the
  average card, since you're unlikely to play it;
* your pair: how much better or worse the card does in simulated decks of
  your two colors than it does everywhere;
* synergy with your whole pool: the card's pair interaction (``limited.synergy``)
  with every card you've taken, each weighted by how likely that card is to
  make your deck, times the chance the two are drawn in the same game, at half
  weight (simulated pair effects only loosely match real ones);
* playables: when your colors are short of a deck's worth of playables for the
  picks that are left, on-color playables gain;
* lands: a dual land in your colors is worth a late pick, more when it lets you
  splash a strong card you've taken.

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
LANE_PENALTY = 0.035         # per off-color color, once fully committed
COMMIT_PICKS = 14            # picks until color matters fully
PAIR_MIN_GAMES = 150
PAIR_PRIOR_GAMES = 300       # a card's record in one pair is shrunk toward its overall one
PAIR_WEIGHT = 0.5            # how much of that difference counts
DRAWN_TOGETHER = 0.4         # chance a deck card is drawn in a game you draw the pick
SYNERGY_WEIGHT = 0.5         # simulated pair effects track real ones only loosely (HOB r=+0.24)
LATE_PICKS = 28              # by here an off-color card rarely makes the deck ...
LATE_DISCOUNT = 0.6          # ... so this much of its edge is gone (less if you can splash it)
UNDECIDED_IN_DECK = 0.5      # chance a pool card makes the deck before you have a lane
OFF_LANE_IN_DECK = 0.1       # ... once you're settled, for a card outside it
PLAYABLE_Z = -1.0            # rated at least this (about a C-) counts as a playable
UNPLAYABLE_IN_DECK = 0.3
DECK_PLAYABLES = 23
TOTAL_PICKS = 42             # three packs of 14
ON_LANE_SHARE = 0.3          # of the picks left, the share that will be on-color playables
NEED_BONUS = 0.03            # most a short pool adds to an on-color playable
DUAL_LAND = -0.025           # a dual in your colors: about a late playable
SPLASH_LAND = -0.012         # a dual that lets you splash a strong card you have
SPLASH_Z = 1.0
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
        if "any color" in clause:
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


def _fits(c: Card, top: list[str]) -> bool:
    return len(top) == 2 and all(k in top for k in c.colors)


def in_deck(ratings: Ratings, c: Card, top: list[str], commit: float) -> float:
    """Rough chance a card you've taken ends up in your deck."""
    if c.makes or c.unsupported == "land":
        return 0.0
    fit = 1.0 if not c.colors or _fits(c, top) else OFF_LANE_IN_DECK
    chance = commit * fit + (1 - commit) * UNDECIDED_IN_DECK
    return chance * (1.0 if (c.z or 0) >= PLAYABLE_Z else UNPLAYABLE_IN_DECK)


def pool_synergy(ratings: Ratings, name: str, picks: list[str], top: list[str],
                 commit: float) -> tuple[float, list[tuple[str, float]]]:
    """The pick's synergy with everything you've taken, on the win-rate scale,
    and each pool card's share of it."""
    parts: dict[str, float] = {}
    for p in picks:
        e = ratings.syn(name, p)
        if e:
            parts[p] = parts.get(p, 0.0) + SYNERGY_WEIGHT * DRAWN_TOGETHER * e * in_deck(
                ratings, ratings.get(p), top, commit)
    return sum(parts.values()), sorted(parts.items(), key=lambda kv: -abs(kv[1]))


def _playables(ratings: Ratings, picks: list[str], top: list[str]) -> int:
    return sum(1 for p in picks for c in [ratings.get(p)]
               if not c.makes and c.unsupported != "land" and c.est is not None
               and (c.z or 0) >= PLAYABLE_Z and (not c.colors or _fits(c, top)))


def advise(ratings: Ratings, pack: list[str], picks: list[str],
           pick_number: int = 0) -> list[Advice]:
    """The pack, best pick first."""
    weight, top = lane(ratings, picks)
    commit = commitment(weight, len(picks))
    pair = "".join(k for k in "WUBRG" if k in top) if len(top) == 2 else ""
    fixes = {k for p in picks for c in [ratings.get(p)] if c.makes and len(c.makes) >= 2
             for k in c.makes}
    splash = {k: p for p in picks for c in [ratings.get(p)]
              if (c.z or 0) >= SPLASH_Z and not c.makes for k in c.colors if k not in top}
    have = _playables(ratings, picks, top)
    left = max(0, TOTAL_PICKS - len(picks))
    need = min(1.0, max(0.0, (DECK_PLAYABLES - have - ON_LANE_SHARE * left) / DECK_PLAYABLES))
    out = []
    for name in pack:
        c = ratings.get(name)
        why = []
        if c.makes and len(c.makes) >= 2:
            out.append(_land(c, top, pair, commit, splash))
            continue
        blend = ratings.experts and c.ez is not None
        if c.est is None and not blend:
            why.append(f"Not simulated ({c.unsupported})." if c.unsupported else
                       "Not in the set data.")
            out.append(Advice(c, None, why))
            continue
        if c.est is None:
            base = c.ez * ratings.sd
        elif blend:
            # Half the card's own rating, half the experts' (on the same scale).
            base = ((c.est - ratings.mean) / ratings.sd + c.ez) / 2 * ratings.sd
        else:
            base = c.est - ratings.mean
        score = base
        why.append(f"{_pts(base)} pts vs. the average card"
                   + (" (with the experts averaged in)." if blend else "."))

        off = [k for k in c.colors if k not in top] if len(top) == 2 else []
        if off and commit > 0:
            # Late on, an off-color card is mostly a card you won't play.
            late = LATE_DISCOUNT * min(1.0, len(picks) / LATE_PICKS) * commit
            penalty = max(0.0, base) * late * (0.5 if all(k in fixes for k in off) else 1.0)
            for k in off:
                heavy = 1 + 0.5 * max(0.0, c.pips.get(k, 1) - 1)
                penalty += LANE_PENALTY * commit * heavy * (0.5 if k in fixes else 1.0)
            score -= penalty
            note = (f"{'One color' if len(off) == 1 else 'Both colors'} outside your {pair} "
                    f"lane: −{100 * penalty:.1f} pts this far in")
            if any(c.pips.get(k, 1) >= 2 for k in off):
                note += " (heavy on off-color mana)"
            elif all(k in fixes for k in off):
                note += " (your pool fixes for it)"
            why.append(note + ".")
        elif c.colors and top and all(k in top for k in c.colors) and picks:
            why.append(f"Fits your {''.join(top)} picks.")
        elif not c.colors:
            why.append("Colorless: fits any deck.")

        if pair and pair in c.pairs and c.pairs[pair][1] >= PAIR_MIN_GAMES and c.sim is not None:
            w, g = c.pairs[pair]
            in_pair = (w + PAIR_PRIOR_GAMES * c.sim) / (g + PAIR_PRIOR_GAMES)
            delta = ((in_pair - ratings.pair_mean.get(pair, ratings.sim_mean))
                     - (c.sim - ratings.sim_mean))
            bonus = PAIR_WEIGHT * commit * delta
            score += bonus
            if abs(bonus) >= 0.001:
                why.append(f"In simulated {pair} decks: {100 * w / g:.1f}% ({g:,} games), "
                           f"{_pts(bonus)} pts for your pair.")
        elif len(c.colors) == 1 and c.pairs:
            best = max(((p, (r[0] + 100 * ratings.sim_mean) / (r[1] + 100))
                        for p, r in c.pairs.items() if r[1] >= PAIR_MIN_GAMES),
                       key=lambda x: x[1], default=None)
            if best:
                why.append(f"Best in {PAIR_NAMES.get(best[0], best[0])} ({best[0]}).")

        syn, parts = pool_synergy(ratings, name, picks, top, commit)
        score += syn
        if abs(syn) >= 0.001:
            names = ", ".join(p for p, v in parts[:3] if v * syn > 0)
            why.append(f"Synergy with your pool: {_pts(syn)} pts ({names}).")

        if need and commit and (not c.colors or _fits(c, top)) and (c.z or 0) >= PLAYABLE_Z:
            bonus = NEED_BONUS * need * commit
            score += bonus
            if bonus >= 0.0005:
                why.append(f"You have {have} {pair} playables with {left} picks left: "
                           f"{_pts(bonus)} pts.")

        if c.removal:
            why.append("Removal.")
        if blend:
            hosts = ", ".join(f"{h} {g}" for h, g in c.expert_grades)
            why.append(f"Experts: {c.ex_grade} ({hosts}).")
        if pick_number and c.ata and len(pack) > 8 and c.ata >= pick_number + 8.5:
            why.append(f"Usually taken around pick {c.ata:.0f}: may come back.")
        out.append(Advice(c, score, why))
    return sorted(out, key=lambda a: -math.inf if a.score is None else a.score, reverse=True)


def _land(c: Card, top: list[str], pair: str, commit: float,
          splash: dict[str, str]) -> Advice:
    """A dual land: worth a late pick in your colors, more if it enables a splash."""
    made = set(c.makes)
    if len(top) < 2 or commit == 0:
        return Advice(c, DUAL_LAND - LANE_PENALTY,
                      [f"Dual land ({c.makes}): take it late once you know your colors."])
    if made <= set(top):
        return Advice(c, DUAL_LAND, [f"Dual land in your {pair} colors: smoother mana."])
    extra = [k for k in made if k not in top]
    if len(made & set(top)) == 1 and extra[0] in splash:
        return Advice(c, SPLASH_LAND, [f"Taps for your colors and {extra[0]}: "
                                       f"lets you splash {splash[extra[0]]}."])
    return Advice(c, None, [f"Dual land ({c.makes}) outside your {pair} colors."])
