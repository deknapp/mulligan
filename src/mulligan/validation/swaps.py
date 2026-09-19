"""Does the simulator get *build* decisions right?

Choosing between versions of one deck is the decision a player most often
faces, and it is where the simulator's pilot biases should mostly cancel (the
same pilot plays both versions). The test: random 3-card swaps in real 17Lands
decks (cut three spells, add three on-color, exactly-modelled commons or
uncommons). For each swap, both versions play the same field of real decks on
the same seeds; the simulated change in win rate is compared with the 17Lands
deck model's change for the same swap.

    python -m mulligan.validation.swaps

HOB result (2026-09-18): 120 swaps x 240 paired games; Spearman +0.338,
Pearson +0.357; same direction on 66 of 86 swaps where the model sees a real
difference (chance would give about 43).
"""

from __future__ import annotations

import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor


def _play(set_code: str, versions: dict, field_names: list, jobs: list) -> dict:
    from ..agents import HeuristicAgent
    from ..match import play_game
    from .decks import _specs
    built = {k: _specs(set_code, v)[0] for k, v in versions.items()}
    field = [_specs(set_code, f)[0] for f in field_names]
    out: dict = defaultdict(float)
    for key, fi, seed in jobs:
        for seat in (0, 1):
            pair = (built[key], field[fi]) if seat == 0 else (field[fi], built[key])
            result = play_game((HeuristicAgent(), HeuristicAgent()), pair, seed=seed,
                               on_the_play=seed % 2)
            out[key] += 0.5 if result.winner is None else float(result.winner == seat)
    return dict(out)


def _swap(deck: dict[str, int], rng: random.Random, info: dict, commons: list[str],
          playable: dict) -> dict[str, int] | None:
    colors = {c for n in deck for opts in info.get(n, (True, 0, 0, frozenset()))[3]
              for c in opts}
    spells = [n for n in deck if not info.get(n, (True,))[0] and n in playable]
    candidates = [n for n in commons if n not in deck
                  and all(options & colors for options in info[n][3])]
    if len(spells) < 3 or len(candidates) < 3:
        return None
    new = dict(deck)
    for name in rng.sample(spells, 3):
        new[name] -= 1
        if not new[name]:
            del new[name]
    for name in rng.sample(candidates, 3):
        new[name] = new.get(name, 0) + 1
    return new


def validate_swaps(set_code: str = "hob", n_swaps: int = 120, field_size: int = 20,
                   games: int = 12, seed: int = 3, workers: int = 9) -> dict:
    from ..cards.sets import load_set
    from ..deckmodel import DeckModel, _card_info
    from .decks import real_decks
    from .seventeen import pearson, spearman
    rng = random.Random(seed)
    data = load_set(set_code)
    info = _card_info(set_code)
    model = DeckModel.load(set_code)
    decks = rng.sample(real_decks(set_code), n_swaps + field_size)
    field = [d.names for d in decks[:field_size]]
    commons = [n for n, e in data.entries.items()
               if e.get("rarity") in ("common", "uncommon") and n in data.playable
               and not data.playable[n].is_land and not data.playable[n].approximations]
    versions: dict = {}
    deltas = []
    for k, real in enumerate(decks[field_size:]):
        new = _swap(real.names, rng, info, commons, data.playable)
        if new is None:
            continue
        versions[(k, "a")], versions[(k, "b")] = real.names, new
        deltas.append((k, model.score(new) - model.score(real.names)))
    jobs = []
    for k, _ in deltas:
        for fi in range(field_size):
            for _ in range(games // 2):
                s = rng.randrange(1 << 30)
                jobs += [((k, "a"), fi, s), ((k, "b"), fi, s)]
    parts = [jobs[i::workers * 4] for i in range(workers * 4)]
    totals: dict = defaultdict(float)
    with ProcessPoolExecutor(workers) as ex:
        for res in ex.map(_play, [set_code] * len(parts), [versions] * len(parts),
                          [field] * len(parts), parts):
            for key, value in res.items():
                totals[key] += value
    n_games = field_size * games
    sim = [(totals[(k, "b")] - totals[(k, "a")]) / n_games for k, _ in deltas]
    mod = [m for _, m in deltas]
    decided = [(s, m) for s, m in zip(sim, mod, strict=True) if abs(m) > 0.05]
    agree = sum(1 for s, m in decided if (s > 0) == (m > 0))
    return {"swaps": len(deltas), "games_per_swap": n_games,
            "spearman": spearman(sim, mod), "pearson": pearson(sim, mod),
            "direction_agrees": agree, "direction_decided": len(decided)}


if __name__ == "__main__":  # pragma: no cover
    print(validate_swaps())
