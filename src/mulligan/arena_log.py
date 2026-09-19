"""Decks straight from MTG Arena: the Player.log, or Arena's Export text.

Arena logs every event deck you submit (draft, sealed) as a ``CourseDeck``
with card ids and counts, next to the event name, e.g.
``PremierDraft_HOB_20260811``. Card ids are Arena ``grpId`` values, which
equal Scryfall's ``arena_id``, so a deck resolves by id — no OCR, no name
matching. It needs Arena's "Detailed Logs (Plugin Support)" option (Options →
Account), and it only ever contains *your* decks: Arena does not log an
opponent's list. A friend's deck comes from their Export text or their log.

Adapted from mtg_ai's sealed-pool log reader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

LOG_DIRS = [
    "~/Library/Logs/Wizards Of The Coast/MTGA",                              # macOS
    "~/AppData/LocalLow/Wizards Of The Coast/MTGA",                          # Windows
    "~/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/"
    "steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA",                  # Linux (Proton)
]
LOG_NAMES = ("Player.log", "Player-prev.log")

EVENT_RE = re.compile(r'"InternalEventName"\s*:\s*"([^"]+)"')
POOL_RE = re.compile(r'"CardPool"\s*:\s*\[([0-9,\s]*)\]')
COURSE_DECK_RE = re.compile(r'"CourseDeck"\s*:\s*\{\s*"MainDeck"\s*:\s*(\[[^\]]*\])')
UPDATED_RE = re.compile(r'LastUpdated","value":"\\?"([0-9T:\-.]+)')
SET_RE = re.compile(r"_([A-Z0-9]{3,5})_")
BASIC_NAMES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}


class ArenaLogError(RuntimeError):
    pass


@dataclass
class LoggedDeck:
    event: str
    set_code: str | None
    cards: dict[int, int]            # grpId -> count
    updated: str = ""
    source: str = ""
    names: dict[str, int] = field(default_factory=dict)   # filled by resolve()
    unresolved: dict[int, int] = field(default_factory=dict)
    pool: list[int] = field(default_factory=list)          # every card drafted/opened

    @property
    def size(self) -> int:
        return sum(self.cards.values())

    def key(self) -> tuple:
        return (self.event, tuple(sorted(self.cards.items())))

    def decklist(self) -> str:
        return "Deck\n" + "".join(f"{n} {name}\n" for name, n in self.names.items())


def find_logs(extra: list[str] | None = None) -> list[Path]:
    found = []
    for directory in (extra or []) + LOG_DIRS:
        base = Path(directory).expanduser()
        if base.is_file():
            found.append(base)
            continue
        for name in LOG_NAMES:
            path = base / name
            if path.exists():
                found.append(path)
    return found


def _set_code(event: str) -> str | None:
    match = SET_RE.search(event)
    return match.group(1).lower() if match else None


def parse_log(text: str, source: str = "") -> list[LoggedDeck]:
    """Every event deck in a log, oldest first, duplicates removed."""
    if "DETAILED LOGS: DISABLED" in text and "CourseDeck" not in text:
        raise ArenaLogError(
            "Arena's detailed logs are off, so it does not record decks. Turn on "
            "Options → Account → \"Detailed Logs (Plugin Support)\", restart Arena, "
            "and open your event again.")
    decks: list[LoggedDeck] = []
    for match in COURSE_DECK_RE.finditer(text):
        # The event name and deck metadata come just before the deck in the same record.
        window = text[max(0, match.start() - 4000):match.start()]
        events = EVENT_RE.findall(window)
        if not events:
            continue
        event = events[-1]
        updated = UPDATED_RE.findall(window)
        try:
            entries = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        cards: dict[int, int] = {}
        for entry in entries:
            cards[int(entry["cardId"])] = cards.get(int(entry["cardId"]), 0) + int(
                entry.get("quantity", 1))
        if sum(cards.values()) < 20:
            continue  # an empty or half-built deck
        # A course record lists its CardPool *after* its deck; stop at the next course.
        after = text[match.end():match.end() + 20000]
        cut = after.find('"CourseId"')
        found = POOL_RE.search(after[:cut] if cut >= 0 else after)
        pool = [int(x) for x in found.group(1).split(",") if x.strip()] if found else []
        decks.append(LoggedDeck(event, _set_code(event), cards,
                                updated[-1] if updated else "", source, pool=pool))
    unique: dict[tuple, LoggedDeck] = {}
    for deck in decks:
        unique.pop(deck.key(), None)
        unique[deck.key()] = deck  # keep the latest sighting's position
    return list(unique.values())


def read_decks(paths: list[Path] | None = None) -> list[LoggedDeck]:
    """Decks from the Arena logs on this machine, oldest first."""
    paths = paths if paths is not None else find_logs()
    if not paths:
        raise ArenaLogError("No Arena Player.log found. Is MTG Arena installed and has it "
                            "been run on this machine?")
    decks: list[LoggedDeck] = []
    # Player-prev.log is the older session; read it first.
    for path in sorted(paths, key=lambda p: p.name != "Player-prev.log"):
        decks.extend(parse_log(path.read_text(errors="replace"), source=str(path)))
    unique: dict[tuple, LoggedDeck] = {}
    for deck in decks:
        unique.pop(deck.key(), None)
        unique[deck.key()] = deck
    return list(unique.values())


def resolve(deck: LoggedDeck, id_to_name: dict[int, str]) -> LoggedDeck:
    """Turn grpIds into card names. Basic lands have many art-variant ids; any
    id the set does not know is looked up in the id cache (see ``arena_names``)."""
    deck.names, deck.unresolved = {}, {}
    for grp, count in deck.cards.items():
        name = id_to_name.get(grp)
        if name is None:
            deck.unresolved[grp] = count
        else:
            deck.names[name] = deck.names.get(name, 0) + count
    return deck


ID_CACHE = Path("data/arena_ids.json")


def arena_names(ids: list[int], cache: Path = ID_CACHE, fetch: bool = True) -> dict[int, str]:
    """Card names for Arena ids, via Scryfall's /cards/arena/<id>, cached on
    disk. Mostly needed for basic lands, whose art variants each have an id."""
    import time
    import urllib.error
    import urllib.request

    known: dict[str, str] = json.loads(cache.read_text()) if cache.exists() else {}
    out: dict[int, str] = {}
    changed = False
    for grp in ids:
        if str(grp) in known:
            out[grp] = known[str(grp)]
            continue
        if not fetch:
            continue
        request = urllib.request.Request(f"https://api.scryfall.com/cards/arena/{grp}",
                                         headers={"User-Agent": "mulligan/0.1",
                                                  "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                name = json.load(response)["name"].split(" // ")[0]
        except (urllib.error.URLError, KeyError, TimeoutError):
            continue
        known[str(grp)] = name
        out[grp] = name
        changed = True
        time.sleep(0.1)
    if changed:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, indent=0, sort_keys=True))
    return out


def export_set_code(text: str) -> str | None:
    """The set an Arena Export decklist is from: the most common ``(SET)`` tag."""
    codes = re.findall(r"\(([A-Za-z0-9]{3,5})\)", text)
    if not codes:
        return None
    return max(set(codes), key=codes.count).lower()
