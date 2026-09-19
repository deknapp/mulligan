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
from functools import lru_cache
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


@lru_cache(maxsize=8)
def _card_info(set_code: str) -> dict[str, tuple[bool, bool, int, frozenset]]:
    """name -> (is land, is creature, mana value, pips), from the compiled set
    (every card has these, including ones the engine cannot play). ``pips`` is
    a frozenset of option-sets: a hybrid pip is one set with two colors."""
    from .cards.sets import load_set
    from .engine.types import ManaCost
    info = {}
    for name, entry in load_set(set_code).entries.items():
        types = entry.get("types", [])
        cost = ManaCost.parse(entry.get("cost", "") or "")
        pips = frozenset(frozenset(symbol.split("/")) for symbol, _ in cost.pips
                         if symbol != "C")
        info[name] = ("Land" in types, "Creature" in types, cost.mana_value, pips)
    for basic in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
        info.setdefault(basic, (True, False, 0, frozenset()))
    return info


STRUCTURE = ["lands<=15", "lands=16", "lands=18", "lands>=19", "colors=1", "colors>=3",
             "creatures<=11", "creatures>=17", "two_drops<=2", "five_plus>=6"]
"""Deck-shape features, each an indicator measured against a typical deck
(17 lands, two colors, 12-16 creatures): the questions players actually ask
about a build — land count, splashing, creature count, curve."""


SHAPE_TEXT = {"lands<=15": "15 or fewer lands", "lands=16": "16 lands", "lands=18": "18 lands",
              "lands>=19": "19+ lands", "colors=1": "one color", "colors>=3": "3+ colors",
              "creatures<=11": "11 or fewer creatures", "creatures>=17": "17+ creatures",
              "two_drops<=2": "2 or fewer cards costing 1-2",
              "five_plus>=6": "6+ cards costing 5+"}


def describe_change(name: str, delta: int) -> str:
    """'2× Forest' for a card; 'now 16 lands' / 'no longer 3+ colors' for a shape."""
    if name.startswith("deck shape: "):
        text = SHAPE_TEXT.get(name[len("deck shape: "):], name)
        return ("now " if delta > 0 else "no longer ") + text
    return f"{abs(delta)}× {name}"


def structure(deck: dict[str, int], info: dict) -> dict[str, float]:
    lands = creatures = two = five = 0
    single: set = set()
    hybrid: list = []
    for name, n in deck.items():
        is_land, is_creature, mv, pips = info.get(name, (False, False, 0, frozenset()))
        if is_land:
            lands += n
            continue
        for options in pips:
            (single.update(options) if len(options) == 1 else hybrid.append(options))
        creatures += n if is_creature else 0
        two += n if mv <= 2 else 0
        five += n if mv >= 5 else 0
    # A hybrid pip needs a new color only if the deck has neither of its colors.
    colors = set(single)
    for options in hybrid:
        if not options & colors:
            colors.add(min(options))
    out = {"lands<=15": lands <= 15, "lands=16": lands == 16, "lands=18": lands == 18,
           "lands>=19": lands >= 19, "colors=1": len(colors) == 1, "colors>=3": len(colors) >= 3,
           "creatures<=11": creatures <= 11, "creatures>=17": creatures >= 17,
           "two_drops<=2": two <= 2, "five_plus>=6": five >= 6}
    return {f"shape:{k}": 1.0 for k, v in out.items() if v}


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
    info = _card_info(set_code)
    shape_index = {f"shape:{k}": len(names) + i for i, k in enumerate(STRUCTURE)}
    names = names + list(shape_index)
    rows = []
    for raw in reader:
        cards = []
        deck: dict[str, int] = {}
        for j, (i, name) in enumerate(deck_cols):
            value = raw[i]
            if value not in ("0", "", "0.0"):
                cards.append((j, float(value)))
                deck[name] = int(float(value))
        cards.extend((shape_index[k], v) for k, v in structure(deck, info).items())
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
    # Card weights refitted on resampled drafts: the spread is the uncertainty.
    bootstrap: list[dict[str, float]] = field(default_factory=list)

    def features(self, deck: dict[str, int]) -> dict[str, float]:
        """Card counts plus the deck-shape indicators (land count, colors,
        creatures, curve)."""
        feats = {name: float(n) for name, n in deck.items()}
        if any(k.startswith("shape:") for k in self.weights):
            feats.update(structure(deck, _card_info(self.set_code)))
        return feats

    def score(self, deck: dict[str, int]) -> float:
        """The deck's contribution, in log-odds against an average deck."""
        return sum(self.weights.get(name, 0.0) * x for name, x in self.features(deck).items())

    def unknown(self, deck: dict[str, int]) -> list[str]:
        return sorted(n for n in deck if n not in self.weights)

    def vs_field(self, deck: dict[str, int]) -> float:
        """Win rate against the average opponent, average pilot, coin-flip play."""
        return _sigmoid(self.intercept + 0.5 * self.play + self.score(deck))

    def head_to_head(self, a: dict[str, int], b: dict[str, int]) -> float:
        return _sigmoid(self.score(a) - self.score(b))

    def head_to_head_interval(self, a: dict[str, int], b: dict[str, int],
                              z: float = 1.64) -> tuple[float, float]:
        """A 90% interval for ``head_to_head`` from the bootstrap refits."""
        if len(self.bootstrap) < 3:
            return 0.0, 1.0
        fa, fb = self.features(a), self.features(b)
        diffs = []
        for w in self.bootstrap:
            diffs.append(sum(w.get(n, 0.0) * c for n, c in fa.items())
                         - sum(w.get(n, 0.0) * c for n, c in fb.items()))
        mean = sum(diffs) / len(diffs)
        sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1))
        center = self.score(a) - self.score(b)
        return _sigmoid(center - z * sd), _sigmoid(center + z * sd)

    def differences(self, a: dict[str, int], b: dict[str, int]) -> list[tuple[str, int, float]]:
        """Cards in one deck and not the other: (name, copies in A minus copies
        in B, effect on A's win rate against B in percentage points), biggest
        effect first."""
        rows = []
        fa, fb = self.features(a), self.features(b)
        for name in set(fa) | set(fb):
            delta = fa.get(name, 0) - fb.get(name, 0)
            if delta:
                effect = 100 * (_sigmoid(delta * self.weights.get(name, 0.0)) - 0.5)
                rows.append((name.replace("shape:", "deck shape: "), int(delta), effect))
        return sorted(rows, key=lambda r: -abs(r[2]))

    def save(self, path: Path | None = None) -> Path:
        path = path or MODEL_DIR / f"{self.set_code.lower()}_{self.fmt}_decks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "set": self.set_code, "format": self.fmt, "intercept": round(self.intercept, 5),
            "on_play": round(self.play, 5), "skill": round(self.skill, 5), "meta": self.meta,
            "weights": {k: round(v, 5) for k, v in sorted(self.weights.items())},
            "bootstrap": self.bootstrap}, indent=0))
        return path

    @staticmethod
    def load(set_code: str, fmt: str = "PremierDraft", path: Path | None = None) -> DeckModel:
        path = path or MODEL_DIR / f"{set_code.lower()}_{fmt}_decks.json"
        if not path.exists():
            raise FileNotFoundError(f"no deck model for {set_code.upper()} {fmt}; "
                                    f"build one with `mulligan fit --set {set_code}`")
        raw = json.loads(path.read_text())
        return DeckModel(raw["set"], raw["format"], raw["weights"], raw["intercept"],
                         raw["on_play"], raw["skill"], raw.get("meta", {}),
                         raw.get("bootstrap", []))


def _log_loss(rows: list[Row], predict) -> float:
    total = 0.0
    for r in rows:
        p = min(1 - 1e-9, max(1e-9, predict(r)))
        total -= r.won * math.log(p) + (1 - r.won) * math.log(1 - p)
    return total / len(rows)


def _sgd(train: list[Row], n_cards: int, epochs: int, lr: float, l2: float,
         rng: random.Random) -> tuple[list[float], float, float, float]:
    """Logistic regression by SGD with a per-feature adaptive step (Adagrad)."""
    w = [0.0] * n_cards
    g2 = [1e-8] * n_cards
    b = b_play = b_skill = 0.0
    gb = [1e-8, 1e-8, 1e-8]
    order = list(train)
    for _ in range(epochs):
        rng.shuffle(order)
        for r in order:
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
    return w, b, b_play, b_skill


def fit(set_code: str, fmt: str = "PremierDraft", epochs: int = 6, lr: float = 0.02,
        l2: float = 3e-5, holdout: float = 0.1, bootstrap: int = 8, seed: int = 0,
        log=print) -> DeckModel:
    """Fit the model; hold out a tenth of the drafts to check it predicts better
    than pilot skill and play/draw alone; then refit ``bootstrap`` times on
    resampled drafts so every answer can carry an uncertainty."""
    names, rows = load_games(set_code, fmt)
    rng = random.Random(seed)
    groups = sorted({r.group for r in rows})
    test_groups = set(rng.sample(groups, int(len(groups) * holdout)))
    train = [r for r in rows if r.group not in test_groups]
    test = [r for r in rows if r.group in test_groups]
    w, b, b_play, b_skill = _sgd(train, len(names), epochs, lr, l2, rng)
    log("fitted")

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
    log(f"held-out log loss {model_ll:.4f} vs {base_ll:.4f} without cards; "
        f"accuracy {accuracy:.1%} on {len(test)} games")
    by_group: dict[str, list[Row]] = {}
    for r in train:
        by_group.setdefault(r.group, []).append(r)
    group_list = list(by_group)
    boots = []
    for k in range(bootstrap):
        sample = [r for g in (rng.choice(group_list) for _ in group_list) for r in by_group[g]]
        bw, *_ = _sgd(sample, len(names), max(2, epochs // 2), lr, l2, rng)
        boots.append({n: round(x, 4) for n, x in zip(names, bw, strict=True)})
        log(f"bootstrap {k + 1}/{bootstrap}")
    meta = {"train_games": len(train), "test_games": len(test),
            "test_log_loss": round(model_ll, 5), "baseline_log_loss": round(base_ll, 5),
            "test_accuracy": round(accuracy, 4), "bootstrap_fits": bootstrap,
            "source": "17lands.com public game data (CC BY 4.0)"}
    return DeckModel(set_code, fmt, dict(zip(names, w, strict=True)), b, b_play, b_skill, meta,
                     boots)
