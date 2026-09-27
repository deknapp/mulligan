"""Which card to take: the simulator's card win rates and 17Lands' real ones,
blended, plus how well each card fits the colors you've drafted.

The same rules as the website's pick helper (``site/tools.js``), with one
difference: the website swaps in a 17Lands number once it has 500 games, while
this blends the two. The simulator is treated as a prior worth
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

    def get(self, name: str) -> Card:
        return self.cards.get(name) or Card(name)


def build_ratings(sim: dict | None, real: dict | None) -> Ratings:
    cards: dict[str, Card] = {}
    sim_mean = sim["mean"] if sim else 0.5
    prior = sim["prior"] if sim else 200
    for c in (sim or {}).get("cards", []):
        card = Card(c["n"], c.get("c") or "", c.get("r", ""), bool(c.get("rm")),
                    unsupported=c.get("un"), ata=c.get("ata") or None,
                    pairs=(sim.get("pc") or {}).get(c["n"], {}))
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
    return Ratings(cards, mean, sim_mean, shift, used, real_rows, (sim or {}).get("run", ""),
                   (real or {}).get("fetched", ""), (real or {}).get("start", ""))


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


def advise(ratings: Ratings, pack: list[str], picks: list[str],
           pick_number: int = 0) -> list[Advice]:
    """The pack, best pick first."""
    _, top = lane(ratings, picks)
    commit = min(1.0, len(picks) / COMMIT_PICKS)
    pair = "".join(k for k in "WUBRG" if k in top) if len(top) == 2 else ""
    out = []
    for name in pack:
        c = ratings.get(name)
        why = []
        if c.est is None:
            why.append(f"Not simulated ({c.unsupported})." if c.unsupported else
                       "Not in the set data.")
            out.append(Advice(c, None, why))
            continue
        base = c.est - ratings.mean
        off = sum(k not in top for k in c.colors) if len(top) == 2 else 0
        penalty = off * commit * LANE_PENALTY
        why.append(f"{_pts(base)} pts vs. the average card.")
        if off and commit > 0:
            why.append(f"{'One color' if off == 1 else 'Both colors'} outside your {pair} "
                       f"lane: −{100 * penalty:.1f} pts this far in.")
        elif c.colors and top and all(k in top for k in c.colors) and picks:
            why.append(f"Fits your {''.join(top)} picks.")
        elif not c.colors:
            why.append("Colorless: fits any deck.")
        if pair and pair in c.pairs and c.pairs[pair][1] >= PAIR_MIN_GAMES:
            w, g = c.pairs[pair]
            why.append(f"In simulated {pair} decks: {100 * w / g:.1f}% ({g:,} games).")
        elif len(c.colors) == 1 and c.pairs:
            best = max(((p, (r[0] + 100 * ratings.sim_mean) / (r[1] + 100))
                        for p, r in c.pairs.items() if r[1] >= PAIR_MIN_GAMES),
                       key=lambda x: x[1], default=None)
            if best:
                why.append(f"Best in {PAIR_NAMES.get(best[0], best[0])} ({best[0]}).")
        if c.removal:
            why.append("Removal.")
        if pick_number and c.ata and len(pack) > 8 and c.ata >= pick_number + 8.5:
            why.append(f"Usually taken around pick {c.ata:.0f}: may come back.")
        out.append(Advice(c, base - penalty, why))
    return sorted(out, key=lambda a: -math.inf if a.score is None else a.score, reverse=True)
