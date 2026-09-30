"""Live draft reading and pick advice, on synthetic Arena log lines in each of
the shapes Arena has used (Premier/Traditional, older Premier, Quick draft)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from mulligan.cli import app
from mulligan.draft_log import DraftTracker, LogFollower
from mulligan.pick_advice import advise, build_ratings, expert_ratings, lane

EVENT = "PremierDraft_FRA_20261002"


def _join(event: str = EVENT) -> str:
    payload = json.dumps({"EventName": event, "EntryCurrencyType": "Gem"})
    request = json.dumps({"Type": 600, "Payload": payload})
    return f"[UnityCrossThreadLogger]==> Event_Join {json.dumps({'id': 'a', 'request': request})}"


def _notify(pack: int, pick: int, cards: list[int]) -> str:
    body = {"draftId": "d", "SelfPick": pick, "SelfPack": pack,
            "PackCards": ",".join(map(str, cards))}
    return f"[UnityCrossThreadLogger]Draft.Notify {json.dumps(body)}"


def _human_pick(pack: int, pick: int, card: int) -> str:
    request = json.dumps({"id": "x", "params": {"draftId": "d", "cardId": str(card),
                                                "packNumber": str(pack),
                                                "pickNumber": str(pick)}})
    return ("[UnityCrossThreadLogger]==> Draft.MakeHumanDraftPick "
            f"{json.dumps({'id': 'x', 'request': request})}")


def _old_pick(pack: int, pick: int, card: int) -> str:
    payload = json.dumps({"DraftId": "d", "GrpIds": [card], "Pack": pack, "Pick": pick})
    request = json.dumps({"Type": 1, "Payload": payload})
    return ("[UnityCrossThreadLogger]==> Event_PlayerDraftMakePick "
            f"{json.dumps({'id': 'y', 'request': request})}")


def _p1p1(cards: list[int]) -> str:
    payload = json.dumps({"CardsInPack": cards, "PackNumber": 1, "PickNumber": 1})
    body = {"id": "z", "request": json.dumps({"Payload": payload})}
    return f"[UnityCrossThreadLogger]LogBusinessEvents {json.dumps(body)}"


def _quick_status(pack: int, pick: int, cards: list[int], picked: list[int],
                  status: str = "PickNext") -> str:
    payload = json.dumps({"Result": "Success", "EventName": "QuickDraft_FRA_20261002",
                          "DraftStatus": status, "PackNumber": pack, "PickNumber": pick,
                          "DraftPack": [str(c) for c in cards],
                          "PickedCards": [str(c) for c in picked]})
    body = {"CurrentModule": "BotDraft", "Payload": payload}
    return f"<== BotDraft_DraftStatus {json.dumps(body)}"


def _quick_pick(pack: int, pick: int, card: int) -> str:
    payload = json.dumps({"EventName": "QuickDraft_FRA_20261002",
                          "PickInfo": {"EventName": "QuickDraft_FRA_20261002",
                                       "CardIds": [str(card)], "PackNumber": pack,
                                       "PickNumber": pick}})
    request = json.dumps({"Type": 1, "Payload": payload})
    return (f"[UnityCrossThreadLogger]==> BotDraft_DraftPick "
            f"{json.dumps({'id': 'q', 'request': request})}")


def _feed(*lines: str) -> DraftTracker:
    tracker = DraftTracker()
    for line in lines:
        tracker.feed(line)
    return tracker


def test_premier_draft_packs_and_picks():
    s = _feed(_join(), _p1p1([1, 2, 3]), _human_pick(1, 1, 2),
              _notify(1, 2, [4, 5]), _human_pick(1, 2, 5), _notify(1, 3, [6, 7])).state
    assert s.event == EVENT and s.set_code == "fra"
    assert (s.pack, s.pick, s.cards) == (1, 3, [6, 7])
    assert s.picked == [2, 5] and not s.waiting


def test_a_pick_before_the_next_pack_arrives_is_waiting():
    s = _feed(_join(), _notify(1, 1, [1, 2, 3]), _human_pick(1, 1, 3)).state
    assert s.waiting and s.picked == [3]


def test_older_premier_pick_format():
    s = _feed(_join(), _notify(1, 1, [1, 2]), _old_pick(1, 1, 1), _notify(1, 2, [8])).state
    assert s.picked == [1] and s.cards == [8]


def test_repeated_log_lines_do_not_double_count():
    lines = [_join(), _notify(1, 1, [1, 2]), _human_pick(1, 1, 1), _human_pick(1, 1, 1)]
    assert _feed(*lines).state.picked == [1]


def test_quick_draft_is_zero_based_and_trusts_picked_cards():
    s = _feed(_quick_status(0, 0, [1, 2, 3], []), _quick_pick(0, 0, 2),
              _quick_status(0, 1, [4, 5], [2])).state
    assert s.event.startswith("QuickDraft_FRA") and (s.pack, s.pick) == (1, 2)
    assert s.picked == [2] and s.cards == [4, 5]


def test_a_new_event_starts_a_new_draft():
    s = _feed(_join(), _notify(1, 1, [1]), _human_pick(1, 1, 1),
              _join("PremierDraft_HOB_20260811"), _notify(1, 1, [9])).state
    assert s.set_code == "hob" and s.picked == [] and s.cards == [9]


def test_follower_reads_only_whole_lines_and_survives_a_new_log(tmp_path):
    log = tmp_path / "Player.log"
    log.write_text(_join() + "\n" + _notify(1, 1, [1, 2])[:30])
    tracker = DraftTracker()
    follower = LogFollower(log, tracker)
    follower.poll()
    assert tracker.state.cards == []
    log.write_text(_join() + "\n" + _notify(1, 1, [1, 2]) + "\n")
    assert follower.poll() and tracker.state.cards == [1, 2]
    log.write_text(_join("PremierDraft_HOB_20260811") + "\n")   # Arena restarted
    follower.poll()
    assert tracker.state.set_code == "hob"


SIM = {"run": "2026-09-21", "mean": 0.5, "prior": 0, "cards": [
    {"n": "Bomb", "c": "R", "t": "Creature", "w": 600, "g": 1000},
    {"n": "Good Green", "c": "G", "t": "Creature", "w": 560, "g": 1000},
    {"n": "Good Blue", "c": "U", "t": "Creature", "w": 565, "g": 1000},
    {"n": "Filler", "c": "W", "t": "Creature", "w": 480, "g": 1000},
] + [{"n": f"C{i}", "c": "B", "t": "Creature", "w": 450 + 5 * i, "g": 1000}
     for i in range(20)], "pc": {}}


def test_sim_only_ratings_rank_by_win_rate():
    ratings = build_ratings(SIM, None)
    ranked = advise(ratings, ["Filler", "Bomb", "Good Green"], [])
    assert [a.card.name for a in ranked] == ["Bomb", "Good Green", "Filler"]
    assert ratings.real_used == 0


def test_real_data_blends_in_by_games_on_the_sim_scale():
    real = {"fetched": "2026-10-05T00:00", "cards": {
        s["n"]: {"gih": s["w"] / s["g"] + 0.05, "gih_n": 500} for s in SIM["cards"]}}
    real["cards"]["Filler"] = {"gih": 0.75, "gih_n": 100000}   # real players love it
    ratings = build_ratings(SIM, real)
    assert ratings.real_used == len(SIM["cards"])
    assert abs(ratings.cards["Bomb"].est - 0.6) < 0.01     # the +5 shift is removed
    assert ratings.cards["Filler"].est > ratings.cards["Bomb"].est


def test_a_set_without_a_simulation_runs_on_17lands_alone():
    real = {"cards": {"A": {"gih": 0.6, "gih_n": 900}, "B": {"gih": 0.5, "gih_n": 900}}}
    ranked = advise(build_ratings(None, real), ["B", "A"], [])
    assert [a.card.name for a in ranked] == ["A", "B"] and ranked[0].score > 0


def test_off_color_cards_lose_ground_once_you_are_in_a_lane():
    ratings = build_ratings(SIM, None)
    picks = ["Good Green"] * 7 + ["Bomb"] * 7
    weight, top = lane(ratings, picks)
    assert set(top) == {"R", "G"}
    ranked = advise(ratings, ["Good Blue", "Good Green"], picks)
    assert ranked[0].card.name == "Good Green"
    assert any("In your final colors 0%" in w for w in ranked[1].why)


def test_live_once_shows_the_pack(tmp_path, monkeypatch):
    log = tmp_path / "Player.log"
    log.write_text("\n".join([_join(), _notify(1, 2, [11, 12]), _human_pick(1, 1, 10)]) + "\n")
    monkeypatch.setattr("mulligan.draft_log.arena_card_names",
                        lambda db=None: {10: "Bomb", 11: "Filler", 12: "Good Green"})
    monkeypatch.setattr("mulligan.draft_log.arena_names", lambda ids: {})
    monkeypatch.setattr("mulligan.pick_advice.load_sim", lambda code, fetch=True: SIM)
    experts = {"cards": {n: {"grades": [{"show": "LR", "host": "Marshall", "grade": g}]}
                         for n, g in (("Bomb", "A"), ("Good Green", "B"), ("Filler", "D"))}}
    monkeypatch.setattr("mulligan.pick_advice.load_experts", lambda *a, **k: experts)
    result = CliRunner().invoke(app, ["live", "--log", str(log), "--once", "--offline"])
    assert result.exit_code == 0, result.output
    assert "Pack 1, pick 2" in result.output
    assert result.output.index("Good Green") < result.output.index("Filler")
    assert "no simulator" in " ".join(result.output.split())   # expert grades only


def test_live_ratings_are_the_experts_alone():
    """A card the simulator rates as filler but the experts call a bomb ranks first."""
    experts = {"cards": {"Filler": {"grades": [{"show": "LR", "host": "M", "grade": "A+"}]},
                         "Bomb": {"grades": [{"show": "LR", "host": "M", "grade": "C"}]},
                         "Good Green": {"grades": [{"show": "LR", "host": "M", "grade": "C+"}]}}}
    ratings = expert_ratings(SIM, experts)
    assert all(c.sim is None and not c.pairs for c in ratings.cards.values())
    assert not ratings.synergy
    ranked = advise(ratings, ["Bomb", "Filler"], [])
    assert ranked[0].card.name == "Filler"


def test_a_real_fra_premier_draft():
    """A whole FRA Premier Draft from Arena's log (2026-09-29): every pick lands
    where Arena's final CardPool says, and the draft ends."""
    from pathlib import Path
    lines = (Path(__file__).parent / "data" / "fra-premier-draft-2026-09-29.log").read_text()
    tracker = DraftTracker()
    tracker.feed(_join("PremierDraft_FRA_20260929"))
    for line in lines.splitlines()[:-1]:
        tracker.feed(line)
    state = tracker.state
    assert (state.pack, state.pick, len(state.picked), state.waiting) == (3, 14, 42, True)
    assert not state.complete
    assert state.picked[:3] == [106468, 106259, 106468]
    tracker.feed(lines.splitlines()[-1])
    assert state.complete and state.set_code == "fra"
