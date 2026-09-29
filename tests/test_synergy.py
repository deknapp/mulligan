import csv
import gzip

from mulligan.limited.synergy import PairTally, contrast, from_17lands, synergies


def test_cells_split_by_what_was_seen():
    t = PairTally()
    deck = ["A", "B", "C"]
    t.add(deck, {"A", "B"}, 1.0)   # both
    t.add(deck, {"A"}, 0.0)        # A only
    t.add(deck, {"B"}, 1.0)        # B only
    t.add(deck, set(), 0.5)        # neither
    assert t.cells["A", "B"] == [1.0, 1, 0.0, 1, 1.0, 1, 0.5, 1]
    assert ("B", "A") not in t.cells


def test_additive_cards_have_no_interaction():
    # A adds 10 points, B adds 5, nothing extra together.
    cell = [650, 1000, 600, 1000, 550, 1000, 500, 1000]
    raw, se = contrast(cell)
    assert abs(raw) < 1e-9 and 0 < se < 0.05


def test_real_synergy_survives_shrinkage_and_noise_does_not():
    import random
    rng = random.Random(1)
    cells = {}
    for k in range(300):
        extra = 0.15 if k == 0 else 0.0
        p = [0.5 + extra, 0.5, 0.5, 0.5]
        n = 4000 if k == 0 else 400
        cell = []
        for q in p:
            cell += [sum(rng.random() < q for _ in range(n)), n]
        cells[f"X{k}|Y{k}"] = cell
    result = synergies(cells)
    top = result["pairs"][0]
    assert (top["a"], top["b"]) == ("X0", "Y0") and top["est"] > 0.05
    assert max(abs(r["est"]) for r in result["pairs"][1:]) < top["est"] / 2


def test_min_cell_drops_thin_pairs():
    assert synergies({"A|B": [5, 10, 5, 10, 5, 10, 5, 10]})["pairs"] == []


def test_reads_17lands_rows(tmp_path):
    path = tmp_path / "g.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["won", "deck_A", "deck_B", "deck_Land", "opening_hand_A", "drawn_A",
                    "opening_hand_B", "drawn_B", "opening_hand_Land", "drawn_Land"])
        w.writerow(["True", 1, 2, 17, 1, 0, 0, 1, 3, 2])
        w.writerow(["False", 1, 0, 17, 0, 0, 0, 0, 3, 2])
    t = from_17lands(path, keep={"A", "B"})
    assert dict(t.cells) == {("A", "B"): [1.0, 1, 0, 0, 0, 0, 0, 0]}


def test_tally_pickles_for_worker_processes():
    import pickle
    t = PairTally()
    t.add(["A", "B"], {"A"}, 1.0)
    assert pickle.loads(pickle.dumps(t)).cells == t.cells


def test_extra_pair_runs_add_to_the_main_run(tmp_path):
    import json

    from mulligan.site.tools import pair_cells
    main = tmp_path / "fra-draft-2026-09-21.json"
    main.write_text(json.dumps({"card_pairs": {"A|B": [1] * 8}}))
    (tmp_path / "fra-pairs-2026-09-29.json").write_text(
        json.dumps({"card_pairs": {"A|B": [2] * 8, "A|C": [3] * 8}}))
    (tmp_path / "hob-pairs-2026-09-29.json").write_text(
        json.dumps({"card_pairs": {"A|B": [100] * 8}}))
    assert pair_cells(main) == {"A|B": [3] * 8, "A|C": [3] * 8}
