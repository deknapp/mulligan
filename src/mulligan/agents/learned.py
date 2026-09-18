"""The learned agent: hand-coded strategy plus a correction learned by self-play.

    score(action) = squash(heuristic score) + w · features(action)

With ``w`` all zero it plays exactly like ``HeuristicAgent`` (``squash`` is
monotonic), so training starts from a competent player and only has to learn
what is different about this set. ``w`` is a sparse dict of named features
(``agents.features``), saved as a small JSON file per set.

During training the agent samples from ``softmax(score / temperature)`` and
records, per decision, what it needs for a policy-gradient update. At
evaluation time it plays the highest-scoring action.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from ..engine import actions as act
from ..engine.view import PlayerView
from .base import Agent
from .features import Features, action_features, situation
from .heuristic import HeuristicAgent

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"


def squash(x: float) -> float:
    """Compress the heuristic's scale (land drops score ~100, attacks ±1) so a
    learned correction of modest size can matter in close decisions."""
    return math.copysign(math.log1p(abs(x)), x)


def dot(w: dict[str, float], f: Features) -> float:
    return sum(w.get(k, 0.0) * v for k, v in f.items())


class LearnedAgent(Agent):
    def __init__(self, weights: dict[str, float] | None = None, name: str = "learned",
                 temperature: float = 0.0, seed: int | None = None, record: bool = False,
                 value_weights: dict[str, float] | None = None):
        self.name = name
        self.w = weights or {}
        self.v = value_weights or {}
        self.temperature = temperature
        self.rng = random.Random(seed)
        self.heuristic = HeuristicAgent()
        self.record = record
        # One entry per real decision: (option features, probabilities, chosen, situation).
        self.trace: list[tuple[list[Features], list[float], int, Features]] = []

    def choose(self, view: PlayerView, options: list[act.Action]) -> act.Action:
        if view.pending == "attackers":
            self.heuristic._plan = self.heuristic._plan_attacks(view)
        elif view.pending == "blockers":
            self.heuristic._plan = self.heuristic._plan_blocks(view)
        sit = situation(view)
        feats = [action_features(view, a, sit) for a in options]
        scores = [squash(self.heuristic.score(view, a)) + dot(self.w, f)
                  for a, f in zip(options, feats, strict=True)]
        if self.temperature <= 0:
            index = max(range(len(options)), key=scores.__getitem__)
            probs = None
        else:
            top = max(scores)
            exps = [math.exp((s - top) / self.temperature) for s in scores]
            total = sum(exps)
            probs = [e / total for e in exps]
            index = self._sample(probs)
        if self.record and probs is not None:
            self.trace.append((feats, probs, index, sit))
        return options[index]

    def _sample(self, probs: list[float]) -> int:
        r = self.rng.random()
        acc = 0.0
        for i, p in enumerate(probs):
            acc += p
            if r <= acc:
                return i
        return len(probs) - 1

    # --------------------------------------------------------------- storage

    def save(self, path: Path, meta: dict | None = None) -> None:
        payload = {"meta": meta or {}, "weights": {k: round(v, 5) for k, v in self.w.items()
                                                   if abs(v) > 1e-4},
                   "value": {k: round(v, 5) for k, v in self.v.items()}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=0, sort_keys=True))

    @staticmethod
    def load_weights(ref: str) -> tuple[dict[str, float], dict[str, float]]:
        """``ref`` is a set code (a model shipped in ``mulligan/models``) or a path."""
        path = Path(ref)
        if not path.exists():
            path = MODEL_DIR / f"{ref.lower()}.json"
        if not path.exists():
            raise FileNotFoundError(f"no learned model for {ref!r} (train one with "
                                    f"`mulligan train --set {ref}`)")
        payload = json.loads(path.read_text())
        return payload["weights"], payload.get("value", {})
