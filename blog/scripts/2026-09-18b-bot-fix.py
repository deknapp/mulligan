"""Figures for the second 2026-09-18 post: the upkeep/fetch-land bot fix.

Data (all in blog/data):
- hob-draft-2026-09-18-{before,after}-fix.json: `mulligan draft --set hob --pods 60
  --games 60000` with the heuristic before and after commit 9dc58d9.
- fra-draft-2026-09-18.json (first post) and fra-draft-2026-09-18-after-fix.json.
"""

from pathlib import Path

from mulligan.site import figures as f

HERE = Path(__file__).resolve().parents[1]
D, OUT, P = HERE / "data", HERE / "figures", "2026-09-18b-"

f.write(OUT, P + "hob-pairs", f.sim_vs_real_pairs({
    "before": D / "hob-draft-2026-09-18-before-fix.json",
    "after": D / "hob-draft-2026-09-18-after-fix.json"}))
f.write(OUT, P + "fra-pairs", f.compare_pairs({
    "first post": D / "fra-draft-2026-09-18.json",
    "fixed bots": D / "fra-draft-2026-09-18-after-fix.json"},
    "Reality Fracture color pairs, before and after the fix"))
new = f.DraftRun(D / "fra-draft-2026-09-18-after-fix.json")
ways = sorted(n for n in new.cards if n.startswith("Way of the") and n in new.data.playable)
f.write(OUT, P + "ways", f.card_intervals(new, ways, "The Way of the ... cycle, fixed bots"))
for n in ways:
    w, g, ata, _ = new.cards[n]
    print(f"{n}: {w / g:.3f} over {g} games, pick {ata:.1f}")
