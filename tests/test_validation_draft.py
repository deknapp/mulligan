import json

from mulligan.validation import draft as vd


def test_validate_draft_orders_like_real(tmp_path, monkeypatch):
    run = {"set": "hob", "records": {"WU": [60, 100], "BR": [50, 100], "RG": [40, 100]},
           "cards": {"A": [300, 500, 2.0, 40], "B": [250, 500, 5.0, 40],
                     "C": [200, 500, 9.0, 40]}}
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run))
    monkeypatch.setattr(vd, "game_data_ratings",
                        lambda s, f: {"A": (0.60, 900), "B": (0.55, 900), "C": (0.50, 900)})
    monkeypatch.setattr(vd, "_ata", lambda s: {"A": 1.5, "B": 4.0, "C": 10.0})
    monkeypatch.setattr(vd, "real_pair_records",
                        lambda s: {"WU": (58, 100), "BR": (55, 100), "RG": (45, 100)})
    v = vd.validate_draft(path)
    assert min(v.gih_rho, v.ata_rho, v.pair_rho) > 0.999
    assert [p for p, _, _ in v.pairs] == ["WU", "BR", "RG"]
