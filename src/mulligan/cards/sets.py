"""Compiled sets: loading, coverage, and the booster slots decks are built from.

A set lives in ``cards/data/<code>.json``: the compiler's output, checked in so
that nobody who runs the simulator needs an API key or a network connection.
``load_set`` turns it into ``CardSpec`` objects and reports, honestly, how much
of the set the engine can play.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..engine.card import CardSpec
from .schema import CardDataError, card

DATA_DIR = Path(__file__).parent / "data"


@dataclass
class SetData:
    code: str
    name: str
    playable: dict[str, CardSpec] = field(default_factory=dict)
    entries: dict[str, dict] = field(default_factory=dict)
    unsupported: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def rarity(self, name: str) -> str:
        return self.entries.get(name, {}).get("rarity", "common")

    @property
    def approximate(self) -> dict[str, list[str]]:
        return {n: list(s.approximations) for n, s in self.playable.items() if s.approximations}

    def coverage(self) -> str:
        total = len(self.entries)
        by_rarity: dict[str, list[int]] = {}
        for name, entry in self.entries.items():
            bucket = by_rarity.setdefault(entry.get("rarity", "?"), [0, 0])
            bucket[1] += 1
            if name in self.playable:
                bucket[0] += 1
        lines = [f"{self.name} ({self.code.upper()}): {len(self.playable)}/{total} cards "
                 f"playable ({len(self.playable) / max(1, total):.0%}), "
                 f"{len(self.approximate)} with noted approximations"]
        for rarity in ("common", "uncommon", "rare", "mythic"):
            if rarity in by_rarity:
                ok, n = by_rarity[rarity]
                lines.append(f"  {rarity:9s} {ok:3d}/{n:<3d} ({ok / n:.0%})")
        if self.errors:
            lines.append(f"  load errors: {len(self.errors)}")
        return "\n".join(lines)


def set_path(code: str) -> Path:
    return DATA_DIR / f"{code.lower()}.json"


def available_sets() -> list[str]:
    return sorted(p.stem for p in DATA_DIR.glob("*.json"))


@lru_cache(maxsize=8)
def load_set(code: str) -> SetData:
    path = set_path(code)
    if not path.exists():
        raise FileNotFoundError(
            f"set {code!r} is not compiled; available: {', '.join(available_sets()) or 'none'}")
    raw = json.loads(path.read_text())
    data = SetData(code=raw.get("code", code), name=raw.get("name", code),
                   meta={k: v for k, v in raw.items() if k != "cards"})
    for entry in raw["cards"]:
        name = entry["name"]
        data.entries[name] = entry
        if entry.get("unsupported"):
            data.unsupported[name] = entry["unsupported"]
            continue
        try:
            data.playable[name] = card(entry)
        except CardDataError as exc:
            data.errors[name] = str(exc)
    return data
