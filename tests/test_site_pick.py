"""The website's pick helper (site/tools.js) ranks a pack exactly as `mulligan
live` does (pick_advice.advise): on the podcast hosts' grades alone
(expert_ratings), and, for the pool rules on their own, on simulated ratings."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from mulligan.pick_advice import advise, build_ratings, expert_ratings

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data" / "fra.json"
EXPERTS = ROOT / "site" / "data" / "fra-experts.json"


@pytest.mark.skipif(shutil.which("node") is None or not EXPERTS.exists(), reason="needs node")
@pytest.mark.parametrize("with_experts", [False, True])
def test_web_pick_helper_matches_the_python_one(with_experts):
    sim = json.loads(DATA.read_text())
    if with_experts:
        ratings = expert_ratings(sim, json.loads(EXPERTS.read_text()))
    else:
        ratings = build_ratings(sim, None)
    names = [c["n"] for c in sim["cards"]]
    rng = random.Random(7)
    for _ in range(12):
        picks = rng.sample(names, rng.randint(0, 35))
        pack = rng.sample(names, 13)
        py = [(a.card.name, a.score) for a in advise(ratings, pack, picks, len(picks) % 14 + 1)]
        out = subprocess.run(
            ["node", str(ROOT / "tests" / "js" / "advise.js"),
             str(ROOT / "src" / "mulligan" / "site" / "tools.js"), str(DATA),
             str(EXPERTS) if with_experts else "-",
             json.dumps({"pack": pack, "picks": picks})],
            capture_output=True, text=True, check=True).stdout
        js = json.loads(out)
        assert [n for n, _ in py] == [n for n, _ in js]
        for (_, a), (_, b) in zip(py, js, strict=True):
            assert (a is None) == (b is None)
            if a is not None:
                assert abs(a - b) < 1e-9
