"""Figures and tables for findings posts, computed from simulation output.

Everything a post shows is generated here from a run's JSON (``mulligan draft
--out``), so the numbers on the page are the numbers in the data. Figures are
SVG; tables are small HTML fragments. Both are inlined by ``{{figure name}}``.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from html import escape
from pathlib import Path

from ..cards.sets import SetData, load_set
from ..limited.rating import static_rating
from . import charts

MIN_GAMES = 300


def wilson(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return mid - half, mid + half


def _colors(data: SetData, name: str) -> str:
    from ..limited.draft import card_colors
    return "".join(c for c in "WUBRG" if c in card_colors(data, name))


def _rarity(data: SetData, name: str) -> str:
    return data.entries.get(name, {}).get("rarity", "common")


class DraftRun:
    """One ``mulligan draft`` run, with the derived per-card numbers."""

    def __init__(self, path: Path):
        raw = json.loads(Path(path).read_text())
        self.raw = raw
        self.data = load_set(raw["set"])
        self.records = raw["records"]
        self.decks = raw["decks"]
        # name -> (wins, games, average pick, times picked)
        self.cards = {n: tuple(v) for n, v in raw["cards"].items()}

    def rated(self, min_games: int = MIN_GAMES) -> list[tuple[str, float, int, float]]:
        """(name, games-in-hand win rate, games, average pick) for spells."""
        out = []
        for name, (wins, games, ata, _) in self.cards.items():
            spec = self.data.playable.get(name)
            if spec is None or spec.is_land or games < min_games:
                continue
            out.append((name, wins / games, games, ata))
        return out

    def mean_gih(self) -> float:
        rows = self.rated(1)
        total = sum(g for _, _, g, _ in rows)
        return sum(r * g for _, r, g, _ in rows) / total


def archetypes(run: DraftRun) -> str:
    rows = []
    for pair, (wins, games) in sorted(run.records.items(),
                                      key=lambda kv: -kv[1][0] / kv[1][1]):
        lo, hi = wilson(wins, int(games))
        rows.append((pair, wins / games, lo, hi, pair))
    return charts.interval_bars(rows, "Win rate by color pair in simulated drafts")


def pick_vs_win(run: DraftRun, callouts: int = 6) -> tuple[str, list[str], list[str]]:
    """Scatter of average pick against games-in-hand win rate. Returns the SVG
    and the most under- and over-drafted card names (residuals of a straight
    line fit: winning more, or less, than where the bots take them)."""
    rows = run.rated()
    xs = [r[3] for r in rows]
    ys = [r[1] for r in rows]
    n = len(rows)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
             / sum((x - mx) ** 2 for x in xs))
    resid = {r[0]: r[1] - (my + slope * (r[3] - mx)) for r in rows}
    under = sorted(resid, key=lambda k: -resid[k])[:callouts]
    over = sorted(resid, key=lambda k: resid[k])[:callouts]
    points = [(r[0], r[3], r[1], charts.color_key(_colors(run.data, r[0]))) for r in rows]
    svg = charts.scatter(points, "Average pick against win rate when drawn",
                         "Average pick taken at (1 = first pick of a pack)",
                         "Win rate when drawn", callouts=set(under[:4] + over[:4]),
                         x_fmt=lambda v: f"{v:.0f}")
    return svg, under, over


def card_table(run: DraftRun, names: list[str], note: dict[str, str] | None = None) -> str:
    stats = {r[0]: r for r in run.rated(1)}
    rows = []
    for name in names:
        _, rate, games, ata = stats[name]
        spec = run.data.playable[name]
        rows.append(
            f"<tr><td>{escape(name)}</td><td>{escape(str(spec.cost))}</td>"
            f"<td>{_rarity(run.data, name)[0].upper()}</td>"
            f'<td class="num">{rate:.1%}</td><td class="num">{ata:.1f}</td>'
            f'<td class="num">{games:,}</td>'
            + (f"<td>{escape(note.get(name, ''))}</td>" if note is not None else "") + "</tr>")
    head = ("<tr><th>Card</th><th>Cost</th><th>Rarity</th><th class=\"num\">Win rate when "
            "drawn</th><th class=\"num\">Avg pick</th><th class=\"num\">Games</th>"
            + ("<th>Why</th>" if note is not None else "") + "</tr>")
    return f"<table>{head}{''.join(rows)}</table>"


def best_commons(run: DraftRun, per_color: int = 3) -> str:
    rows = run.rated()
    out = []
    for color in "WUBRG":
        mine = sorted((r for r in rows if _rarity(run.data, r[0]) == "common"
                       and _colors(run.data, r[0]) == color), key=lambda r: -r[1])
        out.extend((f"{r[0]}", r[1], color) for r in mine[:per_color])
    return charts.bars(out, "Best commons by color", fmt=lambda v: f"{v:.1%}",
                       zero=round(run.mean_gih() - 0.08, 2))


MECHANICS = {
    "Empower Jace": "empower jace",
    "Prepare": "prepared",
    "Surveil": "surveil",
    "Scry": "scry",
    "Prowess": "prowess",
    "Convoke": "convoke",
    "Exhaust": "exhaust",
    "Planeswalker": None,     # by type
    "Equipment": None,        # by subtype
    "Flying": None,           # keyword
}


def _has_mechanic(run: DraftRun, name: str, mechanic: str) -> bool:
    entry = run.data.entries.get(name, {})
    spec = run.data.playable[name]
    text = entry.get("oracle", "").lower()
    if mechanic == "Planeswalker":
        return "Planeswalker" in entry.get("types", [])
    if mechanic == "Equipment":
        return "Equipment" in entry.get("subtypes", [])
    if mechanic == "Flying":
        return spec.is_creature and "flying" in [str(k.value).lower() for k in spec.keywords]
    return MECHANICS[mechanic] in text


def mechanics(run: DraftRun, seed: int = 0) -> tuple[str, dict[str, tuple[float, int]]]:
    """Average games-in-hand win rate of the cards carrying each mechanic,
    against the set's average, with a bootstrap-over-cards interval."""
    rows = run.rated(150)
    mean = run.mean_gih()
    rng = random.Random(seed)
    out, summary = [], {}
    for mechanic in MECHANICS:
        cards = [r for r in rows if _has_mechanic(run, r[0], mechanic)]
        if len(cards) < 4:
            continue
        values = [r[1] - mean for r in cards]
        avg = sum(values) / len(values)
        boots = sorted(sum(rng.choice(values) for _ in values) / len(values)
                       for _ in range(2000))
        out.append((f"{mechanic} ({len(cards)})", avg, boots[50], boots[1949], ""))
        summary[mechanic] = (avg, len(cards))
    out.sort(key=lambda r: -r[1])
    svg = charts.interval_bars(out, "Mechanics against the set average", reference=0.0,
                               fmt=lambda v: f"{v * 100:+.1f}")
    return svg, summary


def text_vs_play(run: DraftRun, n: int = 6) -> tuple[list[str], list[str]]:
    """Cards whose simulated results most beat (and trail) what their text
    alone predicts: residuals of win rate against the text-derived rating."""
    rows = run.rated()
    xs = [static_rating(run.data.playable[r[0]]) for r in rows]
    ys = [r[1] for r in rows]
    k = len(rows)
    mx, my = sum(xs) / k, sum(ys) / k
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum(
        (x - mx) ** 2 for x in xs)
    resid = {r[0]: r[1] - (my + slope * (x - mx)) for r, x in zip(rows, xs, strict=True)}
    return (sorted(resid, key=lambda c: -resid[c])[:n], sorted(resid, key=lambda c: resid[c])[:n])


def color_presence(run: DraftRun) -> str:
    """Per color: share of drafted decks playing it, and those decks' win rate."""
    decks = defaultdict(int)
    for pair, _ in run.decks:
        for c in pair:
            decks[c] += 1
    wins, games = defaultdict(float), defaultdict(int)
    for pair, (w, g) in run.records.items():
        for c in pair:
            wins[c] += w
            games[c] += g
    total = len(run.decks)
    rows = [(f"{c}  in {decks[c] / total:.0%} of decks", wins[c] / games[c],
             *wilson(wins[c], int(games[c])), c) for c in "WUBRG"]
    rows = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    return charts.interval_bars(sorted(rows, key=lambda r: -r[1]),
                                "Win rate of decks playing each color")


def write(out_dir: Path, name: str, content: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".svg" if content.startswith("<svg") else ".html"
    path = out_dir / f"{name}{suffix}"
    path.write_text(content)
    return path
