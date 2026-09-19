"""Fetch a set's live 17Lands numbers for the format tools.

Run by the Pages workflow once a day (``python src/mulligan/site/live.py fra
site/data``), so the tools show real players' results next to the simulator's
as soon as a set is out, without anyone re-running a simulation. Standard
library only: the workflow runs it without installing the package.

Writes ``<set>-17lands.json``::

    {"fetched": "...", "format": "PremierDraft",
     "cards": {name: {"gih": 0.57, "gih_n": 1200, "alsa": 3.1, "ata": 2.4}},
     "pairs": {"WU": [wins, games], ...}}

Before a set's release 17Lands has nothing, and the file says so with empty
maps; the tools then show simulated numbers only. 17Lands gates card win rates
on sample size, so early on only some cards have one.
"""

from __future__ import annotations

import datetime
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://www.17lands.com"
HEADERS = {"User-Agent": "mulligan-format-tools (+https://github.com/deknapp/mulligan)"}


def _get(path: str):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch(set_code: str, fmt: str = "PremierDraft") -> dict:
    code = set_code.upper()
    today = datetime.date.today().isoformat()
    start = _get("/data/filters").get("start_dates", {}).get(code, "")[:10]
    out = {"fetched": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
           "format": fmt, "start": start, "cards": {}, "pairs": {}}
    if not start or start > today:
        return out
    for row in _get(f"/card_ratings/data?expansion={code}&format={fmt}"
                    f"&start_date={start}&end_date={today}"):
        out["cards"][row["name"]] = {
            "gih": row.get("ever_drawn_win_rate"),
            "gih_n": row.get("ever_drawn_game_count") or 0,
            "alsa": row.get("avg_seen"), "ata": row.get("avg_pick")}
    for row in _get(f"/color_ratings/data?expansion={code}&event_type={fmt}"
                    f"&start_date={start}&end_date={today}&combine_splash=true"):
        # short_name is the colors in WUBRG order, the same keys the simulator uses.
        pair = str(row.get("short_name", ""))
        if len(pair) == 2 and not row.get("is_summary") and row.get("games"):
            out["pairs"][pair] = [row["wins"], row["games"]]
    return out


def main(argv: list[str]) -> None:
    set_code, dest = argv[1], Path(argv[2])
    dest.mkdir(parents=True, exist_ok=True)
    data = fetch(set_code)
    path = dest / f"{set_code.lower()}-17lands.json"
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {path}: {len(data['cards'])} cards, {len(data['pairs'])} pairs")


if __name__ == "__main__":
    main(sys.argv)
