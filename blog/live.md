# Live pick helper

**Pick advice while you draft on MTG Arena, on your own computer.**

The [pick helper](tools/pick.html) on this site needs you to type in the pack.
The local version reads it for you. Arena writes every pack it shows you and
every pick you make to a log file on your computer. `mulligan live` watches that
file, and each time a new pack arrives it ranks the cards, both in the terminal
and on a page it opens in your browser (served from your own computer):

```
PremierDraft_FRA_20260929   Pack 1, pick 2   1 cards taken
Your picks lean Blue (U 0.2).
Where your pool's best cards point: WU 10%, WB 10%, WR 10%, WG 10%.
    grade  card                        score  experts
 1  B+     Jiang Yanggu, Never Alone G  +3.1  Marshall B+, Luis B+, Marc B, ...
           +2.7 pts vs. the average card. Taking it, your pool points to its
           colors 80%, splashed 20%: adds +3.1 pts to your likely deck.
 2  B      Mindseeker Oculus         U  +2.1  Marshall B, Luis B, Marc B-, ...
 3  B-     Proft, Sinister Mastermind B +1.8  Marshall B, Luis B, Alex B-, ...
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

**Card ratings are the experts' grades, and only those:** the podcast hosts
from the [primer](posts/2026-09-21-reality-fracture-primer.html#experts)
(Limited Resources, Limited Level-Ups, Lords of Limited, TCGplayer). Shows use
the letters differently, so each host's grades are shifted to a common
average first, then averaged per card. The average letter becomes win-rate
points on an uneven scale, the way real win rates fall out: an A+ bomb is some
ten points better than a C+ card and a B+ under four, while C and C- are about
a point apart. The simulator's ratings are not used here; they missed too many
busted rares.

**Score** is how much the card adds to the deck you'll likely end up with:

- **Your pool as ten decks.** Every card you've taken counts toward each of
  the ten two-color decks it fits, by how much better it is than a card that
  wouldn't make the deck. A deck is worth its best 23 cards. So it's the
  quality of your picks that sets your colors, not how many you have: a
  first-pick bomb makes its colors worth more straight away, and the next
  picks in those colors gain accordingly.
- **Which deck you end in** is uncertain, and more so early. The helper
  weighs the ten decks by their worth, loosely in pack one (most of your
  picks are still to come) and tightly by pack three. A card's score is how
  much it raises that weighted best deck: in full where it fits, which early
  on favors cards that fit many decks (one color, or colorless), and less
  and less where it doesn't as your colors settle.
- **Splashes.** A strong card (B+ or so) with one off-color mana symbol keeps
  part of its value in decks outside its color, more if you've taken a land
  that makes that color. Double off-color symbols can't be splashed.
- **Lands** that tap for both of a deck's colors are worth a late pick, more
  when they let you splash a strong card you've taken.

The terminal lists the likeliest decks above the pack, and the **why** column
spells out each card's odds of being in your final colors, whether it's
removal, and every host's grade. It still doesn't know your curve: if two
cards are within a point or two, take the one your deck needs.

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
