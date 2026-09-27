"""A draft in progress, read live from MTG Arena's Player.log.

Arena writes every pack it shows you and every pick you make to the log (with
"Detailed Logs (Plugin Support)" on), as card ``grpId`` numbers. The shapes
differ by event type, and have changed over the years:

* Premier and Traditional draft (people): the pack arrives as ``Draft.Notify``
  ``{"SelfPack":1,"SelfPick":2,"PackCards":"id,id,..."}``; the first pack of an
  event can instead come as ``"CardsInPack":[...]`` with ``PackNumber`` and
  ``PickNumber``. Picks are ``Draft.MakeHumanDraftPick`` (``packNumber``,
  ``pickNumber``, ``cardId``) or, in older clients, ``Event_PlayerDraftMakePick``
  (``Pack``, ``Pick``, ``GrpId``/``GrpIds``). All 1-based.
* Quick draft (bots): ``BotDraft_DraftStatus`` / ``BotDraft_DraftPick``
  responses carry ``"DraftPack":["id",...]``, ``"PickedCards":[...]`` and a
  0-based ``PackNumber``/``PickNumber``; picks are ``PickInfo``
  (``CardId``/``CardIds``).

Most of these are JSON nested inside JSON strings, so rather than parse each
wrapper, a line is un-escaped and the fields are matched directly. That keeps
working when Arena renames a wrapper, which it does. Formats follow
bstaple1/MTGA_Draft_17Lands, the long-running open-source draft overlay.

Card names come from Arena's own card database (installed with the client, so
it knows a set before Scryfall does), falling back to Scryfall's id lookup.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .arena_log import LOG_DIRS, arena_names

EVENT_RE = re.compile(r'"(?:EventName|EventId|InternalEventName)"\s*:\s*"([^"]*Draft[^"]*)"')
SET_RE = re.compile(r"_([A-Z0-9]{3,5})_")
NUM = r'"?(\d+)"?'
IDS = r'\[([\d",\s]*)\]'


def _field(name: str, text: str, pattern: str = NUM) -> str | None:
    match = re.search(rf'"{name}"\s*:\s*{pattern}', text)
    return match.group(1) if match else None


def _ids(text: str | None) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", text or "")]


def _flat(line: str) -> str:
    """Undo up to three levels of JSON-in-a-string escaping."""
    for _ in range(3):
        if '\\"' not in line:
            break
        line = line.replace('\\\\', '\\').replace('\\"', '"')
    return line


@dataclass
class DraftState:
    event: str = ""
    pack: int = 0                     # 1-based, the pack you are picking from now
    pick: int = 0
    cards: list[int] = field(default_factory=list)       # grpIds in that pack
    picks: dict[tuple[int, int], int] = field(default_factory=dict)  # (pack, pick) -> grpId
    complete: bool = False

    @property
    def set_code(self) -> str | None:
        match = SET_RE.search(self.event)
        return match.group(1).lower() if match else None

    @property
    def picked(self) -> list[int]:
        return [self.picks[k] for k in sorted(self.picks)]

    @property
    def waiting(self) -> bool:
        """You already took a card from the pack on screen; the next is on its way."""
        return (self.pack, self.pick) in self.picks


class DraftTracker:
    """Feed it log lines in order; ``state`` is the current draft."""

    def __init__(self):
        self.state = DraftState()
        self.changed = False

    def _new_draft(self, event: str) -> None:
        self.state = DraftState(event=event)

    def _pack(self, pack: int, pick: int, cards: list[int]) -> None:
        if not cards:
            return
        if (pack, pick) == (1, 1) and self.state.picks:
            self._new_draft(self.state.event)   # a new draft of the same event
        self.state.pack, self.state.pick, self.state.cards = pack, pick, cards
        self.state.complete = False
        self.changed = True

    def _pick(self, pack: int, pick: int, card: int) -> None:
        self.state.picks[(pack, pick)] = card
        self.changed = True

    def feed(self, line: str) -> None:
        if "Draft" not in line and "CardsInPack" not in line and "PackCards" not in line:
            return
        text = _flat(line)
        if "Event_Join" in text or "BotDraft_DraftStatus" in text:
            event = EVENT_RE.search(text)
            if event and event.group(1) != self.state.event:
                self._new_draft(event.group(1))
                self.changed = True
        elif not self.state.event and (event := EVENT_RE.search(text)):
            self.state.event = event.group(1)

        if (cards := _field("PackCards", text, r'"([\d,\s]*)"')) is not None:
            self._pack(int(_field("SelfPack", text) or 0), int(_field("SelfPick", text) or 0),
                       _ids(cards))
        elif (cards := _field("CardsInPack", text, IDS)) is not None:
            self._pack(int(_field("PackNumber", text) or 1), int(_field("PickNumber", text) or 1),
                       _ids(cards))
        elif (cards := _field("DraftPack", text, IDS)) is not None:
            status = _field("DraftStatus", text, r'"(\w+)"')
            picked = _ids(_field("PickedCards", text, IDS))
            pack, pick = int(_field("PackNumber", text) or 0), int(_field("PickNumber", text) or 0)
            if status == "Completed":
                self.state.complete = self.changed = True
            elif status in (None, "PickNext"):
                if picked or (pack, pick) == (0, 0):
                    # Quick draft reports everything taken so far: the whole truth.
                    self.state.picks = {(1 + i // 14, 1 + i % 14): c for i, c in enumerate(picked)}
                self._pack(pack + 1, pick + 1, _ids(cards))

        if "MakeHumanDraftPick" in text or "PlayerDraftMakePick" in text:
            card = _field("cardId", text) or _field("GrpId", text) or \
                next(iter(_ids(_field("GrpIds", text, IDS))), None)
            pack = _field("packNumber", text) or _field("Pack", text)
            pick = _field("pickNumber", text) or _field("Pick", text)
            if card and pack and pick:
                self._pick(int(pack), int(pick), int(card))
        elif "BotDraft_DraftPick" in text and "PickInfo" in text:
            card = _field("CardId", text) or next(iter(_ids(_field("CardIds", text, IDS))), None)
            pack, pick = _field("PackNumber", text), _field("PickNumber", text)
            if card and pack is not None and pick is not None:
                self._pick(int(pack) + 1, int(pick) + 1, int(card))

        if "EventSetDeck" in text or '"DraftStatus":"Completed"' in text:
            self.state.complete = self.changed = True


class LogFollower:
    """Reads a Player.log as it grows. Arena starts a new log each session
    (the old one becomes Player-prev.log), which shows up as the file shrinking."""

    def __init__(self, path: Path, tracker: DraftTracker):
        self.path, self.tracker, self.offset = path, tracker, 0

    def poll(self) -> bool:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return False
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return False
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read(size - self.offset)
        # Only whole lines: Arena may be halfway through writing the last one.
        end = chunk.rfind(b"\n") + 1
        self.offset += end
        self.tracker.changed = False
        for line in chunk[:end].decode("utf-8", errors="replace").splitlines():
            self.tracker.feed(line)
        return self.tracker.changed


def find_player_log(extra: str | None = None) -> Path | None:
    for directory in ([extra] if extra else []) + LOG_DIRS:
        base = Path(directory).expanduser()
        if base.is_file():
            return base
        if (base / "Player.log").exists():
            return base / "Player.log"
    return None


CARD_DB_DIRS = [
    "~/Library/Application Support/com.wizards.mtga/Downloads/Raw",                  # macOS
    "C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Raw",            # Windows
    "C:/Program Files (x86)/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Raw",
    "C:/Program Files/Epic Games/MagicTheGathering/MTGA_Data/Downloads/Raw",
    "C:/Program Files (x86)/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw",
    "~/.local/share/Steam/steamapps/common/MTGA/MTGA_Data/Downloads/Raw",            # Linux
]


def card_database() -> Path | None:
    extra = os.environ.get("MTGA_CARD_DB")
    if extra:
        return Path(extra).expanduser()
    for directory in CARD_DB_DIRS:
        found = sorted(Path(directory).expanduser().glob("Raw_CardDatabase_*.mtga"),
                       key=lambda p: p.stat().st_mtime)
        if found:
            return found[-1]
    return None


def arena_card_names(db: Path | None = None) -> dict[int, str]:
    """grpId -> English card name, from the card database Arena installs."""
    db = db or card_database()
    if db is None or not db.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT c.GrpId, l.Loc FROM Cards c JOIN Localizations_enUS l "
                           "ON l.LocId = c.TitleId WHERE c.IsToken = 0").fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    # Titles carry layout tags (<nobr>Long-Bodied</nobr> Grey Dog).
    return {int(grp): re.sub(r"<[^>]+>", "", loc) for grp, loc in rows if loc}


class CardNames:
    """grpId -> name: Arena's database first, then Scryfall (cached)."""

    def __init__(self, db: Path | None = None):
        self.known = arena_card_names(db)

    def __call__(self, ids: list[int]) -> list[str]:
        missing = [i for i in ids if i not in self.known]
        if missing:
            self.known.update(arena_names(missing))
        return [self.known.get(i, f"#{i}") for i in ids]
