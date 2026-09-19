"""Command line.

    mulligan sets                                  compiled sets and their coverage
    mulligan sealed --set hob --seed 7 --out a.txt open a sealed pool, build a deck
    mulligan decks                                 your event decks from the Arena log
    mulligan versus log:2 clipboard                which of two Arena decks would win?
    mulligan compare a.txt b.txt --set hob         which deck is better (+ vs the field)?
    mulligan agents heuristic random --deck ...    which agent is better?
    mulligan play a.txt b.txt --set hob            watch one game
    mulligan rate --set hob --out r.json           card ratings from self-play
    mulligan validate --set hob --ratings r.json   check them against 17Lands
    mulligan train --set hob                       train the learned agent
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


@app.command("decks")
def decks_cmd():
    """The event decks (draft, sealed) found in your MTG Arena log."""
    from .arena_log import read_decks
    from .cards.sets import available_sets
    decks = read_decks()
    if not decks:
        console.print("No event decks in your Arena log yet.")
        return
    compiled = set(available_sets())
    for i, deck in enumerate(decks):
        ok = "" if deck.set_code in compiled else "   [dim](set not compiled yet)[/dim]"
        when = deck.updated[:16].replace("T", " ")
        console.print(f"  log:{i}  {deck.event}  {deck.size} cards  {when}{ok}")
    console.print("[dim]Use these with: mulligan versus log:N log:M  (or a file, or "
                  "'clipboard' after Arena's Export)[/dim]")


@app.command("versus")
def versus_cmd(
    deck_a: str = typer.Argument(..., help="log:N, log:latest, clipboard, or a decklist file."),
    deck_b: str = typer.Argument(..., help="log:N, log:latest, clipboard, or a decklist file."),
    games: int = typer.Option(2000, help="Games to play (rounded up to even)."),
    agent: str = typer.Option("heuristic", help="Who pilots both decks."),
    set_code: str = typer.Option(None, "--set", help="Override the detected set."),
    fmt: str = typer.Option("PremierDraft", "--format",
                            help="17Lands format for the data model: PremierDraft or Sealed."),
    seed: int = typer.Option(0),
    workers: int = typer.Option(0),
):
    """Which of two Arena decks would win? Decks from your Arena log or Export text.

    Two answers: a model fitted to real 17Lands games (when one exists for the
    set), and a simulation of the matchup."""
    from .arena_log import ArenaLogError
    from .decks import DeckError, load_ref
    try:
        a, b = load_ref(deck_a, set_code), load_ref(deck_b, set_code)
    except (DeckError, ArenaLogError) as exc:
        console.print(f"[red]error:[/red] {exc}", highlight=False)
        raise typer.Exit(1) from None
    for warning in a.warnings + b.warnings:
        console.print(f"[yellow]note:[/yellow] {warning}", highlight=False)
    for deck in (a, b):
        note = deck.fidelity_note()
        if note:
            console.print(f"[cyan]fidelity:[/cyan] {note}", highlight=False)
    if a.set_code and b.set_code and a.set_code != b.set_code:
        console.print(f"[yellow]note:[/yellow] the decks are from different sets "
                      f"({a.set_code.upper()} vs {b.set_code.upper()})")
    if a.names and b.names and a.set_code == b.set_code:
        from .deckmodel import DeckModel
        try:
            model = DeckModel.load(a.set_code, fmt)
        except FileNotFoundError:
            model = None
        if model is not None:
            p = model.head_to_head(a.names, b.names)
            low, high = model.head_to_head_interval(a.names, b.names)
            console.print(f"\n[bold]From real games[/bold] (17Lands {a.set_code.upper()} {fmt}, "
                          f"{model.meta.get('train_games', '?')} games; full card text, "
                          f"no simulation) — the answer to trust for this set")
            verdict = (f"{a.label} is better" if low > 0.5 else f"{b.label} is better"
                       if high < 0.5 else "too close to call")
            console.print(f"  {a.label} beats {b.label}: {p:.0%} "
                          f"(90% interval {low:.0%}–{high:.0%}) → {verdict}", highlight=False)
            console.print(f"  vs an average opponent: {a.label} {model.vs_field(a.names):.0%}, "
                          f"{b.label} {model.vs_field(b.names):.0%}", highlight=False)
            diffs = model.differences(a.names, b.names)
            if diffs:
                console.print("  cards that differ, biggest effect first (A = first deck):")
                for name, delta, effect in diffs[:8]:
                    side = "A" if delta > 0 else "B"
                    good = (effect > 0) == (side == "A")
                    verb = "helps" if good else "hurts"
                    what = (name[len("deck shape: "):] if name.startswith("deck shape: ")
                            else f"{abs(delta)}× {name}")
                    from .deckmodel import SHAPE_TEXT
                    what = SHAPE_TEXT.get(what, what)
                    console.print(f"    {what} in {side}: {verb} {side} by "
                                  f"{abs(effect):.1f} pts", highlight=False)
    start = time.time()
    result = compare(Entry(a.label, agent, tuple(a.cards)), Entry(b.label, agent, tuple(b.cards)),
                     games=games, seed=seed, workers=workers or None)
    console.print("\n[bold]Simulated[/bold] (both decks piloted by the "
                  f"{agent} agent). Not yet validated for ranking real decks — "
                  "see README; useful for watching the matchup")
    console.print(result.summary(), markup=False)
    console.print(f"[dim]{result.games} games in {time.time() - start:.1f}s[/dim]")


@app.command("build")
def build_cmd(
    deck: str = typer.Argument("log:latest", help="A deck from your Arena log (log:N)."),
    fmt: str = typer.Option("PremierDraft", "--format", help="PremierDraft or Sealed model."),
    colors: str = typer.Option(None, help="Force a color pair, e.g. BG."),
    out: Path = typer.Option(None, help="Write the suggested decklist here."),
):
    """The best build of the pool you drafted or opened, by real 17Lands results,
    compared with the deck you played."""
    from .arena_log import arena_names, read_decks, resolve
    from .cards.sets import available_sets, load_set
    from .deckmodel import DeckModel
    from .limited.advise import best_build
    if not deck.startswith("log:"):
        console.print("[red]error:[/red] build needs a deck from your Arena log (log:N)")
        raise typer.Exit(1)
    decks = read_decks()
    index = len(decks) - 1 if deck == "log:latest" else int(deck[4:])
    logged = decks[index]
    if logged.set_code not in available_sets():
        console.print(f"[red]error:[/red] {logged.event}: set not compiled")
        raise typer.Exit(1)
    if not logged.pool:
        console.print("[red]error:[/red] the log has no card pool for this event")
        raise typer.Exit(1)
    data = load_set(logged.set_code)
    ids = {int(e["arena_id"]): n for n, e in data.entries.items() if e.get("arena_id")}
    missing = [g for g in set(logged.pool) | set(logged.cards) if g not in ids]
    ids.update(arena_names(missing))
    resolve(logged, ids)
    pool = [ids[g] for g in logged.pool if g in ids]
    model = DeckModel.load(logged.set_code, fmt)
    advice = best_build(pool, data, model, colors)
    played = logged.names
    console.print(f"[bold]{logged.event}[/bold]: pool of {len(pool)} cards")
    console.print(f"  the deck you played: {model.vs_field(played):.1%} vs an average opponent")
    console.print(f"  best build found ({advice.colors}): {model.vs_field(advice.best):.1%}")
    for pair, rate in advice.alternatives:
        console.print(f"    next best: {pair} {rate:.1%}")
    p = model.head_to_head(advice.best, played)
    low, high = model.head_to_head_interval(advice.best, played)
    console.print(f"  suggested vs played, head to head: {p:.0%} "
                  f"(90% interval {low:.0%}–{high:.0%})")
    diffs = model.differences(advice.best, played)
    adds = [(n, d, e) for n, d, e in diffs if d > 0]
    cuts = [(n, d, e) for n, d, e in diffs if d < 0]
    if adds or cuts:
        console.print("  changes (from real-game card values):")
        from .deckmodel import describe_change
        shape = [(n, d, e) for n, d, e in diffs if n.startswith("deck shape: ")]
        for name, d, e in shape:
            console.print(f"    {describe_change(name, d)}  ({e:+.1f} pts)", highlight=False)
        for name, d, e in [x for x in adds if x not in shape][:8]:
            console.print(f"    + {describe_change(name, d)}  ({e:+.1f} pts)", highlight=False)
        for name, d, e in [x for x in cuts if x not in shape][:8]:
            console.print(f"    − {describe_change(name, d)}  ({e:+.1f} pts)", highlight=False)
    if out:
        out.write_text("Deck\n" + "".join(f"{n} {name}\n" for name, n in advice.best.items()))
        console.print(f"[dim]wrote {out}[/dim]")
    console.print("[dim]Card values come from 17Lands games; they rate each card against an "
                  "average opponent and ignore synergies between your cards.[/dim]")


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


@app.command("rate")
def rate_cmd(
    set_code: str = typer.Option(..., "--set"),
    decks: int = typer.Option(200, help="Sealed decks in the simulated field."),
    games: int = typer.Option(20000, help="Games to simulate."),
    seed: int = typer.Option(0),
    agent: str = typer.Option("heuristic"),
    out: Path = typer.Option(None, help="Save ratings as JSON (name -> [GIH WR, games])."),
    top: int = typer.Option(25, help="Rows to print from each end."),
    workers: int = typer.Option(0),
):
    """Card ratings from self-play: each card's win rate in games where it was
    drawn (the same statistic as 17Lands' GIH WR). Works on release day."""
    import json

    from .cards.sets import load_set
    from .limited.simulate import simulate_ratings
    start = time.time()
    stats = simulate_ratings(set_code, n_decks=decks, n_games=games, seed=seed, agent=agent,
                             workers=workers or None)
    data = load_set(set_code)
    rows = sorted(((s.rate, s.games, name) for name, s in stats.items()
                   if not data.playable[name].is_land and s.games >= 100), reverse=True)
    console.print(f"[bold]{data.name}: simulated games-in-hand win rate[/bold] "
                  f"({games} games, {time.time() - start:.0f}s)")
    shown = rows[:top] + ([None] if len(rows) > 2 * top else []) + rows[-top:] if len(
        rows) > 2 * top else rows
    for row in shown:
        if row is None:
            console.print("  ...")
            continue
        rate, n, name = row
        console.print(f"  {rate:6.1%}  {n:6d}  {data.rarity(name)[0].upper()}  {name}",
                      markup=False)
    if out:
        out.write_text(json.dumps({name: [round(s.rate, 4), s.games]
                                   for name, s in stats.items()}, indent=0))
        console.print(f"[dim]wrote {out}[/dim]")


@app.command("validate")
def validate_cmd(
    set_code: str = typer.Option(..., "--set"),
    ratings: Path = typer.Option(..., help="Simulated ratings JSON from `mulligan rate --out`."),
    fmt: str = typer.Option("Sealed", help="17Lands format: Sealed, PremierDraft, ..."),
    min_games: int = typer.Option(300, help="Minimum games in hand on each side."),
):
    """How well do simulated card ratings track real results (17Lands GIH WR)?"""
    import json

    from .cards.sets import load_set
    from .validation.seventeen import correlate, game_data_ratings, spearman
    data = load_set(set_code)
    sim_raw = json.loads(ratings.read_text())
    sim = {n: v[0] for n, v in sim_raw.items()
           if v[1] >= min_games and n in data.playable and not data.playable[n].is_land}
    real = game_data_ratings(set_code, fmt)
    rho, r, n, rows = correlate(sim, real, min_real_games=min_games)
    rank = {"common": 0, "uncommon": 1, "rare": 2, "mythic": 3}
    base = spearman([rank.get(data.rarity(x[0]), 0) for x in rows], [x[2] for x in rows])
    console.print(f"[bold]{data.name}: simulated vs 17Lands {fmt} GIH WR[/bold] "
                  f"over {n} cards")
    console.print(f"  Spearman {rho:+.3f}   Pearson {r:+.3f}   "
                  f"(rarity-only baseline: Spearman {base:+.3f})")
    console.print("  17Lands data: 17lands.com, used under their public data terms.",
                  style="dim")


@app.command("train")
def train_cmd(
    set_code: str = typer.Option(..., "--set"),
    iterations: int = typer.Option(30),
    games: int = typer.Option(2000, help="Self-play games per iteration."),
    eval_games: int = typer.Option(1000, help="Games in each evaluation against the heuristic."),
    eval_every: int = typer.Option(3),
    lr: float = typer.Option(0.3),
    temperature: float = typer.Option(0.15, help="Exploration: 0 is greedy."),
    seed: int = typer.Option(0),
    out: Path = typer.Option(None, help="Where to save (default: the set's shipped model)."),
    workers: int = typer.Option(0),
):
    """Train the learned agent for a set by self-play. Saves the best model
    (by paired evaluation against the heuristic) as it goes."""
    from .learn import train

    def log(entry: dict) -> None:
        line = f"iter {entry['iteration']:3d}  train {entry['train_score']:.3f}"
        if "eval_rate" in entry:
            line += (f"  vs heuristic {entry['eval_rate']:.1%} "
                     f"({entry['eval_low']:.1%}–{entry['eval_high']:.1%})")
        console.print(line)

    history = train(set_code, iterations=iterations, games=games, eval_games=eval_games,
                    eval_every=eval_every, lr=lr, temperature=temperature, seed=seed, out=out,
                    workers=workers or None, log=log)
    low, high = history.best_interval
    console.print(f"best vs heuristic: {history.best_rate:.1%} ({low:.1%}–{high:.1%})")


@app.command("fit")
def fit_cmd(
    set_code: str = typer.Option(..., "--set"),
    fmt: str = typer.Option("PremierDraft", "--format", help="PremierDraft, Sealed, ..."),
    epochs: int = typer.Option(8),
):
    """Fit the deck model to 17Lands' public games for a set (about a minute)."""
    from .deckmodel import fit
    model = fit(set_code, fmt, epochs=epochs, log=lambda line: console.print(f"[dim]{line}[/dim]"))
    console.print(f"wrote {model.save()}")


@app.command("ingest")
def ingest_cmd(set_code: str = typer.Argument(..., help="Scryfall set code, e.g. fra.")):
    """Fetch a set's cards from Scryfall, the input to compiling it."""
    from .cards.ingest import write_raw
    path = write_raw(set_code)
    console.print(f"wrote {path}")


if __name__ == "__main__":  # pragma: no cover
    app()
