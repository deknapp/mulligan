"""Figures for the 2026-09-18 post: first look at Reality Fracture draft.

    uv run python blog/scripts/2026-09-18-fra-draft.py

Reads blog/data/fra-draft-2026-09-18.json (``mulligan draft --set fra --pods 60
--games 60000``) and writes blog/figures/2026-09-18-*. Prints the numbers the
post quotes.
"""

from pathlib import Path

from mulligan.site import figures as f

HERE = Path(__file__).resolve().parents[1]
PREFIX = "2026-09-18-"
run = f.DraftRun(HERE / "data" / "fra-draft-2026-09-18.json")
out = HERE / "figures"

f.write(out, PREFIX + "archetypes", f.archetypes(run))
f.write(out, PREFIX + "colors", f.color_presence(run))
svg, under, over = f.pick_vs_win(run)
f.write(out, PREFIX + "pick-vs-win", svg)
f.write(out, PREFIX + "underdrafted", f.card_table(run, under))
f.write(out, PREFIX + "overdrafted", f.card_table(run, over))
f.write(out, PREFIX + "commons", f.best_commons(run))
svg, mech = f.mechanics(run)
f.write(out, PREFIX + "mechanics", svg)
beats, trails = f.text_vs_play(run)
f.write(out, PREFIX + "beats-text", f.card_table(run, beats))
f.write(out, PREFIX + "trails-text", f.card_table(run, trails))

print("decks", len(run.decks), "mean gih", round(run.mean_gih(), 4))
for pair, (w, g) in sorted(run.records.items(), key=lambda kv: -kv[1][0] / kv[1][1]):
    lo, hi = f.wilson(w, int(g))
    print(f"  {pair} {w / g:.3f} [{lo:.3f},{hi:.3f}] games={int(g)} "
          f"decks={sum(1 for p, _ in run.decks if p == pair)}")
stats = {r[0]: r for r in run.rated(1)}
for label, names in (("under", under), ("over", over), ("beats text", beats),
                     ("trails text", trails)):
    print(label)
    for n in names:
        _, rate, games, ata = stats[n]
        print(f"  {n}: {rate:.3f} ata={ata:.1f} games={games}")
print("mechanics", {k: (round(v[0] * 100, 2), v[1]) for k, v in mech.items()})
