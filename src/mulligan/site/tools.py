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

from ..limited.synergy import synergies
from .figures import DraftRun, _colors

MIN_PAIR_GAMES = 60        # a card's record inside one pair is shown from this many games
PRIOR_GAMES = 200          # shrink card win rates toward the set average by this many games
PAIRS_PER_CARD = 12        # card-pair synergies kept per card, from each end
REMOVAL_OPS = {"destroy", "exile", "damage", "fight", "bounce", "stun"}


def latest_run(blog: Path, set_code: str) -> Path | None:
    runs = sorted(blog.glob(f"data/{set_code}-draft-*.json"))
    dated = [p for p in runs if re.fullmatch(rf"{set_code}-draft-\d{{4}}-\d{{2}}-\d{{2}}",
                                             p.stem)]
    return (dated or runs or [None])[-1]


def pair_cells(run_path: Path) -> dict[str, list[float]]:
    """The run's card-pair tally plus every extra simulation saved beside it as
    ``<set>-pairs-*.json`` (more games for synergy alone, so the card ratings
    and the primer's figures stay those of the main run)."""
    cells = {k: list(v) for k, v in
             json.loads(run_path.read_text()).get("card_pairs", {}).items()}
    set_code = run_path.stem.split("-")[0]
    for extra in sorted(run_path.parent.glob(f"{set_code}-pairs-*.json")):
        for key, cell in json.loads(extra.read_text()).get("card_pairs", {}).items():
            mine = cells.setdefault(key, [0.0] * 8)
            for i, v in enumerate(cell):
                mine[i] += v
    return cells


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
    """A spell, trigger or ability that can deal with an opposing creature:
    a removal op (or a -X/-X that shrinks toughness) aimed at targets that can
    be a creature. Targets carry down from a spell or mode to its effects."""
    def hits(targets) -> bool:
        for t in targets:
            sel = json.dumps(t)
            if "any_target" in sel:
                return True
            mine = ("yours" in sel and "theirs" not in sel) or "zone=" in sel
            if mine or "!type=creature" in sel:
                continue
            if any(k in sel for k in ("creature", "nonland", "permanent")):
                return True
        return False

    def kills(eff: dict) -> bool:
        op = eff.get("op")
        if op == "pump":
            return (eff.get("toughness") or 0) < 0
        if op == "set_base_pt":
            return eff.get("toughness") == 0
        return op in REMOVAL_OPS

    def walk(node, targets) -> bool:
        if isinstance(node, list):
            return any(walk(v, targets) for v in node)
        if not isinstance(node, dict):
            return False
        targets = node.get("targets", targets)
        for eff in node.get("effects", []):
            if kills(eff) and hits(targets):
                return True
            if (eff.get("op") == "sacrifice" and "opponent" in str(eff.get("who"))
                    and "creature" in str(eff.get("filter"))):
                return True   # an edict needs no target
        return any(walk(v, targets) for k, v in node.items() if k not in ("effects", "targets"))

    return walk(entry, [])


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


def synergy_export(cells: dict, index: dict[str, int]) -> dict | None:
    """Card-pair synergies for the site, compact: [card a, card b, estimate and
    standard error in tenths of a point, games with both in the deck]. Keeps
    each card's strongest pairs both ways, which is what the page can show, and
    ``a``: every pair's estimate of at least a tenth of a point, flat as
    [a, b, estimate, a, b, estimate, ...], for scoring a pick against a whole pool."""
    result = synergies(cells)
    rows = [r for r in result["pairs"] if r["a"] in index and r["b"] in index]
    if not rows:
        return None
    keep: set[int] = set()
    by_card: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_card.setdefault(r["a"], []).append(i)
        by_card.setdefault(r["b"], []).append(i)
    for ids in by_card.values():   # rows are sorted best first
        keep.update(ids[:PAIRS_PER_CARD] + ids[-PAIRS_PER_CARD:])
    every = [x for r in rows if round(1000 * r["est"])
             for x in (index[r["a"]], index[r["b"]], round(1000 * r["est"]))]
    return {"tau": round(1000 * result["tau"]) / 10, "pairs": len(rows), "a": every,
            "p": [[index[r["a"]], index[r["b"]], round(1000 * r["est"]),
                   round(1000 * r["se"]), r["n"]] for i, r in enumerate(rows) if i in keep]}


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
        "syn": synergy_export(pair_cells(run_path),
                              {c["n"]: i for i, c in enumerate(cards)}),
    }


def write(blog: Path, site: Path, set_code: str) -> Path | None:
    run = latest_run(blog, set_code)
    if run is None:
        return None
    out = site / "data" / f"{set_code}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(export(run), separators=(",", ":")))
    return out
