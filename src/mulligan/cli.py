"""Command line: ``mulligan compare``, ``mulligan play``."""

from __future__ import annotations

import time

import typer
from rich.console import Console

from .arena import Entry, compare
from .match import play_game

app = typer.Typer(help="A rules-strict Magic engine where agents play decks against each other.",
                  no_args_is_help=True)
console = Console()


@app.command("compare")
def compare_cmd(
    deck_a: str = typer.Argument(..., help="First deck: built-in name or decklist path."),
    deck_b: str = typer.Argument(..., help="Second deck: built-in name or decklist path."),
    agent: str = typer.Option("heuristic", help="Agent that pilots both decks."),
    games: int = typer.Option(1000, help="Games to play (rounded up to even)."),
    seed: int = typer.Option(0, help="First seed; runs are reproducible."),
    workers: int = typer.Option(0, help="Processes (0 = all cores but one)."),
    set_code: str = typer.Option(None, "--set", help="Set the decklists are from."),
):
    """Which deck is better? Both decks are piloted by the same agent."""
    a_cards = _resolve(deck_a, set_code)
    b_cards = _resolve(deck_b, set_code)
    start = time.time()
    result = compare(Entry(deck_a, agent, tuple(a_cards)), Entry(deck_b, agent, tuple(b_cards)),
                     games=games, seed=seed, workers=workers or None)
    console.print(result.summary())
    console.print(f"[dim]{result.games} games in {time.time() - start:.1f}s[/dim]")


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
    console.print(result.summary())


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


def _resolve(ref: str, set_code: str | None):
    from .decks import load_deck
    return load_deck(ref, set_code)


if __name__ == "__main__":  # pragma: no cover
    app()
