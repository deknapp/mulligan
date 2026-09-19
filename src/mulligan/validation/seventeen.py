"""17Lands: the ground truth the simulator is checked against.

``fetch_ratings`` pulls per-card "games in hand" win rates (GIH WR) for a set
from 17Lands' public card-ratings endpoint and caches them. ``correlate``
compares them with simulated ratings: if the simulator's card ranking tracks
real players' results, its release-day ratings for a new set mean something;
if it does not, the report says so.

17Lands data is published for free by 17lands.com; please credit them.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

from ..paths import cache_dir

URL = "https://www.17lands.com/card_ratings/data?expansion={set}&format={fmt}"
CACHE_DIR = cache_dir("17lands")
HEADERS = {"User-Agent": "mulligan/0.1 (github.com/deknapp/mulligan)"}


def fetch_ratings(set_code: str, fmt: str = "PremierDraft", refresh: bool = False,
                  cache_dir: Path = CACHE_DIR) -> dict[str, tuple[float, int]]:
    """Card name -> (GIH WR, games in hand)."""
    path = cache_dir / f"{set_code.upper()}_{fmt}.json"
    if path.exists() and not refresh:
        rows = json.loads(path.read_text())
    else:
        request = urllib.request.Request(URL.format(set=set_code.upper(), fmt=fmt),
                                         headers=HEADERS)
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
        time.sleep(0.5)
    out = {}
    for row in rows:
        rate = row.get("ever_drawn_win_rate")
        games = row.get("ever_drawn_game_count") or 0
        if rate is not None:
            out[row["name"].split(" // ")[0]] = (float(rate), int(games))
    return out


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else 0.0


def spearman(xs: list[float], ys: list[float]) -> float:
    return pearson(_ranks(xs), _ranks(ys))


Row = tuple[str, float, float]


def correlate(simulated: dict[str, float], real: dict[str, tuple[float, int]],
              min_real_games: int = 500) -> tuple[float, float, int, list[Row]]:
    """Spearman and Pearson correlation over cards present in both, and the rows."""
    rows = [(name, simulated[name], real[name][0]) for name in simulated
            if name in real and real[name][1] >= min_real_games]
    if len(rows) < 3:
        return 0.0, 0.0, len(rows), rows
    sims = [r[1] for r in rows]
    reals = [r[2] for r in rows]
    return spearman(sims, reals), pearson(sims, reals), len(rows), rows


S3_URL = ("https://17lands-public.s3.amazonaws.com/analysis_data/game_data/"
          "game_data_public.{set}.{fmt}.csv.gz")


def game_data_ratings(set_code: str, fmt: str = "Sealed", refresh: bool = False,
                      cache_dir: Path = CACHE_DIR) -> dict[str, tuple[float, int]]:
    """Full-history GIH WR from 17Lands' public per-game dataset.

    Each row is one game with ``opening_hand_<card>`` and ``drawn_<card>``
    counts and ``won``; a card is "in hand" in a game when either is nonzero.
    The aggregate is cached as JSON; the raw CSV is streamed and not kept.
    """
    import csv
    import gzip
    import io

    out_path = cache_dir / f"{set_code.upper()}_{fmt}_gih.json"
    if out_path.exists() and not refresh:
        return {k: tuple(v) for k, v in json.loads(out_path.read_text()).items()}
    request = urllib.request.Request(S3_URL.format(set=set_code.upper(), fmt=fmt),
                                     headers=HEADERS)
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = response.read()
    reader = csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)),
                                         encoding="utf-8"))
    header = next(reader)
    won_col = header.index("won")
    opening = {h[len("opening_hand_"):]: i for i, h in enumerate(header)
               if h.startswith("opening_hand_")}
    drawn = {h[len("drawn_"):]: i for i, h in enumerate(header) if h.startswith("drawn_")}
    cards = sorted(set(opening) & set(drawn))
    games = dict.fromkeys(cards, 0)
    wins = dict.fromkeys(cards, 0)
    for row in reader:
        won = row[won_col] in ("True", "true", "1")
        for card in cards:
            if row[opening[card]] not in ("0", "") or row[drawn[card]] not in ("0", ""):
                games[card] += 1
                wins[card] += won
    result = {card.split(" // ")[0]: (wins[card] / games[card], games[card])
              for card in cards if games[card]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result))
    return result
