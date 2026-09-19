"""Arena decks: the Player.log reader and Export text, on synthetic inputs."""

from __future__ import annotations

import json

import pytest

from mulligan.arena_log import ArenaLogError, export_set_code, parse_log, resolve
from mulligan.cards.sets import load_set
from mulligan.decks import load_ref

HOB = load_set("hob")
IDS = {int(e["arena_id"]): n for n, e in HOB.entries.items() if e.get("arena_id")}


def _record(event: str, cards: dict[int, int], updated: str = "2026-09-06T09:48:36") -> str:
    main = json.dumps([{"cardId": k, "quantity": v} for k, v in cards.items()])
    return ('[UnityCrossThreadLogger]9/6/2026 9:48:36 AM\n{"Courses":[{"CourseId":"x",'
            f'"InternalEventName":"{event}","CourseDeckSummary":{{"Attributes":['
            f'{{"name":"LastUpdated","value":"\\"{updated}\\""}}]}},'
            f'"CourseDeck":{{"MainDeck":{main},"Sideboard":[]}}}}]}}\n')


def _deck_ids(n_spells: int = 23) -> dict[int, int]:
    spells = [i for i, n in IDS.items() if not HOB.playable.get(n, None) or
              not HOB.playable[n].is_land][:n_spells]
    forest = next(i for i, n in IDS.items() if n == "Forest")
    ids = dict.fromkeys(spells, 1)
    ids[forest] = 40 - n_spells
    return ids


def test_log_decks_are_found_with_their_event_and_set():
    text = _record("PremierDraft_HOB_20260811", _deck_ids())
    decks = parse_log(text)
    assert len(decks) == 1
    assert decks[0].set_code == "hob" and decks[0].size == 40


def test_repeated_log_records_of_one_deck_collapse_to_one():
    record = _record("PremierDraft_HOB_20260811", _deck_ids())
    assert len(parse_log(record * 5)) == 1


def test_half_built_decks_are_ignored():
    assert parse_log(_record("PremierDraft_HOB_20260811", {1: 3})) == []


def test_detailed_logs_off_is_explained():
    with pytest.raises(ArenaLogError, match="Detailed Logs"):
        parse_log("DETAILED LOGS: DISABLED\nnothing else")


def test_log_ids_resolve_to_card_names():
    deck = parse_log(_record("PremierDraft_HOB_20260811", _deck_ids()))[0]
    resolve(deck, IDS)
    assert not deck.unresolved
    assert sum(deck.names.values()) == 40


def test_export_text_loads_with_its_set_and_skips_the_sideboard(tmp_path):
    text = ("Deck\n2 Ordinary Bear (HOB) 170\n1 Bilbo's Deadly Slice (HOB) 80\n"
            "17 Forest (HOB) 280\n20 Swamp (HOB) 278\n\nSideboard\n3 Attercop (HOB) 150\n")
    assert export_set_code(text) == "hob"
    path = tmp_path / "deck.txt"
    path.write_text(text)
    ref = load_ref(str(path))
    assert ref.set_code == "hob" and len(ref.cards) == 40
    assert "Attercop" not in {c.name for c in ref.cards}


def test_unplayable_cards_are_replaced_and_reported(tmp_path):
    path = tmp_path / "deck.txt"
    path.write_text("Deck\n1 Belladonna Took (HOB) 1\n22 Ordinary Bear (HOB) 170\n"
                    "17 Forest (HOB) 280\n")
    ref = load_ref(str(path))
    assert len(ref.cards) == 40
    assert ref.replaced == 1 and "Belladonna Took" in ref.warnings[0]
    exact, approx, replaced = ref.fidelity()
    assert (exact, replaced) == (22, 1)
