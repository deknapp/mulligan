# ruff: noqa: E501  (the page template below is HTML/CSS, wrapped by hand)
"""The sealed guide: the best decks in a sealed pool, from real data only.

Card strength comes from two sources and nothing else:

* 17Lands games-in-hand win rate (GIH WR), as a log-odds edge over the set's
  average card, shrunk when the card has few games;
* the podcast hosts' grades (``mulligan experts``), each host's scale aligned,
  averaged, and mapped onto the same log-odds scale by a straight-line fit
  against the cards that have both. Experts carry the cards 17Lands has not
  measured well yet; past a few hundred games the real win rate dominates.

No simulator ratings are used. Builds are made for every color pair, plus
splashes of single-pip bombs, and ranked by the shortcut simulator
(``fastsim``), which plays out thousands of draws with those card values.
The top builds then go to Claude for a final review: it reads the decklists
with every card's text, numbers and the experts' takes, and says which build
to register and why, with any swaps from the sideboard.

``write_html`` turns the result into one self-contained page.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from . import fastsim

COLORS = "WUBRG"
COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
BASIC_FOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
EXPERT_PRIOR_GAMES = 500   # a card's expert grade weighs as much as this many 17Lands games
UNRATED_EDGE = -0.15


@dataclass
class CardInfo:
    name: str
    cost: str = ""
    mv: int = 0
    pips: dict[str, float] = field(default_factory=dict)   # color -> pips (hybrid split)
    types: list[str] = field(default_factory=list)
    subtypes: list[str] = field(default_factory=list)
    power: int | None = None
    keywords: set[str] = field(default_factory=set)
    rarity: str = ""
    oracle: str = ""
    makes: set[str] = field(default_factory=set)          # lands: colors produced
    gih: float | None = None
    games: int = 0
    sealed_gih: float | None = None
    sealed_games: int = 0
    lens: int = 0               # Claude's Sealed shift, -2..+2
    lens_why: str = ""
    real_edge: float | None = None
    grade: str | None = None
    expert_edge: float | None = None
    takes: list[str] = field(default_factory=list)
    edge: float = 0.0

    @property
    def is_land(self) -> bool:
        return "Land" in self.types

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.types

    @property
    def colors(self) -> set[str]:
        return {c for c, n in self.pips.items() if n > 0}

    def castable(self, colors: set[str]) -> bool:
        return all(any(p in colors for p in sym.split("/")) for sym in self._colored_symbols())

    def _colored_symbols(self) -> list[str]:
        out = []
        for sym in re.findall(r"\{([^}]+)\}", self.cost):
            parts = [p for p in sym.split("/") if p in COLORS]
            if parts and not any(p.isdigit() or p == "C" for p in sym.split("/")):
                out.append("/".join(parts))
        return out

    def off_color_pips(self, colors: set[str]) -> Counter:
        off: Counter = Counter()
        for sym in self._colored_symbols():
            if not any(p in colors for p in sym.split("/")):
                off[sym.split("/")[0]] += 1
        return off

    def evidence(self) -> str:
        bits = []
        if self.gih is not None:
            bits.append(f"17Lands GIH {self.gih:.1%} over {self.games:,} games")
        if self.sealed_gih is not None:
            bits.append(f"Sealed {self.sealed_gih:.1%} over {self.sealed_games:,}")
        if self.lens:
            bits.append(f"Sealed lens {self.lens:+d}: {self.lens_why}")
        if self.grade:
            bits.append(f"experts {self.grade}")
        return "; ".join(bits) or "no data (counted as average)"


def _mana_value(cost: str) -> int:
    total = 0
    for sym in re.findall(r"\{([^}]+)\}", cost):
        if sym.isdigit():
            total += int(sym)
        elif sym not in ("X", "Y"):
            total += 1
    return total


def _pips(cost: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym in re.findall(r"\{([^}]+)\}", cost):
        parts = [p for p in sym.split("/") if p in COLORS]
        for p in parts:
            out[p] = out.get(p, 0.0) + 1.0 / len(parts)
    return out


def _land_makes(entry: dict) -> set[str]:
    text = entry.get("oracle", "")
    if "any color" in text or "chosen color" in text:
        return set(COLORS)
    made = set()
    for line in text.splitlines():
        if "Add " in line:
            made |= {p for sym in re.findall(r"\{([^}]+)\}", line.split("Add ", 1)[1])
                     for p in sym.split("/") if p in COLORS}
    for c, basic in BASIC_FOR.items():
        if basic in entry.get("subtypes", []):
            made.add(c)
    return made


def _scryfall(name: str) -> dict | None:
    """Card facts for a pool card the compiled set lacks (bonus-sheet and
    special-guest cards), from Scryfall, cached."""
    import urllib.parse
    import urllib.request

    from .paths import cache_dir
    path = cache_dir("scryfall", re.sub(r"[^A-Za-z0-9]+", "_", name) + ".json")
    if path.exists():
        return json.loads(path.read_text()) or None
    url = "https://api.scryfall.com/cards/named?exact=" + urllib.parse.quote(name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "mulligan/0.1",
                                                       "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            card = json.load(response)
    except Exception:  # noqa: BLE001 - offline or unknown: the card is left out
        return None
    face = (card.get("card_faces") or [card])[0]
    line = face.get("type_line", card.get("type_line", ""))
    main, _, sub = line.partition(" — ")
    entry = {"cost": face.get("mana_cost", ""), "types": main.split(),
             "subtypes": sub.split(), "rarity": card.get("rarity", ""),
             "power": int(face["power"]) if str(face.get("power", "")).isdigit() else None,
             "keywords": card.get("keywords", []),
             "oracle": face.get("oracle_text", "")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry))
    return entry


def _expert_grades(experts: dict | None) -> dict[str, tuple[float, str, list[str]]]:
    """card -> (host-aligned average grade index, letter, takes)."""
    from .pick_advice import LETTERS
    cards = (experts or {}).get("cards") or {}
    by_host: dict[str, list[int]] = {}
    for x in cards.values():
        for g in x.get("grades", []):
            if g.get("grade") in LETTERS:
                by_host.setdefault(f"{g['show']}|{g['host']}", []).append(
                    LETTERS.index(g["grade"]))
    pooled = [i for xs in by_host.values() for i in xs]
    center = sum(pooled) / len(pooled) if pooled else 0.0
    offset = {h: center - sum(xs) / len(xs) for h, xs in by_host.items()}
    out = {}
    for name, x in cards.items():
        idx = [LETTERS.index(g["grade"]) + offset[f"{g['show']}|{g['host']}"]
               for g in x.get("grades", []) if g.get("grade") in LETTERS]
        if not idx:
            continue
        mean = sum(idx) / len(idx)
        letter = LETTERS[max(0, min(len(LETTERS) - 1, round(mean)))]
        takes = [f"{t['show']} ({t['host']}): {t['take']}" for t in x.get("takes", [])]
        out[name] = (mean, letter, takes)
    return out


def _ratings(set_code: str, fmt: str) -> dict[str, tuple[float, int]]:
    """GIH WR per card: 17Lands' full game files when published, else live."""
    from .validation import seventeen
    try:
        return seventeen.game_data_ratings(set_code, fmt)
    except Exception:  # noqa: BLE001 - game files not out yet
        try:
            return seventeen.fetch_ratings(set_code, fmt)
        except Exception:  # noqa: BLE001 - offline
            return {}


def card_infos(set_code: str, fmt: str = "Sealed", names: list[str] | None = None,
               lens: bool = True) -> tuple[dict[str, CardInfo], dict]:
    """Every card of the set with its real-data edge; plus notes on the sources.

    For Sealed, Premier Draft numbers are the base (capped at
    SEALED_PRIOR_GAMES games' worth of evidence) and real Sealed numbers pull
    each card toward its Sealed result as they come in; the Sealed lens nudges
    what neither has settled."""
    from .cards.sets import load_set
    from .pick_advice import grade_points, load_experts
    data = load_set(set_code)
    draft = _ratings(set_code, "PremierDraft")
    sealed = _ratings(set_code, "Sealed") if fmt == "Sealed" else {}
    dv = fastsim.CardValues.from_gih(draft) if draft else None
    sv = fastsim.CardValues.from_gih(sealed) if sealed else None
    shifts = load_lens(set_code) if lens and fmt == "Sealed" else {}
    experts = _expert_grades(load_experts(set_code))
    infos: dict[str, CardInfo] = {}
    for name in set(data.entries) | set(names or []):
        e = data.entries.get(name) or _scryfall(name)
        if e is None:
            continue   # nothing known about the card: leave it out of every build
        info = CardInfo(name, cost=e.get("cost", ""), mv=_mana_value(e.get("cost", "")),
                        pips=_pips(e.get("cost", "")), types=list(e.get("types", [])),
                        subtypes=list(e.get("subtypes", [])),
                        power=e.get("power") if isinstance(e.get("power"), int) else None,
                        keywords={k.lower() for k in e.get("keywords", [])},
                        rarity=e.get("rarity", ""), oracle=e.get("oracle", ""))
        if info.is_land:
            info.makes = _land_makes(e)
        weight = 0.0
        total = 0.0
        if name in draft:
            info.gih, info.games = draft[name]
            w = min(info.games, SEALED_PRIOR_GAMES) if sealed else info.games
            total += w * dv.values[name]
            weight += w
        if name in sealed:
            info.sealed_gih, info.sealed_games = sealed[name]
            total += info.sealed_games * sv.values[name]
            weight += info.sealed_games
        if weight:
            info.real_edge = total / weight
            info.games = int(weight)
        if name in experts:
            mean, info.grade, info.takes = experts[name]
            info.expert_edge = grade_points(mean)   # win-rate points for now
        if name in shifts:
            info.lens, info.lens_why = shifts[name]
        infos[name] = info
    # Put expert points on the log-odds scale: fit edge ≈ a + b·points on
    # well-measured cards.
    both = [(i.expert_edge, i.real_edge) for i in infos.values()
            if i.expert_edge is not None and i.real_edge is not None and i.games >= 1000]
    a, b = 0.0, 0.03
    if len(both) >= 20:
        mx = sum(x for x, _ in both) / len(both)
        my = sum(y for _, y in both) / len(both)
        sxx = sum((x - mx) ** 2 for x, _ in both)
        b = sum((x - mx) * (y - my) for x, y in both) / sxx if sxx else 0.03
        a = my - b * mx
    for i in infos.values():
        if i.expert_edge is not None:
            i.expert_edge = a + b * i.expert_edge
        n = i.games if i.real_edge is not None else 0
        real = i.real_edge or 0.0
        if i.expert_edge is not None:
            i.edge = (n * real + EXPERT_PRIOR_GAMES * i.expert_edge) / (n + EXPERT_PRIOR_GAMES)
        elif i.real_edge is not None:
            i.edge = real
        else:
            # Neither 17Lands nor the hosts rate it: bonus-sheet and guest cards
            # nobody drafts around. Count it as filler, not as an average card.
            i.edge = UNRATED_EDGE
        if i.lens:
            i.edge += LENS_STEP * i.lens * LENS_FADE_GAMES / (LENS_FADE_GAMES + i.sealed_games)
    source = "17Lands FRA Premier Draft" + (
        f" + Sealed ({len(sealed)} cards)" if sealed else "")
    notes = {"values": source.replace("FRA", set_code.upper()),
             "expert_fit": f"edge = {a:+.3f} + {b:.4f} × grade points ({len(both)} cards)",
             "experts": sum(1 for i in infos.values() if i.grade),
             "rated": sum(1 for i in infos.values() if i.gih is not None),
             "lens": sum(1 for i in infos.values() if i.lens),
             "mean_gih": dv.mean_gih if dv else 0.55}
    return infos, notes


@dataclass
class Build:
    colors: str                 # e.g. "UB" or "UB+r"
    spells: list[CardInfo]
    lands: list[str]            # land names, basics included
    splash: list[CardInfo] = field(default_factory=list)
    win_rate: float = 0.0       # shortcut simulator, vs an average deck
    score: float = 0.0
    parts: dict = field(default_factory=dict)   # the score, term by term
    notes: list[str] = field(default_factory=list)
    label: str = ""             # e.g. "Claude's build"

    @property
    def creatures(self) -> int:
        return sum(1 for c in self.spells if c.is_creature)

    def curve(self) -> list[int]:
        out = [0] * 7
        for c in self.spells:
            out[min(max(c.mv, 1), 7) - 1] += 1
        return out

    def decklist(self) -> str:
        counts = Counter(c.name for c in self.spells) + Counter(self.lands)
        return "Deck\n" + "".join(f"{n} {name}\n" for name, n in counts.items())


# ------------------------------------------------------------------ roles
#
# What a card does in a deck, read from its rules text, so the score can ask
# "does this deck have answers and a way to win" and not only "are its cards
# good on average".

REMOVAL = [
    r"destroy target (creature|nonland permanent|permanent|artifact or creature"
    r"|creature or planeswalker|attacking|blocking|tapped creature)",
    r"exile target (creature|nonland permanent|permanent|creature or planeswalker"
    r"|attacking|blocking|tapped creature)",
    r"deals? (\d+|x|that much) damage to (target creature|any target|target attacking"
    r"|target blocking|target creature or planeswalker|each creature)",
    r"fights? (target|another target|up to one target)",
    r"deals damage equal to its power to (target|another target|up to one target)",
    r"(target|each) opponent sacrifices",
    r"target creature (an opponent controls )?gets -\d+/-\d+",
    r"gets -x/-x",
    r"enchanted creature (can't attack or block|loses all abilities|doesn't untap)",
    r"destroy (all|each) (creatures|creature)",
    r"puts? (it|that permanent) on (their choice of )?the (top or bottom|bottom)",
    r"on the bottom of its owner's library",
]
SOFT_REMOVAL = [
    r"return target (creature|nonland permanent)[^.]{0,50}to its owner's hand",
    r"counter target (spell|creature spell|noncreature spell)",
    r"stun counter",
]
ENGINE = [
    r"(whenever|at the beginning of)[^.]{0,80}(draw a card|draws? \w+ cards|create)",
]
JACE_ENGINE_CARDS = 4       # this many empower-Jace cards make a Jace that wins games
EVASION = {"flying", "menace", "trample"}
# Set mechanics that reward playing several of their cards. Generic words
# ("graveyard", "+1/+1 counter") appear on too many unrelated cards to count.
THEMES = {
    "Jace engine": r"empower jace|behold a jace|\bjace\b you control",
    "Prepare": r"\bprepare",
    "Spells matter": r"whenever you cast (a|an|your) (noncreature|instant)",
    "Burn": r"deals? (\d+|x) damage to (any target|each opponent|target opponent|each player)"
            r"|damage to each opponent|noncombat damage",
}
# A theme counts only with a card that rewards it, not just cards that do it.
THEME_PAYOFF = {"Burn": r"noncombat damage|was dealt damage this turn"}
THEME_ACTIVE = 5            # cards needed before a theme's members stop counting as filler

# Thresholds, in log-odds over the set's average card. In FRA the top 5% of
# cards sit above +0.21 (rares and mythics at 65%+ GIH WR), the bottom quarter
# below -0.11.
BOMB_EDGE = 0.20
BOMB_GRADE = "A-"
FILLER_EDGE = -0.08         # about two win-rate points below the average card
FILLER_GRADE = "D+"

# Score weights: judgment calls until there are real FRA sealed decks to fit
# them to. Each is shown, term by term, on the page.
BOMB_BONUS = 0.5            # extra per unit of edge above BOMB_EDGE - 0.05
FILLER_COST = 0.04          # the k-th filler card costs k times this
REMOVAL_EACH = 0.08         # per removal spell up to six: Sealed is slow and
REMOVAL_MORE = 0.04         # bomb-heavy, so answers matter; then less, up to ten
NO_WINCON = 0.30
ONE_WINCON = 0.10
THEME_EACH = 0.05           # per theme card past four, best theme only
THEME_CAP = 0.40
SPLASH_COST = 0.06          # per splashed card, on top of its mana
EXTRA_LAND_EDGE = -0.10     # an 18th land is worth about a -0.10 card
SPLASH_CANDIDATE = 0.20     # splash only bombs...
SPLASH_REMOVAL = 0.03       # ...or removal at least this good
MAX_SPLASH = 2


def _text(c: CardInfo) -> str:
    return c.oracle.lower().replace(c.name.lower(), "this")


def is_removal(c: CardInfo) -> float:
    """1 for removal, 0.5 for soft interaction (bounce, counters, stun), else 0."""
    text = _text(c)
    if any(re.search(p, text) for p in REMOVAL):
        return 1.0
    if any(re.search(p, text) for p in SOFT_REMOVAL):
        return 0.5
    return 0.0


def is_bomb(c: CardInfo) -> bool:
    from .pick_advice import LETTERS
    return c.edge >= BOMB_EDGE or (
        c.grade in LETTERS and LETTERS.index(c.grade) >= LETTERS.index(BOMB_GRADE)
        and (c.real_edge is None or c.games < 1000 or c.real_edge > 0.1))


def is_filler(c: CardInfo) -> bool:
    from .pick_advice import LETTERS
    if c.edge < FILLER_EDGE:
        return True
    # The hosts' low grade stands unless plenty of real games say otherwise.
    return (c.grade in LETTERS and LETTERS.index(c.grade) <= LETTERS.index(FILLER_GRADE)
            and (c.real_edge is None or c.games < 1000 or c.real_edge < 0))


def win_condition(c: CardInfo) -> str | None:
    """Why this card wins games on its own, if it does."""
    if is_bomb(c):
        return "bomb"
    if "Planeswalker" in c.types:
        return "planeswalker"
    if re.search(r"deals? x damage to (any target|target player|each opponent)", _text(c)):
        return "finisher"   # a Fireball ends games
    if c.is_creature:
        if (c.power or 0) >= 3 and (c.keywords & EVASION or "can't be blocked" in _text(c)):
            return "evasive threat"
        if (c.power or 0) >= 5:
            return "big threat"
    if (not {"Instant", "Sorcery"} & set(c.types)
            and any(re.search(p, _text(c)) for p in ENGINE) and c.edge > -0.02):
        return "engine"
    return None


def themes(c: CardInfo) -> list[str]:
    text = _text(c) + " " + " ".join(c.subtypes).lower()
    return [t for t, p in THEMES.items() if re.search(p, text)]


def deck_theme(spells: list[CardInfo]) -> tuple[str, int, set[str]]:
    """The deck's strongest set theme: (name, cards, member names)."""
    best = ("", 0, set())
    for theme in THEMES:
        members = [c for c in spells if theme in themes(c)]
        payoff = THEME_PAYOFF.get(theme)
        if payoff and not any(re.search(payoff, _text(c)) for c in members):
            continue
        if len(members) > best[1]:
            best = (theme, len(members), {c.name for c in members})
    return best


def score_deck(spells: list[CardInfo], splash: list[CardInfo], lands: int = 17) -> tuple[float, dict]:
    """The deck's score and its parts, all in log-odds-of-winning units."""
    quality = sum(c.edge for c in spells) + EXTRA_LAND_EDGE * max(0, lands - 17)
    bombs = [c for c in spells if is_bomb(c)]
    bomb = BOMB_BONUS * sum(max(0.0, c.edge - (BOMB_EDGE - 0.05)) for c in bombs)
    bomb += 0.05 * sum(1 for c in bombs if c.edge < BOMB_EDGE)   # expert-graded bombs
    theme, tn, members = deck_theme(spells)
    active = members if tn >= THEME_ACTIVE else set()
    # Removal is never filler, and neither is a working theme's card: its win
    # rate was measured mostly in decks without the theme.
    filler = [c for c in spells if is_filler(c) and not is_removal(c) and c.name not in active]
    n = len(filler)
    filler_cost = FILLER_COST * n * (n + 1) / 2
    removal = sum(is_removal(c) for c in spells)
    removal_bonus = REMOVAL_EACH * min(removal, 6) + REMOVAL_MORE * min(max(removal - 6, 0), 4)
    wincons = [(c.name, why) for c in spells if (why := win_condition(c))]
    jace = [c for c in spells if "empower jace" in _text(c)]
    if len(jace) >= JACE_ENGINE_CARDS:
        wincons.append((f"Jace engine ({len(jace)} empower cards)", "engine"))
    distinct = len({n for n, _ in wincons}) + sum(1 for _ in wincons) / 100
    wincon_cost = NO_WINCON if distinct < 1 else ONE_WINCON if distinct < 2 else 0.0
    theme_bonus = min(THEME_CAP, THEME_EACH * max(0, tn - 4))
    creatures = sum(1 for c in spells if c.is_creature)
    twos = sum(1 for c in spells if c.mv <= 2)
    tops = sum(1 for c in spells if c.mv >= 6)
    # Too few creatures loses to anything that attacks: steeper below ten.
    shape = (0.05 * max(0, 4 - twos) + 0.05 * max(0, tops - 3)
             + 0.08 * max(0, 13 - creatures) + 0.12 * max(0, 10 - creatures))
    splash_cost = SPLASH_COST * len(splash)
    total = (quality + bomb - filler_cost + removal_bonus - wincon_cost + theme_bonus
             - shape - splash_cost)
    return total, {
        "quality": quality, "bomb": bomb, "bombs": [c.name for c in bombs],
        "filler_cost": filler_cost, "filler": [c.name for c in filler],
        "removal": removal, "removal_bonus": removal_bonus,
        "removal_cards": [c.name for c in spells if is_removal(c)],
        "wincons": wincons, "wincon_cost": wincon_cost,
        "theme": theme, "theme_n": tn, "theme_bonus": theme_bonus,
        "shape": shape, "creatures": creatures, "twos": twos, "tops": tops,
        "splash_cost": splash_cost, "lands": lands, "total": total,
    }


def _search(candidates: list[CardInfo], allowed_splash: set[str], colors: set[str],
            n_spells: int, seed_order: list[CardInfo]) -> list[CardInfo]:
    """Hill-climb: start from the best cards, then make the single swap that
    most improves the score until none does."""
    def splashed(deck):
        return [c for c in deck if not c.castable(colors)]

    def value(deck):
        sp = splashed(deck)
        if len(sp) > MAX_SPLASH:
            return -1e9
        return score_deck(deck, sp, 40 - n_spells)[0]

    deck: list[CardInfo] = []
    for c in seed_order:
        if len(deck) == n_spells:
            break
        if c.castable(colors) or len(splashed(deck)) < MAX_SPLASH:
            deck.append(c)
    current = value(deck)
    for _ in range(60):
        best, move = current, None
        for i, out in enumerate(deck):
            for c in candidates:
                if c is out or (sum(1 for d in deck if d is c)):
                    continue
                trial = deck[:i] + [c] + deck[i + 1:]
                v = value(trial)
                if v > best + 1e-9:
                    best, move = v, trial
        if move is None:
            break
        deck, current = move, best
    return deck


def _lands_for(pool: list[CardInfo], pair: str, spells: list[CardInfo],
               splash: list[CardInfo], total: int) -> list[str]:
    colors = set(pair)
    all_colors = colors | {k for c in splash for k in c.off_color_pips(colors)}
    # Nonbasics only when they make two of the deck's colors (splash included):
    # a land that is one useful color, often tapped, is a worse basic.
    nonbasic = [c for c in pool if c.is_land and len(c.makes & all_colors) >= 2]
    nonbasic.sort(key=lambda c: (-len(c.makes & all_colors), -c.edge))
    lands = [c.name for c in nonbasic[:4]]
    for s in all_colors - colors:   # three sources per splashed color, four for two+ cards
        want = 3 + (sum(1 for c in splash if s in c.off_color_pips(colors)) >= 2)
        have = sum(1 for n in lands for c in pool if c.name == n and s in c.makes)
        lands += [BASIC_FOR[s]] * max(0, want - have)
    pips = Counter()
    for c in spells:
        for k, v in c.pips.items():
            if k in colors:
                pips[k] += v
    remaining = max(0, total - len(lands))
    weight = sum(pips.values()) or 1.0
    counts = {k: round(remaining * pips[k] / weight) for k in pair}
    while sum(counts.values()) > remaining:
        counts[max(counts, key=counts.get)] -= 1
    while sum(counts.values()) < remaining:
        counts[max(pair, key=lambda k: pips[k] / max(1, counts[k]))] += 1
    for k in pair:
        lands += [BASIC_FOR[k]] * counts[k]
    return lands


def _build(pool: list[CardInfo], pair: str, splash_color: str | None = None) -> Build | None:
    """The best deck in ``pair`` (optionally splashing ``splash_color``), at 17
    or 18 lands, whichever scores better."""
    colors = set(pair)
    spells = [c for c in pool if not c.is_land]
    main = [c for c in spells if c.castable(colors)]
    if len(main) < 16:
        return None
    candidates = list(main)
    if splash_color:
        extra = [c for c in spells if not c.castable(colors)
                 and c.castable(colors | {splash_color})
                 and sum(c.off_color_pips(colors).values()) == 1
                 and (c.edge >= SPLASH_CANDIDATE
                      or (is_removal(c) >= 1 and c.edge >= SPLASH_REMOVAL) or is_bomb(c))]
        if not extra:
            return None
        candidates += extra
    order = sorted(candidates, key=lambda c: -(c.edge + 0.05 * is_removal(c)
                                               + (0.1 if is_bomb(c) else 0)))
    best = None
    for n_spells in (23, 22):
        deck = _search(candidates, {splash_color} if splash_color else set(), colors,
                       n_spells, order)
        splash = [c for c in deck if not c.castable(colors)]
        total, parts = score_deck(deck, splash, 40 - n_spells)
        if best is None or total > best[0] + 1e-9:
            best = (total, deck, splash, parts, n_spells)
    total, deck, splash, parts, n_spells = best
    if splash_color and not splash:
        return None
    lands = _lands_for(pool, pair, deck, splash, 40 - n_spells)
    label = pair + ("+" + splash_color.lower() if splash else "")
    b = Build(label, deck, lands, splash, score=total, parts=parts)
    if parts["filler"]:
        b.notes.append(f"{len(parts['filler'])} filler")
    if parts["wincon_cost"] >= NO_WINCON:
        b.notes.append("no win condition")
    if parts["removal"] < 3:
        b.notes.append(f"only {parts['removal']:g} removal")
    return b


def _simulate(build: Build, games: int, seed: int) -> None:
    """The shortcut simulator's win rate vs an average deck, for reference."""
    cards = [(c.edge, False) for c in build.spells] + [(0.0, True)] * len(build.lands)
    edge = fastsim.deck_score(fastsim.Deck(cards), games, seed)
    base = fastsim.deck_score(fastsim.Deck([(0.0, False)] * 23 + [(0.0, True)] * 17),
                              games, seed)
    build.win_rate = fastsim._sigmoid(edge - base)


def pool_bombs(pool_names: list[str], infos: dict[str, CardInfo]) -> list[CardInfo]:
    seen = {n: infos[n] for n in pool_names if n in infos and not infos[n].is_land}
    return sorted((c for c in seen.values() if is_bomb(c)), key=lambda c: -c.edge)


def candidate_builds(pool_names: list[str], infos: dict[str, CardInfo],
                     games: int = 3000, seed: int = 0) -> tuple[list[Build], list[Build]]:
    """(every color pair's best build, the best builds overall with splashes)."""
    pool = [infos[n] for n in pool_names if n in infos]   # unknown cards are left out
    pairs = []
    for p in combinations(COLORS, 2):
        b = _build(pool, "".join(p))
        if b:
            pairs.append(b)
    pairs.sort(key=lambda b: -b.score)
    builds = list(pairs)
    for base in pairs[:5]:
        for s in COLORS:
            if s not in base.colors:
                b = _build(pool, base.colors, s)
                if b and b.score > base.score - 0.15:
                    builds.append(b)
    builds.sort(key=lambda b: -b.score)
    best, seen = [], set()
    for b in builds:
        key = frozenset(c.name for c in b.spells)
        if key not in seen:
            seen.add(key)
            best.append(b)
    for b in pairs + best:
        _simulate(b, games, seed)
    return pairs, best[:5]


def parse_custom(spec: dict, pool_names: list[str], infos: dict[str, CardInfo]) -> Build | None:
    """Claude's own build, if it gave one and every card is in the pool."""
    if not spec or not spec.get("spells"):
        return None
    have = Counter(pool_names)
    want = Counter(spec["spells"])
    if any(n not in infos or want[n] > have[n] for n in want):
        return None
    counts = Counter(spec["spells"])
    for item in spec.get("lands", []):
        counts[item["name"]] += int(item["count"])
    if not 38 <= sum(counts.values()) <= 42:
        return None
    return deck_from_counts(counts, infos, "Claude's build")


def deck_from_counts(counts: dict[str, int], infos: dict[str, CardInfo], label: str) -> Build:
    """A finished decklist (name -> copies) scored like the builder's decks."""
    spells = [infos[n] for n, k in counts.items() for _ in range(k)
              if n in infos and not infos[n].is_land]
    lands = [n for n, k in counts.items() for _ in range(k)
             if not (n in infos and not infos[n].is_land)]
    colors = {k for c in spells for k in c.colors}
    main = {k for k, _ in Counter(k for c in spells for k in c.colors).most_common(2)}
    splash = [c for c in spells if not c.castable(main)]
    total, parts = score_deck(spells, splash, len(lands))
    label_colors = "".join(k for k in COLORS if k in main) + (
        "+" + "".join(sorted(colors - main)).lower() if colors - main else "")
    b = Build(label_colors, spells, lands, splash, score=total, parts=parts, label=label)
    _simulate(b, 3000, 0)
    return b


# ------------------------------------------------------------------ sealed lens
#
# 17Lands numbers for a new set come from Premier Draft long before Sealed has
# enough games, and some cards are known to be better or worse in Sealed:
# games are slower and decks stronger, so removal, bombs, card advantage and
# fixing gain, while tempo, cheap aggression and draft-archetype payoffs
# lose. One Claude pass per set reads every card with that in mind and gives
# a shift from -2 to +2; it is saved in the repo, and it fades as real Sealed
# games for the card come in.

LENS_STEP = 0.04            # log-odds per step: +2 is about two win-rate points
LENS_FADE_GAMES = 1000      # real Sealed games at which a shift counts half
SEALED_PRIOR_GAMES = 1500   # draft data counts as this many Sealed games at most

LENS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["cards"],
    "properties": {"cards": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "shift", "why"],
        "properties": {"name": {"type": "string"},
                       "shift": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
                       "why": {"type": "string", "description": "one short clause"}}}}},
}

LENS_SYSTEM = """You are an expert Magic: The Gathering Limited player. For each card of a \
new set, judge how much better or worse it will be in SEALED than its Premier Draft numbers \
suggest, on a scale of -2 to +2 (0 = same; most cards are 0 or +-1; +-2 only for clear cases). \
Sealed: 6 random packs, no drafting, so decks are built from what you open. Games are slower \
and decks have more bombs and rares; two-color archetypes rarely come together; splashing is \
common. Usually better in Sealed: unconditional and flexible removal (especially answers to \
bombs), X spells and scalable finishers, card advantage and grindy value, bombs, fixing and \
dual lands, big late-game creatures, cards that are good on their own. Usually worse: cheap \
aggressive creatures and tempo cards, combat tricks, narrow archetype payoffs that need many \
specific cards, cards that need a dense synergy package a drafter would assemble. Use the card \
text first; the numbers and host comments are evidence. Return every card listed, with the \
exact name and one short clause of why."""


def lens_path(set_code: str):
    from .site.build import BLOG
    return BLOG / "data" / f"{set_code.lower()}-sealed-lens.json"


def load_lens(set_code: str) -> dict[str, tuple[int, str]]:
    path = lens_path(set_code)
    if not path.exists():
        return {}
    return {n: (v["shift"], v["why"]) for n, v in json.loads(path.read_text())["cards"].items()}


def build_lens(set_code: str, client=None) -> dict[str, tuple[int, str]]:
    """Ask Claude for every card's Sealed shift and save it (one call per set)."""
    from .experts import _ask, _client
    from .pick_advice import load_experts
    infos, notes = card_infos(set_code, lens=False)
    experts = (load_experts(set_code) or {}).get("cards") or {}
    lines = []
    for c in sorted(infos.values(), key=lambda c: c.name):
        if c.name in fastsim.BASIC_LANDS or not (c.gih or c.grade):
            continue
        takes = [t["take"] for t in experts.get(c.name, {}).get("takes", [])
                 if re.search("sealed", t["take"], re.I)]
        lines.append(f"- {c.name} {c.cost} [{' '.join(c.types)}] ({c.evidence()})"
                     f": {c.oracle.replace(chr(10), ' / ')}"
                     + (f" HOSTS ON SEALED: {' | '.join(takes)}" if takes else ""))
    user = (f"Set {set_code.upper()}, {len(lines)} cards. Premier Draft GIH WR average is "
            f"about {notes['mean_gih']:.1%}.\n\n" + "\n".join(lines))
    out = _ask(client or _client(), LENS_SYSTEM, user, LENS_SCHEMA)
    known = {c.name for c in infos.values()}
    cards = {x["name"]: {"shift": int(x["shift"]), "why": x["why"]}
             for x in out["cards"] if x["name"] in known}
    import datetime
    lens_path(set_code).write_text(json.dumps({
        "set": set_code, "made": datetime.date.today().isoformat(),
        "made_by": "Claude (claude-opus-5-5), one pass over the card text, Premier Draft "
                   "17Lands numbers and the hosts' Sealed comments",
        "cards": cards}, indent=1, ensure_ascii=False) + "\n")
    return {n: (v["shift"], v["why"]) for n, v in cards.items()}


# ------------------------------------------------------------------ review

_BUILD_SPEC = {
    "type": "object", "additionalProperties": False,
    "required": ["use", "spells", "lands", "why"],
    "properties": {
        "use": {"type": "boolean", "description": "true only if you built a deck that is "
                                                  "better than every candidate"},
        "spells": {"type": "array", "items": {"type": "string"},
                   "description": "exact card names from the pool, one entry per copy"},
        "lands": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "count"],
            "properties": {"name": {"type": "string"}, "count": {"type": "integer"}}}},
        "why": {"type": "string"},
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["recommended", "headline", "why", "builds", "swaps", "own_build",
                 "registered_verdict", "play_tips"],
    "properties": {
        "recommended": {"type": "integer",
                        "description": "index of the build to register; -1 for own_build; "
                                       "-2 for the deck the player registered"},
        "headline": {"type": "string", "description": "one sentence"},
        "why": {"type": "string", "description": "3-6 sentences of reasoning"},
        "builds": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["index", "verdict", "strengths", "weaknesses"],
            "properties": {"index": {"type": "integer"}, "verdict": {"type": "string"},
                           "strengths": {"type": "array", "items": {"type": "string"}},
                           "weaknesses": {"type": "array", "items": {"type": "string"}}}}},
        "swaps": {"type": "array", "description": "changes to the recommended build",
                  "items": {"type": "object", "additionalProperties": False,
                            "required": ["out", "in", "why"],
                            "properties": {"out": {"type": "string"}, "in": {"type": "string"},
                                           "why": {"type": "string"}}}},
        "own_build": _BUILD_SPEC,
        "registered_verdict": {"type": "string", "description": "if the player's "
                               "registered deck is given: 3-5 honest sentences on how it "
                               "compares with the best build and what it does better or "
                               "worse; else empty"},
        "play_tips": {"type": "array", "items": {"type": "string"}},
    },
}

REVIEW_SYSTEM = """You are an expert Magic: The Gathering Limited player reviewing sealed \
deck builds for a friend before they register. The goals, in the friend's words: play bombs \
if possible, no bad cards in the deck, and a real way to win; a deck of many good cards that \
work together is as good as a bomb deck. Candidate builds come from a program using real data \
only: each card's 17Lands games-in-hand win rate (GIH WR; the set's average card is about \
{mean:.1%}; Premier Draft data, not Sealed) and the averaged grades of Limited podcast hosts. \
Each build has a score broken into parts: card quality, a bomb bonus, a cost for filler cards, \
removal count, win conditions, a set-mechanic theme, curve and splash costs. Those parts come \
from rules-text patterns and averages: check them against the actual cards. Read every card. \
Check removal (real answers vs. soft ones), the curve and creature count, mana (pips vs. \
sources, what a splash costs), the win conditions, and real synergies and anti-synergies. \
Recommend one build. Propose swaps only with cards from the pool. If none of the candidates \
is right, build your own from the pool (exact names, one entry per copy, 40 cards with \
lands), set own_build.use = true and recommended = -1; otherwise own_build.use = false with \
empty lists. If the pool has no bombs, say so plainly. Keep tips short and specific to these \
cards. Disagree with the numbers when the cards justify it, and say so."""


def _parts_line(b: Build) -> str:
    p = b.parts
    bits = [f"score {b.score:+.2f}", f"card quality {p['quality']:+.2f}"]
    if p["bombs"]:
        bits.append(f"bombs +{p['bomb']:.2f} ({', '.join(p['bombs'])})")
    if p["filler"]:
        bits.append(f"filler -{p['filler_cost']:.2f} ({', '.join(p['filler'])})")
    bits.append(f"removal {p['removal']:g} (+{p['removal_bonus']:.2f})")
    bits.append("win conditions: " + (", ".join(f"{n} [{w}]" for n, w in p["wincons"])
                                      or "NONE") + (f" (-{p['wincon_cost']:.2f})"
                                                    if p["wincon_cost"] else ""))
    if p["theme_bonus"]:
        bits.append(f"theme {p['theme']} x{p['theme_n']} (+{p['theme_bonus']:.2f})")
    if p["shape"]:
        bits.append(f"curve/creature cost -{p['shape']:.2f}")
    if p["splash_cost"]:
        bits.append(f"splash cost -{p['splash_cost']:.2f}")
    return "; ".join(bits)


def _card_line(c: CardInfo) -> str:
    text = c.oracle.replace("\n", " / ")
    return f"- {c.name} {c.cost} [{' '.join(c.types)}] ({c.evidence()}): {text}"


def review_prompt(builds: list[Build], pool_names: list[str], infos: dict[str, CardInfo],
                  event: str, played: Build | None = None) -> str:
    bombs = pool_bombs(pool_names, infos)
    lines = [f"Event: {event}. Pool: {len(pool_names)} cards. Bombs in the pool: "
             + (", ".join(c.name for c in bombs) or "none"), ""]
    for i, b in enumerate(builds):
        lines.append(f"## Build {i}: {b.colors} - {b.creatures} creatures; {len(b.lands)} "
                     f"lands; curve 1-7+: {b.curve()}")
        lines.append("Score: " + _parts_line(b))
        for c in sorted(b.spells, key=lambda c: (c.mv, c.name)):
            lines.append(_card_line(c) + ("  [SPLASH]" if c in b.splash else ""))
        lands = Counter(b.lands)
        lines.append("Lands: " + ", ".join(f"{n} {name}" for name, n in lands.items()))
        lines.append("")
    if played is not None:
        lines.append(f"## The deck the player registered: {played.colors} - "
                     f"{played.creatures} creatures; {len(played.lands)} lands")
        lines.append("Score: " + _parts_line(played))
        for c in sorted(played.spells, key=lambda c: (c.mv, c.name)):
            lines.append(_card_line(c))
        lines.append("Compare it with the builds honestly: say where it is better or worse "
                     "and why. If it is the best deck, set recommended = -2.")
        lines.append("")
    used = {c.name for b in builds for c in b.spells}
    side = sorted({n for n in pool_names if n not in used and n in infos},
                  key=lambda n: -infos[n].edge)
    lines.append("## Sideboard (pool cards in none of the builds, best first)")
    lines += [_card_line(infos[n]) for n in side[:40]]
    counts = Counter(n for n in pool_names if n in infos)
    lines += ["", "## The whole pool, with copies (for swaps or your own build)",
              "; ".join(f"{k}x {n}" for n, k in sorted(counts.items()))]
    takes = []
    for name in sorted({c.name for b in builds for c in b.spells}):
        for t in infos[name].takes[:2] if name in infos else []:
            takes.append(f"- {name}: {t}")
    if takes:
        lines += ["", "## What the podcast hosts said about these cards"] + takes[:80]
    return "\n".join(lines)


def review(builds: list[Build], pool_names: list[str], infos: dict[str, CardInfo],
           event: str, mean_gih: float, played: Build | None = None) -> dict:
    from .experts import _ask, _client
    return _ask(_client(), REVIEW_SYSTEM.format(mean=mean_gih),
                review_prompt(builds, pool_names, infos, event, played), REVIEW_SCHEMA)


# ------------------------------------------------------------------ page

def _img(name: str) -> str:
    from urllib.parse import quote
    return ("https://api.scryfall.com/cards/named?format=image&version=normal&exact="
            + quote(name))


def _esc(s: str) -> str:
    import html
    return html.escape(str(s))


def write_html(path, event: str, pool_names: list[str], infos: dict[str, CardInfo],
               pairs: list[Build], builds: list[Build], verdict: dict | None,
               notes: dict, played: Build | None = None) -> None:
    builds = list(builds)
    if played is not None:
        builds.append(played)
    custom = parse_custom((verdict or {}).get("own_build") or {}, pool_names, infos) \
        if (verdict or {}).get("own_build", {}).get("use") else None
    if custom:
        builds.append(custom)
    rec = verdict.get("recommended", 0) if verdict else 0
    if rec == -1 and custom:
        rec = len(builds) - 1
    elif rec == -2 and played is not None:
        rec = builds.index(played)
    rec = rec if 0 <= rec < len(builds) else 0
    by_index = {b.get("index"): b for b in (verdict or {}).get("builds", [])}

    def badge(c: CardInfo) -> str:
        bits = []
        if c.gih is not None:
            bits.append(f"{c.gih:.1%}")
        if c.grade:
            bits.append(_esc(c.grade))
        return " · ".join(bits) or "—"

    def tier(c: CardInfo) -> str:
        return "hi" if c.edge >= 0.2 else "lo" if c.edge <= -0.15 else "mid"

    def deck_grid(b: Build) -> str:
        cols = []
        for mv in range(1, 8):
            cards = sorted((c for c in b.spells if min(max(c.mv, 1), 7) == mv),
                           key=lambda c: (not c.is_creature, -c.edge))
            items = "".join(
                f'<figure class="card {tier(c)}{" splash" if c in b.splash else ""}" '
                f'title="{_esc(c.name)} — {_esc(c.evidence())}">'
                f'<img loading="lazy" src="{_img(c.name)}" alt="{_esc(c.name)}">'
                f'<figcaption>{badge(c)}</figcaption></figure>' for c in cards)
            cols.append(f'<div class="col"><div class="mv">{mv if mv < 7 else "7+"}'
                        f' <span>{len(cards)}</span></div>{items}</div>')
        lands = Counter(b.lands)
        land_html = "".join(f'<li>{n}× {_esc(name)}</li>' for name, n in lands.items())
        return (f'<div class="grid">{"".join(cols)}</div>'
                f'<div class="lands"><b>{len(b.lands)} lands</b><ul>{land_html}</ul></div>')

    def checklist(b: Build) -> str:
        p = b.parts
        if not p:
            return ""
        def row(ok, label, value, detail=""):
            mark = "ok" if ok is True else "bad" if ok is False else "meh"
            return (f'<li class="{mark}"><span class="k">{label}</span><span class="v">{value}'
                    f'</span><span class="d">{_esc(detail)}</span></li>')
        wins = ", ".join(f"{n} ({w})" for n, w in p["wincons"])
        rows = [
            row(None, "Card quality", f"{p['quality']:+.2f}", "sum of every card's edge over "
                "an average card"),
            row(bool(p["bombs"]) or None, "Bombs", f"+{p['bomb']:.2f}",
                ", ".join(p["bombs"]) or "none"),
            row(not p["filler"] if len(p["filler"]) <= 1 else False, "Filler",
                f"−{p['filler_cost']:.2f}", ", ".join(p["filler"]) or "none"),
            row(p["removal"] >= 4 if p["removal"] >= 3 else False, "Removal",
                f"{p['removal']:g}", ", ".join(dict.fromkeys(p["removal_cards"]))),
            row(not p["wincon_cost"] if p["wincons"] else False, "Win conditions",
                str(len({n for n, _ in p['wincons']})), wins or "none"),
        ]
        if p["theme_bonus"]:
            rows.append(row(True, "Theme", f"+{p['theme_bonus']:.2f}",
                            f"{p['theme']} ({p['theme_n']} cards)"))
        if p["shape"]:
            rows.append(row(False, "Curve / creatures", f"−{p['shape']:.2f}",
                            f"{p['creatures']} creatures, {p['twos']} plays ≤ 2 mana, "
                            f"{p['tops']} at 6+"))
        if p["splash_cost"]:
            rows.append(row(None, "Splash", f"−{p['splash_cost']:.2f}",
                            ", ".join(c.name for c in b.splash)))
        return (f'<ul class="check">{"".join(rows)}<li class="tot"><span class="k">Score</span>'
                f'<span class="v">{b.score:+.2f}</span><span class="d">higher is better; '
                f'shortcut simulator: {b.win_rate:.1%} vs an average deck</span></li></ul>')

    said = (verdict or {}).get("registered_verdict")
    registered = f"<p>{_esc(said)}</p>" if said else ""

    def build_panel(i: int, b: Build) -> str:
        v = by_index.get(i, {}) if not b.label else {}
        pros = "".join(f"<li>{_esc(s)}</li>" for s in v.get("strengths", []))
        cons = "".join(f"<li>{_esc(s)}</li>" for s in v.get("weaknesses", []))
        flags = "".join(f'<span class="flag">{_esc(n)}</span>' for n in b.notes)
        review_html = (f'<p class="verdict">{_esc(v["verdict"])}</p>'
                       f'<div class="pc"><div><h4>Strengths</h4><ul>{pros}</ul></div>'
                       f'<div><h4>Weaknesses</h4><ul>{cons}</ul></div></div>') if v else ""
        own = (f'<p class="verdict">Claude built this from the pool: '
               f'{_esc(verdict["own_build"].get("why", ""))}</p>') if b.label == \
            "Claude's build" else ('<p class="verdict">The deck you registered, scored the '
                                   'same way.</p>' + registered if b.label else "")
        return (f'<section class="build" id="b{i}" {"" if i == rec else "hidden"}>'
                f'<div class="stats"><div><b>{b.score:+.2f}</b><span>score</span></div>'
                f'<div><b>{b.creatures}</b><span>creatures</span></div>'
                f'<div><b>{sum(1 for c in b.spells if c.mv <= 2)}</b><span>plays ≤ 2 mana'
                f'</span></div><div><b>{len(b.lands)}</b><span>lands</span></div>'
                f'{flags}</div>{own}{checklist(b)}{review_html}{deck_grid(b)}'
                f'<details><summary>Arena import text</summary><pre>{_esc(b.decklist())}'
                f'</pre></details></section>')

    tabs = "".join(
        f'<button class="tab{" on" if i == rec else ""}" data-i="{i}">'
        f'{"★ " if i == rec else ""}{_esc(b.label or b.colors)}'
        f'{" " + _esc(b.colors) if b.label else ""} <small>{b.score:+.2f}</small></button>'
        for i, b in enumerate(builds))
    top = max((b.score for b in pairs), default=1.0)
    low = min((b.score for b in pairs), default=0.0)
    bars = "".join(
        f'<div class="bar"><span class="lbl">{_esc(b.colors)}</span><span class="track">'
        f'<span class="fill" style="width:{8 + 92 * (b.score - low) / max(1e-6, top - low):.0f}%">'
        f'</span></span><span class="val">{b.score:+.2f}</span></div>' for b in pairs)
    used = {c.name for b in builds for c in b.spells}
    side = sorted({n for n in pool_names if n in infos and not infos[n].is_land and n not in
                   used}, key=lambda n: -infos[n].edge)[:16]
    side_html = "".join(
        f'<figure class="card {tier(infos[n])}" title="{_esc(n)} — {_esc(infos[n].evidence())}">'
        f'<img loading="lazy" src="{_img(n)}" alt="{_esc(n)}"><figcaption>{badge(infos[n])}'
        f'</figcaption></figure>' for n in side)
    if verdict:
        swaps = "".join(f'<li><b>−</b> {_esc(s["out"])} <b>+</b> {_esc(s["in"])}: '
                        f'{_esc(s["why"])}</li>' for s in verdict.get("swaps", []))
        tips = "".join(f"<li>{_esc(t)}</li>" for t in verdict.get("play_tips", []))
        head = (f'<section class="review"><div class="kicker">Claude\'s review</div>'
                f'<h2>{_esc(verdict.get("headline", ""))}</h2><p>{_esc(verdict.get("why", ""))}</p>'
                + (f'<h4>Suggested swaps</h4><ul>{swaps}</ul>' if swaps else "")
                + (f'<h4>Playing it</h4><ul>{tips}</ul>' if tips else "") + "</section>")
    else:
        head = ('<section class="review"><div class="kicker">No AI review</div><p>Run without '
                '<code>--no-review</code> (needs ANTHROPIC_API_KEY) for a final review.</p></section>')
    bombs = pool_bombs(pool_names, infos)
    best_cards = sorted({n for n in pool_names if n in infos and not infos[n].is_land},
                        key=lambda n: -infos[n].edge)[:3]
    banner = (f'<p class="banner bombs">Bombs in this pool: '
              f'{", ".join(_esc(c.name) for c in bombs)}</p>' if bombs else
              f'<p class="banner">No bombs in this pool. Best cards: '
              f'{", ".join(f"{_esc(n)} ({infos[n].evidence()})" for n in best_cards)}. '
              f'Win with card quality, removal and a theme.</p>')
    head = banner + head
    page = PAGE.format(
        title=_esc(f"Sealed guide · {event}"), event=_esc(event), n=len(pool_names),
        head=head, tabs=tabs, panels="".join(build_panel(i, b) for i, b in enumerate(builds)),
        bars=bars, side=side_html, values=_esc(notes.get("values", "")),
        fit=_esc(notes.get("expert_fit", "")), rated=notes.get("rated", 0),
        experts=notes.get("experts", 0))
    path.write_text(page)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{--bg:#f7f6f2;--panel:#fff;--ink:#1d1c1a;--muted:#6b6862;--line:#e3e0d8;--accent:#2f5d8a;
--hi:#2e7d4f;--lo:#b4472f;--mid:#8a8578;--chip:#eef2f6}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151514;--panel:#1f1e1c;
--ink:#ecebe7;--muted:#a19d95;--line:#34322e;--accent:#8db7e0;--hi:#6cc28f;--lo:#e58a73;--mid:#9a958b;
--chip:#26303a}}}}
:root[data-theme="dark"]{{--bg:#151514;--panel:#1f1e1c;--ink:#ecebe7;--muted:#a19d95;--line:#34322e;
--accent:#8db7e0;--hi:#6cc28f;--lo:#e58a73;--mid:#9a958b;--chip:#26303a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1240px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}}.sub{{color:var(--muted);margin:0 0 20px}}
.review{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:10px;padding:16px 20px;margin-bottom:20px}}
.kicker{{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);font-weight:600}}
.review h2{{font-size:19px;margin:4px 0 8px}}h4{{margin:12px 0 4px;font-size:14px}}
ul{{margin:4px 0;padding-left:20px}}
.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
.tab{{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;
padding:6px 14px;font-size:14px;cursor:pointer}}.tab small{{color:var(--muted)}}
.tab.on{{background:var(--accent);color:var(--bg);border-color:var(--accent)}}.tab.on small{{color:inherit}}
.build{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}}
.stats{{display:flex;gap:24px;flex-wrap:wrap;align-items:center;margin-bottom:8px}}
.stats div{{display:flex;flex-direction:column}}.stats b{{font-size:20px}}
.stats span{{font-size:12px;color:var(--muted)}}
.flag{{background:var(--chip);border-radius:6px;padding:2px 8px;font-size:12px}}
.verdict{{font-weight:600;margin:8px 0}}.pc{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.grid{{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;margin-top:12px}}
.mv{{font-weight:600;text-align:center;border-bottom:1px solid var(--line);margin-bottom:6px}}
.mv span{{color:var(--muted);font-weight:400}}
.card{{margin:0 0 -62%;position:relative}}.col .card:last-child{{margin-bottom:0}}
.card img{{width:100%;border-radius:4.5%;display:block;box-shadow:0 1px 3px rgba(0,0,0,.35)}}
.card figcaption{{position:absolute;top:11%;right:6%;font-size:11px;font-weight:600;padding:1px 6px;
border-radius:6px;background:var(--panel);color:var(--mid);border:1px solid var(--line)}}
.card.hi figcaption{{color:var(--hi)}}.card.lo figcaption{{color:var(--lo)}}
.card.splash img{{outline:3px dashed var(--accent);outline-offset:1px}}
.card:hover{{z-index:5;transform:scale(1.04)}}
.lands{{margin-top:14px;color:var(--muted)}}.lands ul{{display:flex;gap:16px;list-style:none;padding:0;flex-wrap:wrap}}
pre{{background:var(--bg);padding:10px;border-radius:6px;overflow:auto}}
.two{{display:grid;grid-template-columns:1fr 2fr;gap:20px;margin-top:20px}}
.box{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}}
.box h3{{margin:0 0 10px;font-size:16px}}
.bar{{display:grid;grid-template-columns:44px 1fr 52px;gap:8px;align-items:center;margin:4px 0}}
.track{{background:var(--bg);border-radius:4px;height:14px}}.fill{{display:block;height:14px;
border-radius:4px;background:var(--accent)}}.val{{font-variant-numeric:tabular-nums;text-align:right}}
.side{{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px}}.side .card{{margin:0}}
.banner{{background:var(--chip);border-radius:8px;padding:10px 14px;margin:0 0 16px}}
.banner.bombs{{border-left:4px solid var(--hi)}}
.check{{list-style:none;padding:0;margin:10px 0;border:1px solid var(--line);border-radius:8px}}
.check li{{display:grid;grid-template-columns:140px 70px 1fr;gap:10px;padding:6px 12px;
border-top:1px solid var(--line);align-items:baseline}}.check li:first-child{{border-top:0}}
.check .k{{font-weight:600}}.check .v{{font-variant-numeric:tabular-nums;text-align:right}}
.check .d{{color:var(--muted);font-size:13px}}
.check li.ok .k::before{{content:"✓ ";color:var(--hi)}}.check li.bad .k::before{{content:"✗ ";color:var(--lo)}}
.check li.meh .k::before{{content:"· ";color:var(--mid)}}.check li.tot{{background:var(--bg)}}
@media (max-width:760px){{.check li{{grid-template-columns:1fr auto}}.check .d{{grid-column:1/-1}}}}
.foot{{color:var(--muted);font-size:13px;margin-top:20px}}
@media (max-width:760px){{.grid{{grid-template-columns:repeat(4,minmax(0,1fr))}}
.two,.pc{{grid-template-columns:1fr}}.side{{grid-template-columns:repeat(4,minmax(0,1fr))}}}}
</style></head><body><main>
<h1>Sealed guide</h1><p class="sub">{event} · {n} cards in the pool</p>
{head}
<div class="tabs">{tabs}</div>{panels}
<div class="two"><div class="box"><h3>Best build per color pair (score)</h3>{bars}</div>
<div class="box"><h3>Best of the rest (not in any build above)</h3><div class="side">{side}</div></div></div>
<p class="foot">Badges: 17Lands games-in-hand win rate · podcast hosts' average grade. Green = well above
the average card, red = well below. Dashed outline = splash. Card values: {values}
({rated} cards rated) blended with expert grades ({experts} cards; {fit}). Score = card quality
+ bomb bonus − a filler cost that grows with each filler card + removal (up to 4–6) − a cost for no
win condition + set-theme bonus − curve and splash costs, all in log-odds of winning; the weights are
judgment calls until real FRA sealed results exist to fit them. No simulator ratings are used.</p>
</main><script>
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{{
document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
document.querySelectorAll('.build').forEach(s=>s.hidden=s.id!=='b'+t.dataset.i);}});
</script></body></html>"""
