"""The user-facing commands, end to end, on Export-format decklists."""

from __future__ import annotations

from typer.testing import CliRunner

from mulligan.cli import app

RUNNER = CliRunner()

BASE = ["2 Ordinary Bear", "2 Attercop", "2 Wargling", "2 Little Bear", "2 Quarrel",
        "2 Warg Tactics", "2 Wood Elves", "2 Guardian of the Halls", "2 Ravening Warg",
        "2 Bilbo's Deadly Slice", "2 Front Porch Sentries", "1 Stony-Voiced Goblins",
        "9 Forest", "8 Swamp"]


def _deck(tmp_path, name: str, lines: list[str]):
    path = tmp_path / name
    path.write_text("Deck\n" + "\n".join(f"{line} (HOB) 1" for line in lines) + "\n")
    return str(path)


def test_versus_two_builds_of_one_deck_uses_the_real_field(tmp_path):
    a = _deck(tmp_path, "a.txt", BASE)
    b = _deck(tmp_path, "b.txt", [x if x != "1 Stony-Voiced Goblins" else "1 Crude Bent Blade"
                                  for x in BASE])
    result = RUNNER.invoke(app, ["versus", a, b, "--workers", "1", "--field-games", "2"])
    assert result.exit_code == 0, result.output
    assert "From real games" in result.output
    assert "two builds of one deck" in result.output
    assert "difference" in result.output


def test_versus_different_decks_plays_head_to_head(tmp_path):
    a = _deck(tmp_path, "a.txt", BASE)
    other = ["2 Dwarven Provisioner", "2 Lake-town Lookout", "2 Magnificent End",
             "2 Ori, Keeper of Songs", "2 Smaug's Fury", "2 Dori, Bearer of Friends",
             "2 Goblin-town Flunkies", "2 Tidings of War", "2 Iron Hills Stalwart",
             "2 Gundabad Opportunist", "2 Dwarven Shortsword", "1 Thorin's Last Stand",
             "9 Plains", "8 Mountain"]
    b = _deck(tmp_path, "b.txt", other)
    result = RUNNER.invoke(app, ["versus", a, b, "--games", "20", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert "From real games" in result.output and "20 games" in result.output


def test_versus_reports_unreadable_decks_cleanly(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("Deck\nthis is not a card line\n")
    result = RUNNER.invoke(app, ["versus", str(bad), str(bad), "--set", "hob"])
    assert result.exit_code == 1
    assert "error:" in result.output and "Traceback" not in result.output
