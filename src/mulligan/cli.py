"""Command line.

    mulligan sets                                  compiled sets and their coverage
    mulligan sealed --set hob --seed 7 --out a.txt open a sealed pool, build a deck
    mulligan compare a.txt b.txt --set hob         which deck is better?
    mulligan agents heuristic random --deck ...    which agent is better?
    mulligan play a.txt b.txt --set hob            watch one game
    mulligan ingest hob                            fetch a set from Scryfall (to compile it)
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console

from .arena import Entry, compare, gauntlet
from .match import play_game

app = typer.Typer(help="A rules-strict Magic engine where agents play decks against each other.",
                  no_args_is_help=True)
console = Console()


def _resolve(ref: str, set_code: str | None):
    from .decks import load_deck
    return load_deck(ref, set_code)


@app.command("sets")
def sets_cmd():
    """Compiled sets, and how much of each the engine can play."""
    from .cards.sets import available_sets, load_set
    for code in available_sets():
        console.print(load_set(code).coverage(), markup=False)


@app.command("sealed")
def sealed_cmd(
    set_code: str = typer.Option(..., "--set", help="Set code, e.g. hob."),
    seed: int = typer.Option(0, help="Which pool to open (same seed, same pool)."),
    out: Path = typer.Option(None, help="Write the decklist here."),
    colors: str = typer.Option(None, help="Force a color pair, e.g. RG."),
    lands: int = typer.Option(17, help="Number of lands."),
):
    """Open a sealed pool and build the best deck it allows."""
    from .cards.sets import load_set
    from .limited.build import build_deck
    from .limited.pools import sealed_pool
    data = load_set(set_code)
    pool = sealed_pool(data, seed)
    deck = build_deck(pool, data, colors=colors, lands=lands)
    unplayable = sorted({n for n in pool if n not in data.playable})
    console.print(f"[bold]{data.name}[/bold] sealed pool #{seed}: {len(pool)} cards"
                  + (f" ({len(unplayable)} not yet playable: {', '.join(unplayable)})"
                     if unplayable else ""))
    console.print(f"Best colors: {deck.colors}   (top pairs: {', '.join(deck.notes)})")
    console.print(deck.decklist(), markup=False, highlight=False)
    if out:
        out.write_text(deck.decklist())
        console.print(f"[dim]wrote {out}[/dim]")


@app.command("compare")
def compare_cmd(
    deck_a: str = typer.Argument(..., help="First deck: built-in name or decklist path."),
    deck_b: str = typer.Argument(..., help="Second deck: built-in name or decklist path."),
    set_code: str = typer.Option(None, "--set", help="Set the decklists are from."),
    agent: str = typer.Option("heuristic", help="Agent that pilots both decks."),
    games: int = typer.Option(1000, help="Head-to-head games (rounded up to even)."),
    field: int = typer.Option(24, help="Field decks for the vs-the-field test (0 = skip). "
                                       "Needs --set."),
    field_games: int = typer.Option(20, help="Games against each field deck."),
    seed: int = typer.Option(0, help="First seed; runs are reproducible."),
    workers: int = typer.Option(0, help="Processes (0 = all cores but one)."),
):
    """Which deck is better? Head to head, and against the set's field.

    The same agent pilots both decks. Against the field, both decks face the
    same opponents on the same shuffles, so the difference between them is a
    paired comparison and the interval is much tighter than two separate runs.
    """
    a_cards = tuple(_resolve(deck_a, set_code))
    b_cards = tuple(_resolve(deck_b, set_code))
    a, b = Entry(deck_a, agent, a_cards), Entry(deck_b, agent, b_cards)
    start = time.time()
    console.print("[bold]Head to head[/bold]")
    console.print(compare(a, b, games=games, seed=seed, workers=workers or None).summary(),
                  markup=False)
    if field and set_code:
        from .cards.sets import load_set
        from .limited.field import sealed_field
        decks = sealed_field(load_set(set_code), field, seed)
        opponents = [Entry(f"field{i}", agent, tuple(d.cards)) for i, d in enumerate(decks)]
        console.print(f"\n[bold]Against the field[/bold] ({field} sealed decks of "
                      f"{set_code.upper()}, {field_games} games each)")
        result = gauntlet([a, b], opponents, games_per_opponent=field_games, seed=seed,
                          workers=workers or None)
        console.print(result.summary(), markup=False)
    console.print(f"[dim]{time.time() - start:.1f}s[/dim]")


@app.command("agents")
def agents_cmd(
    agent_a: str = typer.Argument(..., help="First agent."),
    agent_b: str = typer.Argument(..., help="Second agent."),
    deck: str = typer.Option(..., help="Deck both agents play (mirror match)."),
    games: int = typer.Option(1000),
    seed: int = typer.Option(0),
    workers: int = typer.Option(0),
    set_code: str = typer.Option(None, "--set"),
):
    """Which agent is better? Both play the same deck, seat-swapped on identical shuffles."""
    cards = tuple(_resolve(deck, set_code))
    result = compare(Entry(agent_a, agent_a, cards), Entry(agent_b, agent_b, cards),
                     games=games, seed=seed, workers=workers or None)
    console.print(result.summary(), markup=False)


@app.command("play")
def play_cmd(
    deck_a: str = typer.Argument(...),
    deck_b: str = typer.Argument(...),
    agent: str = typer.Option("heuristic"),
    seed: int = typer.Option(0),
    set_code: str = typer.Option(None, "--set"),
):
    """Play one game and print its log."""
    from .arena import make_agent
    result = play_game((make_agent(agent, 1), make_agent(agent, 2)),
                       (_resolve(deck_a, set_code), _resolve(deck_b, set_code)),
                       seed=seed, keep_log=True)
    for line in result.log:
        console.print(line, markup=False, highlight=False)
    console.print(f"\n[bold]{result.reason}[/bold] after {result.turns} turns")


@app.command("ingest")
def ingest_cmd(set_code: str = typer.Argument(..., help="Scryfall set code, e.g. fra.")):
    """Fetch a set's cards from Scryfall, the input to compiling it."""
    from .cards.ingest import write_raw
    path = write_raw(set_code)
    console.print(f"wrote {path}")


if __name__ == "__main__":  # pragma: no cover
    app()
