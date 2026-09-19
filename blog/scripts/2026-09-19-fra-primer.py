"""Figures and numbers for the Reality Fracture primer (2026-09-19).

    uv run python blog/scripts/2026-09-19-fra-primer.py

Reads blog/data/fra-draft-2026-09-19.json, writes blog/figures/2026-09-19-*.svg
and prints the numbers the post quotes.
"""

from __future__ import annotations

import math
from pathlib import Path

from mulligan.site import charts
from mulligan.site.figures import (
    DraftRun,
    _colors,
    _rarity,
    archetypes,
    best_commons,
    card_intervals,
    card_table,
    color_presence,
    mechanics,
    pick_vs_win,
    wilson,
    write,
)
from mulligan.site.tools import deck_profile

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "blog/data/fra-draft-2026-09-19.json"
OUT = ROOT / "blog/figures"
P = "2026-09-19-"


def shrunk(run: DraftRun, name: str, prior: int = 200) -> float:
    wins, games, _, _ = run.cards[name]
    return (wins + prior * run.mean_gih()) / (games + prior)


def deck_shape(run: DraftRun) -> dict:
    """What winning decks look like. Deck records against deck features, with
    the deck's average card quality held fixed (least squares), so 'more
    two-drops' isn't just 'better cards'."""
    rows = []
    for (pair, names), (wins, games) in zip(run.decks, run.raw["deck_records"], strict=True):
        if not games:
            continue
        prof = deck_profile(run, names)
        spells = [n for n in names if n in run.data.playable and not run.data.playable[n].is_land]
        quality = sum(shrunk(run, n) for n in spells if n in run.cards) / len(spells)
        rows.append((wins / games, games, quality, prof))
    feats = ["creatures", "twos", "removal", "mv"]

    def fit(keys):
        # Weighted least squares via normal equations, small enough to do by hand.
        xs = [[1.0, r[2]] + [r[3][k] for k in keys] for r in rows]
        ws = [r[1] for r in rows]
        ys = [r[0] for r in rows]
        n = len(xs[0])
        a = [[sum(w * x[i] * x[j] for x, w in zip(xs, ws, strict=True)) for j in range(n)]
             for i in range(n)]
        b = [sum(w * x[i] * y for x, w, y in zip(xs, ws, ys, strict=True)) for i in range(n)]
        inv = _inverse(a)
        beta = [sum(inv[i][j] * b[j] for j in range(n)) for i in range(n)]
        resid = [y - sum(bi * xi for bi, xi in zip(beta, x, strict=True))
                 for x, y in zip(xs, ys, strict=True)]
        s2 = sum(w * e * e for w, e in zip(ws, resid, strict=True)) / (len(xs) - n)
        se = [math.sqrt(inv[i][i] * s2) for i in range(n)]
        return dict(zip(["const", "quality", *keys], zip(beta, se, strict=True), strict=True))

    buckets = {}
    for key, cuts in (("creatures", [13, 15, 17, 19]), ("twos", [3, 5, 7]),
                      ("removal", [1, 2, 3, 4])):
        prev, out = -1, []
        for cut in [*cuts, 99]:
            sel = [r for r in rows if prev < r[3][key] <= cut]
            if len(sel) >= 15:
                w = sum(r[0] * r[1] for r in sel)
                g = sum(r[1] for r in sel)
                label = (f"{prev + 1}–{cut}" if cut != 99 else f"{prev + 1}+") if prev >= 0 \
                    else f"≤{cut}"
                out.append((label, w / g, *wilson(w, g), len(sel)))
            prev = cut
        buckets[key] = out
    return {"fit": fit(feats), "buckets": buckets, "decks": len(rows),
            "means": {k: sum(r[3][k] for r in rows) / len(rows) for k in feats}}


def _inverse(m):
    n = len(m)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(n):
            if r != col:
                f = a[r][col]
                a[r] = [v - f * c for v, c in zip(a[r], a[col], strict=True)]
    return [row[n:] for row in a]


def bucket_chart(rows, label, x_label):
    return charts.interval_bars([(f"{r[0]} {x_label} ({r[4]} decks)", r[1], r[2], r[3], "C")
                                 for r in rows], label, reference=0.5, left=230)


def main() -> None:
    run = DraftRun(RUN)
    mean = run.mean_gih()
    write(OUT, P + "pairs", archetypes(run))
    write(OUT, P + "colors", color_presence(run))
    write(OUT, P + "commons", best_commons(run))
    svg, summary = mechanics(run)
    write(OUT, P + "mechanics", svg)
    svg, under, over = pick_vs_win(run)
    write(OUT, P + "pick-vs-win", svg)
    shape = deck_shape(run)
    write(OUT, P + "creatures", bucket_chart(shape["buckets"]["creatures"],
                                             "Deck win rate by creature count", "creatures"))
    write(OUT, P + "twos", bucket_chart(shape["buckets"]["twos"],
                                        "Deck win rate by two-drop creatures", "two-drops"))

    print(f"mean GIH {mean:.3f}; decks {len(run.decks)}; games {run.raw['games']}")
    w, g = run.raw["on_the_play"]
    print(f"on the play {w / g:.3f} ± {1.96 * math.sqrt(w / g * (1 - w / g) / g):.3f}")
    for pair, (w, g) in sorted(run.records.items(), key=lambda kv: -kv[1][0] / kv[1][1]):
        lo, hi = wilson(w, int(g))
        share = sum(p == pair for p, _ in run.decks) / len(run.decks)
        print(f"  {pair} {w / g:.3f} ({lo:.3f}-{hi:.3f}) share {share:.2f}")
    print("mechanics", {k: (round(v[0] * 100, 1), v[1]) for k, v in summary.items()})
    print("deck shape (per unit, quality held fixed):")
    for k, (b, se) in shape["fit"].items():
        print(f"  {k:10s} {b * 100:+.2f} pts ± {1.96 * se * 100:.2f}")
    print("  means", {k: round(v, 2) for k, v in shape["means"].items()})
    for key, rows in shape["buckets"].items():
        print(" ", key, [(r[0], round(r[1], 3), r[4]) for r in rows])

    rated = {n: (r, g, ata) for n, r, g, ata in run.rated(300)}

    def clears(name):
        wins, games, _, _ = run.cards[name]
        lo, hi = wilson(wins, int(games))
        return "above" if lo > mean else "below" if hi < mean else "noise"

    print("under-drafted:", [(n, round(rated[n][0], 3), round(rated[n][2], 1), clears(n))
                            for n in under])
    print("over-drafted:", [(n, round(rated[n][0], 3), round(rated[n][2], 1), clears(n))
                           for n in over])
    for rarity in ("common", "uncommon"):
        top = sorted((n for n in rated if _rarity(run.data, n) == rarity),
                     key=lambda n: -shrunk(run, n))[:12]
        print(f"top {rarity}s:", [(n, _colors(run.data, n), round(rated[n][0], 3),
                                   round(rated[n][2], 1)) for n in top])
    bottom = sorted((n for n in rated if _rarity(run.data, n) == "common"),
                    key=lambda n: shrunk(run, n))[:8]
    print("worst commons:", [(n, _colors(run.data, n), round(rated[n][0], 3),
                              round(rated[n][2], 1)) for n in bottom])
    top_all = sorted(rated, key=lambda n: -shrunk(run, n))[:15]
    print("top overall:", [(n, _rarity(run.data, n)[0], round(rated[n][0], 3)) for n in top_all])
    write(OUT, P + "uncommons", card_table(run, [
        "Thalia, the Survivor", "Tetsuko Umezawa, Pursuer", "Kiora of Fire and Ashes",
        "Vigorbloom Vanguard", "Bloombrute", "Your Fate Ends Here", "Desperate Futurescribe",
        "Titanbones, Towering Heart"]))
    write(OUT, P + "sleepers", card_table(run, [
        "Way of the Warlord", "Way of the Wildspeaker", "Tam's Resistance",
        "Garruk, Curse Breaker", "Overwrite the Multiverse"], note={
        "Way of the Warlord": "A 5-loyalty Jace that can -4 to kill a creature and hit face",
        "Way of the Wildspeaker": "A 7-loyalty Jace that makes a 4/4 trampler the turn it lands",
        "Tam's Resistance": "Two mana: a counter and a 4-loyalty Jace, in green or blue",
        "Garruk, Curse Breaker": "A 4/4 every other turn, and cards off your big creatures",
        "Overwrite the Multiverse": "Six-mana wrath that leaves you a huge Jace",
    }))
    write(OUT, P + "traps", card_table(run, [
        "Proft, Sinister Mastermind", "Ghalta the Unstoppable", "Flickering Hound",
        "Germinate Recruits", "Loyal Tutor"], note={
        "Proft, Sinister Mastermind": "Needs seven cards in your graveyard to cast",
        "Ghalta the Unstoppable": "Simplified: no cost reduction here. Discount this one",
        "Flickering Hound": "A 2/2 for four whose trigger rarely matters",
        "Germinate Recruits": "Blank unless you gained life this turn",
        "Loyal Tutor": "Does nothing in a deck without planeswalker cards",
    }))
    ways = [n for n in rated if n.startswith("Way of the")]
    if ways:
        write(OUT, P + "ways", card_intervals(run, ways, "The Way of the ... cycle"))
        print("ways:", sorted(((n, round(rated[n][0], 3), rated[n][1]) for n in ways),
                              key=lambda t: -t[1]))
    pc = run.raw.get("pair_cards", {})
    for pair in sorted(run.records, key=lambda p: -run.records[p][0] / run.records[p][1]):
        cards = []
        for key, (w, g) in pc.items():
            name, p = key.rsplit("|", 1)
            if p == pair and g >= 150 and name in run.data.playable \
                    and not run.data.playable[name].is_land:
                cards.append((name, (w + 100 * mean) / (g + 100), g))
        cards.sort(key=lambda t: -t[1])
        print(f"  best in {pair}:", [(n, round(r, 3), _rarity(run.data, n)[0])
                                     for n, r, _ in cards[:8]])


if __name__ == "__main__":
    main()
