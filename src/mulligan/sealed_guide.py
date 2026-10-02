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

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from . import fastsim

COLORS = "WUBRG"
COLOR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
BASIC_FOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
EXPERT_PRIOR_GAMES = 500   # a card's expert grade weighs as much as this many 17Lands games
SPLASH_KEEP = 0.7          # a splashed card is castable about this often when it matters
SPLASH_MIN_EDGE = 0.25     # only splash cards at least this far above average (log-odds)
MIN_CREATURES = 14
SPELLS = 23


@dataclass
class CardInfo:
    name: str
    cost: str = ""
    mv: int = 0
    pips: dict[str, float] = field(default_factory=dict)   # color -> pips (hybrid split)
    types: list[str] = field(default_factory=list)
    rarity: str = ""
    oracle: str = ""
    makes: set[str] = field(default_factory=set)          # lands: colors produced
    gih: float | None = None
    games: int = 0
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


def card_infos(set_code: str, fmt: str = "Sealed",
               names: list[str] | None = None) -> tuple[dict[str, CardInfo], dict]:
    """Every card of the set with its real-data edge; plus notes on the sources."""
    from .cards.sets import load_set
    from .pick_advice import grade_points, load_experts
    from .validation import seventeen
    data = load_set(set_code)
    values = fastsim.load_values(set_code, fmt)
    try:
        gih = seventeen.game_data_ratings(set_code, fmt)
    except Exception:  # noqa: BLE001 - game files not out yet
        gih = seventeen.fetch_ratings(set_code, "PremierDraft" if "PremierDraft"
                                      in values.source else fmt)
    experts = _expert_grades(load_experts(set_code))
    infos: dict[str, CardInfo] = {}
    for name in set(data.entries) | set(names or []):
        e = data.entries.get(name, {})
        info = CardInfo(name, cost=e.get("cost", ""), mv=_mana_value(e.get("cost", "")),
                        pips=_pips(e.get("cost", "")), types=list(e.get("types", [])),
                        rarity=e.get("rarity", ""), oracle=e.get("oracle", ""))
        if info.is_land:
            info.makes = _land_makes(e)
        if name in gih:
            info.gih, info.games = gih[name]
            info.real_edge = values.values.get(name)
        if name in experts:
            mean, info.grade, info.takes = experts[name]
            info.expert_edge = grade_points(mean)   # win-rate points for now
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
        else:
            i.edge = real
    notes = {"values": values.source, "expert_fit": f"edge = {a:+.3f} + {b:.4f} × grade points "
             f"({len(both)} cards)", "experts": sum(1 for i in infos.values() if i.grade),
             "rated": sum(1 for i in infos.values() if i.gih is not None)}
    return infos, notes


@dataclass
class Build:
    colors: str                 # e.g. "UB" or "UB+r"
    spells: list[CardInfo]
    lands: list[str]            # land names, basics included
    splash: list[CardInfo] = field(default_factory=list)
    win_rate: float = 0.0       # shortcut simulator, vs an average deck
    notes: list[str] = field(default_factory=list)

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


def _build(pool: list[CardInfo], pair: str, splash: list[CardInfo]) -> Build | None:
    colors = set(pair)
    spells = [c for c in pool if not c.is_land and c.castable(colors)]
    if len(spells) < 15:
        return None
    spells.sort(key=lambda c: c.edge, reverse=True)
    chosen = spells[:SPELLS - len(splash)]
    rest = spells[SPELLS - len(splash):]
    creatures = sum(1 for c in chosen if c.is_creature)
    for creature in [c for c in rest if c.is_creature]:
        if creatures >= MIN_CREATURES:
            break
        weakest = min((c for c in chosen if not c.is_creature), key=lambda c: c.edge,
                      default=None)
        if weakest is None or creature.edge < weakest.edge - 0.15:
            break
        chosen.remove(weakest)
        chosen.append(creature)
        creatures += 1
    chosen += splash
    lands_total = 40 - len(chosen)
    # Lands: on-color nonbasics first (duals, and fixers for the splash).
    all_colors = colors | {k for c in splash for k in c.off_color_pips(colors)}
    nonbasic = [c for c in pool if c.is_land and c.makes and c.makes & all_colors
                and (len(c.makes & all_colors) >= 2 or c.makes <= all_colors)]
    nonbasic.sort(key=lambda c: (-len(c.makes & all_colors), -c.edge))
    lands = [c.name for c in nonbasic[:4]]
    splash_colors = all_colors - colors
    for s in splash_colors:   # three sources per splashed color, four for two+ cards
        want = 3 + (sum(1 for c in splash if s in c.off_color_pips(colors)) >= 2)
        have = sum(1 for n in lands for c in pool if c.name == n and s in c.makes)
        lands += [BASIC_FOR[s]] * max(0, want - have)
    pips = Counter()
    for c in chosen:
        for k, v in c.pips.items():
            if k in colors:
                pips[k] += v
    remaining = max(0, lands_total - len(lands))
    weight = sum(pips.values()) or 1.0
    counts = {k: round(remaining * pips[k] / weight) for k in pair}
    while sum(counts.values()) > remaining:
        counts[max(counts, key=counts.get)] -= 1
    while sum(counts.values()) < remaining:
        counts[max(pair, key=lambda k: pips[k] / max(1, counts[k]))] += 1
    for k in pair:
        lands += [BASIC_FOR[k]] * counts[k]
    label = pair + ("+" + "".join(sorted(splash_colors)).lower() if splash_colors else "")
    return Build(label, chosen, lands, list(splash))


def _score(build: Build, games: int, seed: int) -> None:
    cards = [(c.edge * (SPLASH_KEEP if c in build.splash else 1.0), False)
             for c in build.spells] + [(0.0, True)] * len(build.lands)
    deck = fastsim.Deck(cards)
    edge = fastsim.deck_score(deck, games, seed)
    # Deck-shape costs the card values cannot see.
    short = max(0, 13 - build.creatures)
    twos = sum(1 for c in build.spells if c.mv <= 2)
    edge -= 0.04 * short + 0.03 * max(0, 4 - twos)
    build.win_rate = fastsim._sigmoid(edge + fastsim.deck_score(
        fastsim.Deck([(0.0, False)] * 23 + [(0.0, True)] * 17), games, seed) * -1)
    if short:
        build.notes.append(f"only {build.creatures} creatures")
    if twos < 4:
        build.notes.append(f"only {twos} plays at two mana or less")


def candidate_builds(pool_names: list[str], infos: dict[str, CardInfo],
                     games: int = 3000, seed: int = 0) -> tuple[list[Build], list[Build]]:
    """(every color pair's build, the best builds overall including splashes)."""
    pool = [infos[n] if n in infos else CardInfo(n) for n in pool_names]
    pairs = []
    for p in combinations(COLORS, 2):
        b = _build(pool, "".join(p), [])
        if b:
            _score(b, games, seed)
            pairs.append(b)
    pairs.sort(key=lambda b: -b.win_rate)
    builds = list(pairs)
    for base in pairs[:4]:
        colors = set(base.colors[:2])
        bombs = sorted((c for c in pool if not c.is_land and not c.castable(colors)
                        and sum(c.off_color_pips(colors).values()) == 1
                        and c.edge >= SPLASH_MIN_EDGE), key=lambda c: -c.edge)
        by_color: dict[str, list[CardInfo]] = {}
        for c in bombs:
            by_color.setdefault(next(iter(c.off_color_pips(colors))), []).append(c)
        for cards in by_color.values():
            for k in (1, 2):
                if len(cards) >= k:
                    b = _build(pool, base.colors[:2], cards[:k])
                    if b:
                        _score(b, games, seed)
                        builds.append(b)
    builds.sort(key=lambda b: -b.win_rate)
    best, seen = [], set()
    for b in builds:
        key = frozenset(c.name for c in b.spells)
        if key not in seen:
            seen.add(key)
            best.append(b)
    return pairs, best[:5]


# ------------------------------------------------------------------ review

REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["recommended", "headline", "why", "builds", "swaps", "play_tips"],
    "properties": {
        "recommended": {"type": "integer", "description": "index of the build to register"},
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
        "play_tips": {"type": "array", "items": {"type": "string"}},
    },
}

REVIEW_SYSTEM = """You are an expert Magic: The Gathering Limited player reviewing sealed \
deck builds for a friend before they register. You get candidate builds that a program made \
from real data: each card's 17Lands games-in-hand win rate (GIH WR; the set's average card is \
about {mean:.1%}) and the averaged grades of Limited podcast hosts, plus a shortcut-simulator \
estimate of each build's win rate against an average deck. Those numbers know nothing about \
synergy, curve, interaction density, or how the cards work together - that is your job. Read \
the card text. Check: enough early plays and creatures, removal count, mana (pips vs sources, \
splash cost), bombs, real synergies and anti-synergies. Recommend one build; propose swaps only \
with cards that are in the pool's sideboard; keep tips short and specific to these cards. \
Disagree with the numbers when the cards justify it, and say so plainly."""


def _card_line(c: CardInfo) -> str:
    text = c.oracle.replace("\n", " / ")
    return f"- {c.name} {c.cost} [{' '.join(c.types)}] ({c.evidence()}): {text}"


def review_prompt(builds: list[Build], pool_names: list[str], infos: dict[str, CardInfo],
                  event: str) -> str:
    lines = [f"Event: {event}. Pool: {len(pool_names)} cards.", ""]
    for i, b in enumerate(builds):
        lines.append(f"## Build {i}: {b.colors} - estimated {b.win_rate:.1%} vs an average "
                     f"deck; {b.creatures} creatures; curve 1-7+: {b.curve()}"
                     + (f"; flags: {', '.join(b.notes)}" if b.notes else ""))
        for c in sorted(b.spells, key=lambda c: (c.mv, c.name)):
            lines.append(_card_line(c) + ("  [SPLASH]" if c in b.splash else ""))
        lands = Counter(b.lands)
        lines.append("Lands: " + ", ".join(f"{n} {name}" for name, n in lands.items()))
        lines.append("")
    used = {c.name for b in builds for c in b.spells}
    side = sorted({n for n in pool_names if n not in used and n in infos},
                  key=lambda n: -infos[n].edge)
    lines.append("## Sideboard (pool cards in none of the builds, best first)")
    lines += [_card_line(infos[n]) for n in side[:40]]
    takes = []
    for name in sorted({c.name for b in builds for c in b.spells}):
        for t in infos[name].takes[:2] if name in infos else []:
            takes.append(f"- {name}: {t}")
    if takes:
        lines += ["", "## What the podcast hosts said about these cards"] + takes[:80]
    return "\n".join(lines)


def review(builds: list[Build], pool_names: list[str], infos: dict[str, CardInfo],
           event: str, mean_gih: float) -> dict:
    from .experts import _ask, _client
    return _ask(_client(), REVIEW_SYSTEM.format(mean=mean_gih),
                review_prompt(builds, pool_names, infos, event), REVIEW_SCHEMA)


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
               notes: dict) -> None:
    rec = verdict.get("recommended", 0) if verdict else 0
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

    def build_panel(i: int, b: Build) -> str:
        v = by_index.get(i, {})
        pros = "".join(f"<li>{_esc(s)}</li>" for s in v.get("strengths", []))
        cons = "".join(f"<li>{_esc(s)}</li>" for s in v.get("weaknesses", []))
        flags = "".join(f'<span class="flag">{_esc(n)}</span>' for n in b.notes)
        review_html = (f'<p class="verdict">{_esc(v["verdict"])}</p>'
                       f'<div class="pc"><div><h4>Strengths</h4><ul>{pros}</ul></div>'
                       f'<div><h4>Weaknesses</h4><ul>{cons}</ul></div></div>') if v else ""
        return (f'<section class="build" id="b{i}" {"" if i == rec else "hidden"}>'
                f'<div class="stats"><div><b>{b.win_rate:.1%}</b><span>vs average deck</span>'
                f'</div><div><b>{b.creatures}</b><span>creatures</span></div>'
                f'<div><b>{sum(1 for c in b.spells if c.mv <= 2)}</b><span>plays ≤ 2 mana'
                f'</span></div><div><b>{len(b.splash)}</b><span>splashed</span></div>'
                f'{flags}</div>{review_html}{deck_grid(b)}'
                f'<details><summary>Arena import text</summary><pre>{_esc(b.decklist())}'
                f'</pre></details></section>')

    tabs = "".join(
        f'<button class="tab{" on" if i == rec else ""}" data-i="{i}">'
        f'{"★ " if i == rec else ""}{_esc(b.colors)} <small>{b.win_rate:.1%}</small></button>'
        for i, b in enumerate(builds))
    top = max((b.win_rate for b in pairs), default=0.5)
    low = min((b.win_rate for b in pairs), default=0.4)
    bars = "".join(
        f'<div class="bar"><span class="lbl">{_esc(b.colors)}</span><span class="track">'
        f'<span class="fill" style="width:{8 + 92 * (b.win_rate - low) / max(1e-6, top - low):.0f}%">'
        f'</span></span><span class="val">{b.win_rate:.1%}</span></div>' for b in pairs)
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
.foot{{color:var(--muted);font-size:13px;margin-top:20px}}
@media (max-width:760px){{.grid{{grid-template-columns:repeat(4,minmax(0,1fr))}}
.two,.pc{{grid-template-columns:1fr}}.side{{grid-template-columns:repeat(4,minmax(0,1fr))}}}}
</style></head><body><main>
<h1>Sealed guide</h1><p class="sub">{event} · {n} cards in the pool</p>
{head}
<div class="tabs">{tabs}</div>{panels}
<div class="two"><div class="box"><h3>Best build per color pair</h3>{bars}</div>
<div class="box"><h3>Best of the rest (not in any build above)</h3><div class="side">{side}</div></div></div>
<p class="foot">Badges: 17Lands games-in-hand win rate · podcast hosts' average grade. Green = well above
the average card, red = well below. Dashed outline = splash. Card values: {values}
({rated} cards rated) blended with expert grades ({experts} cards; {fit}). Win rates are the shortcut
simulator (draws scored by those values; no synergy or curve). No simulator ratings are used.</p>
</main><script>
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{{
document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
document.querySelectorAll('.build').forEach(s=>s.hidden=s.id!=='b'+t.dataset.i);}});
</script></body></html>"""
