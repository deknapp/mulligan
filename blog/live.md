# Live pick helper

**Pick advice while you draft on MTG Arena, on your own computer.**

The [pick helper](tools/pick.html) on this site needs you to type in the pack.
The local version reads it for you. Arena writes every pack it shows you and
every pick you make to a log file on your computer. `mulligan live` watches that
file, and each time a new pack arrives it ranks the cards, both in the terminal
and on a page it opens in your browser (served from your own computer):

```
PremierDraft_FRA_20261002   Pack 1, pick 5   4 cards taken
Your picks lean Red-Green (R 2.1, G 2.0, B 1.2).
    grade  card                    score  sim     17Lands
 1  A      Blossom-Blessed Angel W  +7.5  59.4%   –
           +8.5 pts vs. the average card. One color outside your RG lane.
 2  B-     Restore with Empathy  G  +0.7  51.5%   –
           Fits your RG picks. In simulated RG decks: 52.6% (1,394 games).
 ...
```

The browser page shows the same ranking with card art and the reasons for each
score, plus:

- **A color compass.** The five colors sit around a pentagon, with the ten
  two-color pairs between them, each labelled with its win rate in simulated
  decks (17Lands' real win rate once every pair has 500 games). Petals grow toward the colors your picks hold, a trail shows where
  each pick moved you, and a ring fills as your colors settle. Hover a card in
  the pack to see where taking it would pull you.
- **Your pool,** by color, with each card's grade.
- **Your deck.** From 23 picks on, the pool built into a deck the way
  `mulligan build` does: the recommended two-color build laid out by mana
  cost, the next-best builds (including a splash) a click away, the best
  cards left out, and a button that copies the list for Arena's Import.
- **Every pick so far,** as three rows of squares colored by the card you took,
  starred where you took the helper's top card. Click one (or use the arrow
  keys) to see that pack again as the helper ranked it then; **L** or the live
  button jumps back to the current pick. After a draft, run `mulligan live`
  again to step back through the whole thing.

It only reads the log file. It doesn't touch the game or send anything anywhere,
and it's the same log that 17Lands' and other draft overlays read.

## Where the advice comes from

Each card gets one rating, blended from two sources:

- **The simulator.** Its win rate when drawn, from the latest run of simulated
  drafts: the numbers behind the [primer](index.html) and the
  [card ratings](tools/cards.html). This is all there is on release day.
- **17Lands.** Real players' win rate when drawn, fetched from
  [17Lands](https://www.17lands.com) when you start (and at most every three
  hours), for the format you're in: Premier, Quick or Traditional draft.

The blend counts the simulator as worth 500 real games. So a card with no real
games yet is rated by the simulator alone, one with 500 is half and half, and
one with 5,000 is almost all real data. Real win rates run a few points higher
than simulated ones, because 17Lands users are better than the average player,
so they're shifted onto the simulator's scale before the two are mixed. Early
on, while 17Lands only has numbers for a handful of cards, the helper sticks to
the simulator and says so.

From there it works like the website's pick helper. **Score** is how many
points more often you win when the card is drawn than with an average card,
minus a penalty for colors outside the two your picks lean toward. The penalty
is zero at pick one and grows until the middle of pack two. The **why** column
also says how the card did in simulated decks of your colors, whether it's
removal, and whether it usually goes late enough to come back to you.

It doesn't know about your curve or synergies. If two cards are within a
point or two, take the one your deck needs.

## Install

You need about five minutes and a terminal (Terminal on a Mac, PowerShell on
Windows). It works on macOS and Windows, and on Linux if Arena runs under
Steam.

**1. Install uv**, a Python installer. It fetches the right Python by itself,
so you don't need Python already.

On a Mac or Linux:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, in PowerShell:

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then close the terminal and open a new one.

**2. Install mulligan:**

```
uv tool install git+https://github.com/deknapp/mulligan
```

**3. Turn on Arena's detailed logs.** In Arena, go to Options → Account and
check **Detailed Logs (Plugin Support)**, then restart Arena. Without this,
Arena doesn't log packs. You only have to do this once.

**4. Run it** before or during a draft, and keep the window beside Arena:

```
mulligan live
```

Your browser opens the helper's page. When a pack shows up in Arena, it shows up
there (and in the terminal, best pick in green). Ctrl-C stops it. For the
terminal alone, run `mulligan live --no-web`; if the page doesn't open by
itself, the terminal prints its address (http://127.0.0.1:8765/). If you start it partway through a draft, it catches up on the picks
you've already made from the log.

**To update** to the newest simulated data and fixes:

```
uv tool upgrade mulligan
```

## If something's off

- **"No draft yet" while you're drafting:** detailed logs are probably off (step
  3), or Arena wasn't restarted after turning them on.
- **"No Arena Player.log found":** Arena keeps its log somewhere unusual on
  your machine. Point at it with `mulligan live --log "path/to/Player.log"`.
- **Card names like `#91234`:** the helper couldn't find Arena's card database
  or look the card up online. Set `MTGA_CARD_DB` to the `Raw_CardDatabase_….mtga`
  file in Arena's `Downloads/Raw` folder.
- **No internet:** `mulligan live --offline` uses the data from the last time
  it ran.
- **Other sets:** the simulator covers Reality Fracture. In a set it hasn't
  simulated, the helper runs on 17Lands alone, once 17Lands has data for it.

Once the draft is done, `mulligan build log:latest` suggests a deck from the
cards you took. The [code and full README](https://github.com/deknapp/mulligan)
cover the rest of what the tool does.
