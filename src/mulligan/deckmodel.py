"""Which deck wins, from real games instead of simulated ones.

17Lands publishes every game its users play: the full decklist, whether they
won, whether they were on the play, and how strong the player is. Fitting

    P(win) = sigmoid(b + b_play·on_play + b_skill·skill + Σ_card w_card · copies)

gives each card a contribution to a deck's win rate that already includes
everything the card does — no rules engine, no simplified card text. The player
skill term matters: good players both build better decks and win more, and
without it the model would credit cards for their pilots.

Two decks are compared Bradley–Terry style: with the same pilot on both,
``P(A beats B) = sigmoid(score(A) - score(B))`` where ``score`` is the card
sum. The model rates each deck against an *average* opponent, so it misses
specific matchups (fliers against a deck with no reach); the simulator does not.

Needs real data, so it exists weeks after a set's release, not on day one.
Trained per set and format in about a minute on a laptop, in plain Python; the
weights are a small JSON file. 17Lands data: 17lands.com, CC BY 4.0.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import random
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .validation.seventeen import HEADERS, S3_URL

MODEL_DIR = Path(__file__).resolve().parent / "models"
RAW_DIR = Path("data/17lands")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, x))))


def _download(set_code: str, fmt: str, raw_dir: Path = RAW_DIR) -> Path:
    path = raw_dir / f"game_data_public.{set_code.upper()}.{fmt}.csv.gz"
    if not path.exists():
        request = urllib.request.Request(S3_URL.format(set=set_code.upper(), fmt=fmt),
                                         headers=HEADERS)
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        raw_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


@dataclass
class Row:
    group: str               # draft_id: games from one deck stay on one side of the split
    cards: list[tuple[int, float]]
    on_play: float
    skill: float
    won: float


def load_games(set_code: str, fmt: str = "PremierDraft") -> tuple[list[str], list[Row]]:
    path = _download(set_code, fmt)
    reader = csv.reader(io.TextIOWrapper(gzip.open(path), encoding="utf-8"))
    header = next(reader)
    col = {name: i for i, name in enumerate(header)}
    deck_cols = [(i, name[5:].split(" // ")[0]) for i, name in enumerate(header)
                 if name.startswith("deck_")]
    names = [name for _, name in deck_cols]
    rows = []
    for raw in reader:
        cards = []
        for j, (i, _) in enumerate(deck_cols):
            value = raw[i]
            if value not in ("0", "", "0.0"):
                cards.append((j, float(value)))
        bucket = raw[col["user_game_win_rate_bucket"]]
        skill = (float(bucket) - 0.55) * 10 if bucket not in ("", "None") else 0.0
        rows.append(Row(raw[col["draft_id"]], cards,
                        1.0 if raw[col["on_play"]] in ("True", "true", "1") else 0.0,
                        skill, 1.0 if raw[col["won"]] in ("True", "true", "1") else 0.0))
    return names, rows


@dataclass
class DeckModel:
    set_code: str
    fmt: str
    weights: dict[str, float]
    intercept: float
    play: float
    skill: float
    meta: dict = field(default_factory=dict)

    def score(self, deck: dict[str, int]) -> float:
        """The deck's card contribution, in log-odds against an average deck."""
        return sum(self.weights.get(name, 0.0) * n for name, n in deck.items())

    def unknown(self, deck: dict[str, int]) -> list[str]:
        return sorted(n for n in deck if n not in self.weights)

    def vs_field(self, deck: dict[str, int]) -> float:
        """Win rate against the average opponent, average pilot, coin-flip play."""
        return _sigmoid(self.intercept + 0.5 * self.play + self.score(deck))

    def head_to_head(self, a: dict[str, int], b: dict[str, int]) -> float:
        return _sigmoid(self.score(a) - self.score(b))

    def save(self, path: Path | None = None) -> Path:
        path = path or MODEL_DIR / f"{self.set_code.lower()}_{self.fmt}_decks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "set": self.set_code, "format": self.fmt, "intercept": round(self.intercept, 5),
            "on_play": round(self.play, 5), "skill": round(self.skill, 5), "meta": self.meta,
            "weights": {k: round(v, 5) for k, v in sorted(self.weights.items())}}, indent=0))
        return path

    @staticmethod
    def load(set_code: str, fmt: str = "PremierDraft", path: Path | None = None) -> DeckModel:
        path = path or MODEL_DIR / f"{set_code.lower()}_{fmt}_decks.json"
        if not path.exists():
            raise FileNotFoundError(f"no deck model for {set_code.upper()} {fmt}; "
                                    f"build one with `mulligan fit --set {set_code}`")
        raw = json.loads(path.read_text())
        return DeckModel(raw["set"], raw["format"], raw["weights"], raw["intercept"],
                         raw["on_play"], raw["skill"], raw.get("meta", {}))


def _log_loss(rows: list[Row], predict) -> float:
    total = 0.0
    for r in rows:
        p = min(1 - 1e-9, max(1e-9, predict(r)))
        total -= r.won * math.log(p) + (1 - r.won) * math.log(1 - p)
    return total / len(rows)


def fit(set_code: str, fmt: str = "PremierDraft", epochs: int = 8, lr: float = 0.02,
        l2: float = 3e-5, holdout: float = 0.1, seed: int = 0, log=print) -> DeckModel:
    """Logistic regression by SGD with a per-feature adaptive step (Adagrad).
    A tenth of the drafts are held out to check the model predicts better than
    knowing only the pilot's skill and the play/draw."""
    names, rows = load_games(set_code, fmt)
    rng = random.Random(seed)
    groups = sorted({r.group for r in rows})
    test_groups = set(rng.sample(groups, int(len(groups) * holdout)))
    train = [r for r in rows if r.group not in test_groups]
    test = [r for r in rows if r.group in test_groups]
    w = [0.0] * len(names)
    g2 = [1e-8] * len(names)
    b = b_play = b_skill = 0.0
    gb = [1e-8, 1e-8, 1e-8]
    for epoch in range(epochs):
        rng.shuffle(train)
        for r in train:
            z = b + b_play * r.on_play + b_skill * r.skill + sum(w[j] * x for j, x in r.cards)
            err = r.won - _sigmoid(z)
            for k, x in enumerate((1.0, r.on_play, r.skill)):
                grad = err * x
                gb[k] += grad * grad
                step = lr * grad / math.sqrt(gb[k])
                if k == 0:
                    b += step
                elif k == 1:
                    b_play += step
                else:
                    b_skill += step
            for j, x in r.cards:
                grad = err * x - l2 * w[j]
                g2[j] += grad * grad
                w[j] += lr * grad / math.sqrt(g2[j])
        log(f"epoch {epoch + 1}/{epochs}")

    def full(r: Row) -> float:
        return _sigmoid(b + b_play * r.on_play + b_skill * r.skill
                        + sum(w[j] * x for j, x in r.cards))

    # Baseline: the same regression with no cards — pilot skill and play/draw only.
    nb = nb_play = nb_skill = 0.0
    for _ in range(3):
        for r in train:
            err = r.won - _sigmoid(nb + nb_play * r.on_play + nb_skill * r.skill)
            nb += 0.01 * err
            nb_play += 0.01 * err * r.on_play
            nb_skill += 0.01 * err * r.skill

    def base(r: Row) -> float:
        return _sigmoid(nb + nb_play * r.on_play + nb_skill * r.skill)

    model_ll, base_ll = _log_loss(test, full), _log_loss(test, base)
    accuracy = sum((full(r) > 0.5) == (r.won > 0.5) for r in test) / len(test)
    meta = {"train_games": len(train), "test_games": len(test),
            "test_log_loss": round(model_ll, 5), "baseline_log_loss": round(base_ll, 5),
            "test_accuracy": round(accuracy, 4),
            "source": "17lands.com public game data (CC BY 4.0)"}
    log(f"held-out log loss {model_ll:.4f} vs {base_ll:.4f} without cards; "
        f"accuracy {accuracy:.1%} on {len(test)} games")
    return DeckModel(set_code, fmt, dict(zip(names, w, strict=True)), b, b_play, b_skill, meta)
