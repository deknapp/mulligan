"""Sealed pools with a real set's rarity distribution.

A Play Booster is approximated as 7 commons, 3 uncommons, one rare-or-mythic
(mythic 1 in 7), one wildcard (common 60% / uncommon 30% / rare 10%) and one
land-slot card. Six packs make a sealed pool. Every card in the set can appear,
including ones the engine cannot play — exactly as in a real pool, the builder
simply cannot use those.
"""

from __future__ import annotations

import random

from ..cards.sets import SetData

PACKS_PER_POOL = 6


def _by_rarity(data: SetData) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"common": [], "uncommon": [], "rare": [], "mythic": [],
                                     "land": []}
    for name, entry in data.entries.items():
        types = entry.get("types", [])
        if "Basic" in entry.get("supertypes", ()):
            continue
        if "Land" in types and entry.get("rarity") == "common":
            buckets["land"].append(name)
            continue
        buckets.setdefault(entry.get("rarity", "common"), []).append(name)
    return buckets


def booster(data: SetData, rng: random.Random) -> list[str]:
    b = _by_rarity(data)
    pack = rng.sample(b["common"], min(7, len(b["common"])))
    pack += rng.sample(b["uncommon"], min(3, len(b["uncommon"])))
    rare_slot = "mythic" if b["mythic"] and rng.random() < 1 / 7 else "rare"
    pack.append(rng.choice(b[rare_slot] or b["rare"]))
    roll = rng.random()
    wild = "common" if roll < 0.6 else "uncommon" if roll < 0.9 else "rare"
    pack.append(rng.choice(b[wild]))
    if b["land"]:
        pack.append(rng.choice(b["land"]))
    return pack


def sealed_pool(data: SetData, seed: int, packs: int = PACKS_PER_POOL) -> list[str]:
    rng = random.Random(seed)
    pool: list[str] = []
    for _ in range(packs):
        pool.extend(booster(data, rng))
    return pool
