"""Figures and numbers for the Reality Fracture primer.

    uv run python blog/scripts/fra-primer.py

The primer is one living page, not a dated post, so this reads whatever the
newest blog/data/fra-draft-*.json is and always writes the same
blog/figures/fra-primer-*.svg. Rerun a draft, rerun this, and the page's
charts are the new numbers; the prose above them still has to be re-read by
hand against what this prints.
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
from mulligan.site.tools import deck_profile, latest_run

ROOT = Path(__file__).resolve().parents[2]
RUN = latest_run(ROOT / "blog", "fra")
OUT = ROOT / "blog/figures"
P = "fra-primer-"


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


PAIR_NAMES = {"WU": "Azorius", "UB": "Dimir", "BR": "Rakdos", "RG": "Gruul", "WG": "Selesnya",
              "WB": "Orzhov", "UR": "Izzet", "BG": "Golgari", "WR": "Boros", "UG": "Simic"}
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def color_records(run: DraftRun) -> dict[str, tuple[float, int]]:
    out = {}
    for c in "WUBRG":
        w = sum(r[0] for p, r in run.records.items() if c in p)
        g = sum(r[1] for p, r in run.records.items() if c in p)
        out[c] = (w, int(g))
    return out


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def expert_versus(run: DraftRun, kind: str):
    """Experts' expected strength per pair (or color) on the simulator's
    win-rate scale. Each host scored -2..+2 (experts.rate); the hosts' mean is
    mapped linearly so the experts' spread across pairs equals the
    simulation's. The level and spread are borrowed; the order and the gaps
    between pairs are the experts' own."""
    from mulligan import experts
    exp = experts.expected("fra")[kind]
    recs = run.records if kind == "pairs" else color_records(run)
    keys = [k for k in recs if k in exp]
    sim = {k: recs[k][0] / recs[k][1] for k in recs}
    xs = [exp[k]["score"] for k in keys]
    ys = [sim[k] for k in keys]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / len(xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / len(ys))
    scale = sy / sx if sx else 0.0

    def to_wr(score):
        return my + (score - mx) * scale

    rows = []
    for k in sorted(recs, key=lambda k: -sim[k]):
        w, g = recs[k]
        lo, hi = wilson(w, int(g))
        name = f"{k} {PAIR_NAMES[k]}" if kind == "pairs" else COLOR_NAMES[k]
        if k in exp:
            hs = list(exp[k]["hosts"].values())
            who = ", ".join(f"{h.split('|')[1]} {v:+d}" for h, v in exp[k]["hosts"].items())
            rows.append((f"{name} ({len(hs)})", sim[k], lo, hi, k, to_wr(exp[k]["score"]),
                         to_wr(min(hs)), to_wr(max(hs)), f"Scores: {who}"))
        else:
            rows.append((f"{name} (0)", sim[k], lo, hi, k, None, None, None, ""))
    rho = spearman(xs, ys)
    label = ("Color pairs" if kind == "pairs" else "Colors") + ": simulation vs. experts"
    svg = charts.versus(rows, label, reference=my, left=150 if kind == "pairs" else 110,
                        legend=("Simulation (95% interval)", "Experts (band: range of hosts)"))
    return svg, rho, {k: (exp[k]["score"], to_wr(exp[k]["score"])) for k in keys}


def expert_grades(run: DraftRun):
    """Scatter: each card's average expert grade against its simulated win
    rate when drawn; the biggest disagreements are labelled."""
    import json

    from mulligan import experts
    data = json.loads((ROOT / "blog/data/fra-experts.json").read_text())["cards"]
    pts = []
    for name, x in data.items():
        g = [experts.grade_points(r["grade"]) for r in x["grades"]]
        g = [v for v in g if v is not None]
        if len(g) < 2 or name not in run.cards or run.cards[name][1] < 300:
            continue
        pts.append((name, sum(g) / len(g), shrunk(run, name),
                    charts.color_key(_colors(run.data, name))))
    xs, ys = [p[1] for p in pts], [p[2] for p in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True)) / \
        sum((a - mx) ** 2 for a in xs)
    resid = {p[0]: p[2] - (my + slope * (p[1] - mx)) for p in pts}
    sim_higher = sorted(resid, key=lambda k: -resid[k])[:5]
    experts_higher = sorted(resid, key=lambda k: resid[k])[:5]
    letters = experts.GRADES[::-1]

    def letter(v):
        i = round(v)
        return letters[i] if 0 <= i < len(letters) and abs(v - i) < 0.01 else ""

    svg = charts.scatter(pts, "Expert grade vs. simulated win rate when drawn",
                         "Average expert grade", "Simulated win rate when drawn",
                         callouts=set(sim_higher + experts_higher), x_fmt=letter,
                         y_ref=run.mean_gih())
    return svg, spearman(xs, ys), n, sim_higher, experts_higher


def depth_by_color(run: DraftRun):
    """Per color: how many of its commons beat the set's average card when
    drawn. The commons are what you see all draft, so this is how deep a color
    runs."""
    mean = run.mean_gih()
    rated = {n for n, _, _, _ in run.rated(300)}
    counts = {c: [0, 0] for c in "WUBRG"}
    for n in rated:
        cols = _colors(run.data, n)
        if len(cols) == 1 and _rarity(run.data, n) == "common":
            counts[cols][1] += 1
            counts[cols][0] += shrunk(run, n) > mean
    rows = sorted(((f"{COLOR_NAMES[c]} ({v[0]} of {v[1]})", v[0], c)
                   for c, v in counts.items()), key=lambda r: -r[1])
    return charts.bars(rows, "Commons that beat the average card, by color",
                       fmt=lambda v: f"{v:.0f}"), counts


def glance(run: DraftRun, pair_exp: dict, color_rho: float, pair_rho: float) -> str:
    """The format in six numbers, as tiles."""
    from html import escape
    recs = {p: w / g for p, (w, g) in run.records.items()}
    best = max(recs, key=recs.get)
    worst = min(recs, key=recs.get)
    exp_best = max(pair_exp, key=lambda k: pair_exp[k][0])
    w, g = run.raw["on_the_play"]
    rated = {n: r for n, r, _, _ in run.rated(300)}
    commons = [n for n in rated if _rarity(run.data, n) == "common"]
    top_common = max(commons, key=lambda n: shrunk(run, n))
    tiles = [
        ("Best pair (sim)", f"{best} {PAIR_NAMES[best]}", f"{recs[best]:.1%} win rate"),
        ("Experts' favorite", f"{exp_best} {PAIR_NAMES[exp_best]}",
         "highest average host score"),
        ("Pair to avoid (sim)", f"{worst} {PAIR_NAMES[worst]}", f"{recs[worst]:.1%} win rate"),
        ("Play or draw", "Play", f"{w / g:.1%} on the play"),
        ("Best common (sim)", top_common, f"{rated[top_common]:.1%} when drawn"),
        ("Experts vs. sim on pairs", f"ρ = {pair_rho:+.2f}",
         "rank agreement (1 = same order)"),
    ]
    return '<div class="glance">' + "".join(
        f'<div><span class="k">{escape(k)}</span><strong>{escape(v)}</strong>'
        f'<span class="s">{escape(s)}</span></div>' for k, v, s in tiles) + "</div>"


def main() -> None:
    if RUN is None:
        raise SystemExit("no blog/data/fra-draft-*.json to read")
    print(f"reading {RUN.name}")
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

    svg, pair_rho, pair_exp = expert_versus(run, "pairs")
    write(OUT, P + "pairs-experts", svg)
    svg, color_rho, color_exp = expert_versus(run, "colors")
    write(OUT, P + "colors-experts", svg)
    print(f"experts vs sim, pairs rho {pair_rho:+.2f}:",
          {k: (round(v[0], 2), round(v[1], 3)) for k, v in pair_exp.items()})
    print(f"experts vs sim, colors rho {color_rho:+.2f}:",
          {k: (round(v[0], 2), round(v[1], 3)) for k, v in color_exp.items()})
    svg, grade_rho, graded, sim_higher, experts_higher = expert_grades(run)
    write(OUT, P + "grades", svg)
    print(f"expert grade vs sim: rho {grade_rho:+.2f} over {graded} cards")
    print("  sim likes more:", sim_higher)
    print("  experts like more:", experts_higher)
    svg, depth = depth_by_color(run)
    write(OUT, P + "depth", svg)
    print("commons above average (above, of):", depth)
    write(OUT, P + "glance", glance(run, pair_exp, color_rho, pair_rho))

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
        "Kiora of Fire and Ashes", "Thalia, the Survivor", "Tetsuko Umezawa, Pursuer",
        "Ghalta the Unstoppable", "Craftwork Crusher", "Twisted Fates",
        "Ghalta the Immovable", "Your Fate Ends Here"]))
    write(OUT, P + "sleepers", card_table(run, [
        "Garruk, Curse Breaker", "Garruk, Veiled Butcher", "Overwrite the Multiverse",
        "Way of the Wildspeaker", "Multiply by Zero"], note={
        "Garruk, Curse Breaker": "A 4/4 every other turn, and cards off your big creatures",
        "Garruk, Veiled Butcher": "Kills a creature on the way down and keeps making them",
        "Overwrite the Multiverse": "Six-mana wrath that leaves you a huge Jace",
        "Way of the Wildspeaker": "A 7-loyalty Jace that makes a 4/4 trampler the turn it lands",
        "Multiply by Zero": "Cheap interaction the bots keep passing",
    }))
    write(OUT, P + "traps", card_table(run, [
        "Emrakul, the Exigent Doom", "Proft, Sinister Mastermind", "Germinate Recruits",
        "Flickering Hound", "Return to the Light Realms"], note={
        "Emrakul, the Exigent Doom": "Ten mana. The bots take it first pick and lose with it",
        "Proft, Sinister Mastermind": "Needs seven cards in your graveyard to cast",
        "Germinate Recruits": "Blank unless you gained life this turn",
        "Flickering Hound": "A 2/2 for four whose trigger rarely matters",
        "Return to the Light Realms": "Seven mana for an effect that does not stabilise",
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
