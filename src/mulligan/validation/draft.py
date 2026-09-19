"""Draft validation: do simulated drafts look like real ones?

Compares a ``mulligan draft --out`` run with 17Lands' public Premier Draft
data for the same set, on the three numbers the findings posts quote:

- card win rate when drawn (simulated vs real GIH WR),
- average pick (simulated vs real ATA, "average taken at"),
- color-pair win rate (simulated vs real records by main colors).

Each is a Spearman rank correlation: does the simulation order things the way
real drafts do? Absolute levels differ (17Lands users win about 55%, not 50%).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from ..paths import cache_dir
from .seventeen import HEADERS, S3_URL, game_data_ratings, spearman

PAIRS = {"".join(p) for p in combinations("WUBRG", 2)}


def real_pair_records(set_code: str, fmt: str = "PremierDraft") -> dict[str, tuple[int, int]]:
    """Wins and games by two-color main deck colors (no splash distinction),
    from 17Lands' per-game data. Cached as JSON next to the other aggregates."""
    out_path = cache_dir("17lands") / f"{set_code.upper()}_{fmt}_pairs.json"
    if out_path.exists():
        return {k: tuple(v) for k, v in json.loads(out_path.read_text()).items()}
    raw_path = cache_dir("17lands") / f"game_data_public.{set_code.upper()}.{fmt}.csv.gz"
    if raw_path.exists():
        raw = raw_path.read_bytes()
    else:
        request = urllib.request.Request(S3_URL.format(set=set_code.upper(), fmt=fmt),
                                         headers=HEADERS)
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = response.read()
    reader = csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)),
                                         encoding="utf-8"))
    header = next(reader)
    colors, won = header.index("main_colors"), header.index("won")
    wins: dict[str, int] = defaultdict(int)
    games: dict[str, int] = defaultdict(int)
    for row in reader:
        pair = "".join(c for c in "WUBRG" if c in row[colors])
        if pair in PAIRS:
            games[pair] += 1
            wins[pair] += row[won] in ("True", "true", "1")
    result = {p: (wins[p], games[p]) for p in games}
    out_path.write_text(json.dumps(result))
    return result


@dataclass
class DraftValidation:
    gih_rho: float
    gih_n: int
    ata_rho: float
    ata_n: int
    pair_rho: float
    pairs: list[tuple[str, float, float]]   # (pair, simulated, real), by real

    def summary(self) -> str:
        lines = [f"card win rate when drawn  Spearman {self.gih_rho:+.2f}  ({self.gih_n} cards)",
                 f"average pick              Spearman {self.ata_rho:+.2f}  ({self.ata_n} cards)",
                 f"color-pair win rate       Spearman {self.pair_rho:+.2f}  (10 pairs)"]
        lines += [f"  {p}  sim {s:.1%}  real {r:.1%}" for p, s, r in self.pairs]
        return "\n".join(lines)


def validate_draft(run_path: Path, min_games: int = 200,
                   real_min_games: int = 500) -> DraftValidation:
    run = json.loads(Path(run_path).read_text())
    set_code = run["set"]
    cards = run["cards"]      # name -> [wins, games, average pick, times picked]
    real_gih = game_data_ratings(set_code, "PremierDraft")
    real_ata = {name: row for name, row in _ata(set_code).items()}
    gih = [(w / g, real_gih[n][0]) for n, (w, g, _, _) in cards.items()
           if g >= min_games and n in real_gih and real_gih[n][1] >= real_min_games]
    ata = [(a, real_ata[n]) for n, (_, _, a, picked) in cards.items()
           if picked >= 20 and n in real_ata]
    real_pairs = real_pair_records(set_code)
    pairs = sorted(((p, w / g, real_pairs[p][0] / real_pairs[p][1])
                    for p, (w, g) in run["records"].items() if p in real_pairs),
                   key=lambda r: -r[2])
    return DraftValidation(
        spearman([a for a, _ in gih], [b for _, b in gih]), len(gih),
        spearman([a for a, _ in ata], [b for _, b in ata]), len(ata),
        spearman([s for _, s, _ in pairs], [r for _, _, r in pairs]), pairs)


def _ata(set_code: str, start: str = "2020-01-01") -> dict[str, float]:
    """17Lands' average pick per card over the set's whole history (the
    endpoint's default window is recent weeks only, which leaves most cards
    with too few picks)."""
    import datetime
    path = cache_dir("17lands") / f"{set_code.upper()}_PremierDraft_alltime.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        url = ("https://www.17lands.com/card_ratings/data?expansion={}&format=PremierDraft"
               "&start_date={}&end_date={}").format(set_code.upper(), start,
                                                     datetime.date.today().isoformat())
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS),
                                    timeout=60) as response:
            rows = json.load(response)
        path.write_text(json.dumps(rows))
    return {r["name"].split(" // ")[0]: r["avg_pick"] for r in rows
            if r.get("avg_pick") and (r.get("pick_count") or 0) >= 50}
