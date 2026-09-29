"""The browser view of ``mulligan live``: the snapshot it serves and the server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from test_draft_log import SIM, _human_pick, _join, _notify

from mulligan.draft_log import DraftTracker
from mulligan.live_web import LiveServer, Snapshots, pair_strength
from mulligan.pick_advice import build_ratings

IDS = {10: "Bomb", 11: "Filler", 12: "Good Green", 13: "Good Blue"}


def names(ids):
    return [IDS.get(i, f"#{i}") for i in ids]


def _draft():
    tracker = DraftTracker()
    for line in [_join(), _notify(1, 1, [10, 11, 13]), _human_pick(1, 1, 10),
                 _notify(1, 2, [11, 12])]:
        tracker.feed(line)
    return tracker.state


def test_snapshot_keeps_every_pack_as_it_was_ranked():
    snap = Snapshots()(_draft(), names, build_ratings(SIM, None), "note", "fra")
    assert snap["status"] == "drafting" and (snap["pack"], snap["pick"]) == (1, 2)
    first, now = snap["steps"]
    assert first["taken"] == "Bomb" and first["cards"][0]["name"] == "Bomb"
    assert first["before"]["weight"]["R"] == 0              # nothing taken yet
    assert now["taken"] is None and now["before"]["weight"]["R"] > 0
    assert [c["name"] for c in now["cards"]] == ["Good Green", "Filler"]
    assert [c["name"] for c in snap["pool"]] == ["Bomb"]
    json.dumps(snap)                                        # all plain JSON


def test_pair_strength_is_the_deck_win_rate():
    sim = {"pairs": {"WU": {"w": 55.0, "g": 100, "share": 0.1}, "UB": {"w": 0, "g": 0}}}
    assert pair_strength(sim) == {"WU": {"wr": 0.55, "share": 0.1, "name": "Azorius"}}


def test_server_serves_the_page_and_only_new_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MULLIGAN_CACHE", str(tmp_path))
    server = LiveServer(port=18765, offline=True)
    try:
        server.publish(Snapshots()(_draft(), names, build_ratings(SIM, None), "", "fra"))

        def get(path):
            with urllib.request.urlopen(server.url + path, timeout=5) as r:
                return r.status, r.read()

        status, page = get("")
        assert status == 200 and b"compass" in page
        state = json.loads(get("state.json")[1])
        assert state["v"] == 1 and len(state["steps"]) == 2
        assert json.loads(get("state.json?v=1")[1]) == {"v": 1}   # unchanged: no resend
        try:
            get("img/normal/Bomb")                          # offline, nothing cached
            raise AssertionError("expected a 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.close()


def test_pair_strength_adds_17lands_once_a_pair_has_the_games():
    sim = {"pairs": {"WU": {"w": 55.0, "g": 100}}}
    real = {"pairs": {"WU": [3000, 5000], "UB": [100, 200]}}
    got = pair_strength(sim, real)
    assert got["WU"]["real"] == 0.6 and got["WU"]["real_games"] == 5000
    assert "UB" not in got                                  # 200 games: too few


def test_a_finished_pool_is_built_into_decks():
    from mulligan.cards.sets import load_set
    from mulligan.live_web import deck_builds
    data = load_set("fra")
    pool = [n for n, s in data.playable.items()
            if not s.is_land and s.color_set and s.color_set <= {"W", "U"}][:30]
    decks = deck_builds(pool, "fra", build_ratings(SIM, None))
    best = decks[0]
    assert best["recommended"] and best["colors"] == "WU" and len(best["spells"]) == 23
    assert best["decklist"].startswith("Deck\n") and sum(best["lands"].values()) == 17
    assert len(best["left_out"]) <= 8
    assert deck_builds(pool[:10], "fra", build_ratings(SIM, None)) == []
    assert deck_builds(pool, "xyz", build_ratings(SIM, None)) == []
