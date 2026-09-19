"""Data for the format tools: the latest simulated draft of a set, flattened
into one JSON file the tool pages read in the browser.

The tools (card ratings, pick helper, color pairs) are static pages; all they
know is ``site/data/<set>.json``, written here from the newest
``blog/data/<set>-draft-*.json``, plus ``<set>-17lands.json`` once the set is
out (fetched daily by the Pages workflow, see ``site.live``). Re-run a draft
and rebuild the site, and every tool updates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .figures import DraftRun, _colors

MIN_PAIR_GAMES = 60        # a card's record inside one pair is shown from this many games
PRIOR_GAMES = 200          # shrink card win rates toward the set average by this many games
REMOVAL_OPS = {"destroy", "exile", "damage", "fight", "bounce", "stun"}


def latest_run(blog: Path, set_code: str) -> Path | None:
    runs = sorted(blog.glob(f"data/{set_code}-draft-*.json"))
    dated = [p for p in runs if re.fullmatch(rf"{set_code}-draft-\d{{4}}-\d{{2}}-\d{{2}}",
                                             p.stem)]
    return (dated or runs or [None])[-1]


def _ops(node) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        if "op" in node:
            out.add(node["op"])
        for value in node.values():
            out |= _ops(value)
    elif isinstance(node, list):
        for value in node:
            out |= _ops(value)
    return out


def is_removal(entry: dict) -> bool:
    """A spell or ability that can deal with an opposing creature."""
    text = json.dumps(entry.get("targets", [])) + json.dumps(entry.get("modes", []))
    return "creature" in text and bool(_ops(entry) & REMOVAL_OPS)


def deck_profile(run: DraftRun, names: list[str]) -> dict[str, float]:
    # A card compiled differently since the run (or dropped) is skipped.
    spells = [run.data.entries[n] for n in names
              if n in run.data.playable and not run.data.playable[n].is_land]
    return {
        "creatures": sum("Creature" in e["types"] for e in spells),
        "removal": sum(is_removal(e) for e in spells),
        "mv": sum(run.data.playable[e["name"]].cost.mana_value for e in spells)
        / max(1, len(spells)),
        "twos": sum(run.data.playable[e["name"]].cost.mana_value <= 2 and "Creature" in e["types"]
                    for e in spells),
    }


def export(run_path: Path) -> dict:
    run = DraftRun(run_path)
    data = run.data
    mean = run.mean_gih()
    cards = []
    for entry in data.entries.values():
        name = entry["name"]
        spec = data.playable.get(name)
        if spec is not None and spec.is_land and "Basic" in entry.get("supertypes", []):
            continue
        wins, games, ata, picked = run.cards.get(name, (0, 0, 0, 0))
        cards.append({
            "n": name, "cost": entry.get("cost", ""), "c": _colors(data, name) if spec else
            "".join(c for c in "WUBRG" if c in entry.get("color_identity", [])),
            "r": entry.get("rarity", "common")[0], "t": " ".join(entry["types"]),
            "cn": entry.get("collector_number", ""), "o": entry.get("oracle", ""),
            "w": round(wins, 1), "g": games, "ata": ata, "p": picked,
            "ap": list(spec.approximations) if spec else None,
            "un": entry.get("unsupported") if spec is None else None,
            "rm": is_removal(entry),
        })
    pair_cards: dict[str, dict[str, list[float]]] = {}
    for key, (wins, games) in run.raw.get("pair_cards", {}).items():
        name, pair = key.rsplit("|", 1)
        if games >= MIN_PAIR_GAMES and name in data.playable and not data.playable[name].is_land:
            pair_cards.setdefault(name, {})[pair] = [round(wins, 1), games]
    pairs = {}
    deck_count = len(run.decks)
    for pair, (wins, games) in run.records.items():
        idx = [i for i, (p, _) in enumerate(run.decks) if p == pair]
        profiles = [deck_profile(run, run.decks[i][1]) for i in idx]
        avg = {k: round(sum(p[k] for p in profiles) / len(profiles), 2) for k in profiles[0]}
        recs = run.raw.get("deck_records", [])
        best = max(idx, key=lambda i: recs[i][0] / recs[i][1] if recs and recs[i][1] else 0)
        pairs[pair] = {"w": round(wins, 1), "g": games, "decks": len(idx),
                       "share": round(len(idx) / deck_count, 4), "profile": avg,
                       "sample": {"cards": run.decks[best][1],
                                  "record": recs[best] if recs else None}}
    return {
        "set": run.raw["set"], "name": data.name, "run": run_path.stem[-10:],
        "spoiler": data.meta.get("spoiler", ""),
        "pods": run.raw["pods"], "games": run.raw["games"], "decks": deck_count,
        "mean": round(mean, 4), "prior": PRIOR_GAMES,
        "play": run.raw.get("on_the_play"), "cards": cards, "pairs": pairs,
        "pc": pair_cards,
    }


def write(blog: Path, site: Path, set_code: str) -> Path | None:
    run = latest_run(blog, set_code)
    if run is None:
        return None
    out = site / "data" / f"{set_code}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(export(run), separators=(",", ":")))
    return out
