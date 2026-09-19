"""Fetch a set's cards from Scryfall: the compiler's input.

    mulligan ingest hob          # writes data/raw/hob.json (gitignored)

One request per 175 cards, paced and identified per Scryfall's API guidelines.
Only the fields the compiler reads are kept. Adapted from mtg_ai's set ingest.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ..paths import cache_dir

SEARCH_URL = "https://api.scryfall.com/cards/search"
HEADERS = {"User-Agent": "mulligan/0.1 (github.com/deknapp/mulligan)",
           "Accept": "application/json"}
KEEP = ("name", "mana_cost", "type_line", "oracle_text", "power", "toughness", "rarity",
        "colors", "color_identity", "keywords", "collector_number", "arena_id", "layout",
        "produced_mana")
FACE_KEEP = ("name", "mana_cost", "type_line", "oracle_text", "power", "toughness")
RAW_DIR = cache_dir("raw")


def fetch_set(code: str) -> list[dict]:
    query = urllib.parse.urlencode({"q": f"set:{code} game:arena", "unique": "cards",
                                    "order": "set"})
    url: str | None = f"{SEARCH_URL}?{query}"
    cards: list[dict] = []
    while url:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request) as response:
            page = json.load(response)
        cards.extend(page.get("data", []))
        url = page.get("next_page") if page.get("has_more") else None
        time.sleep(0.1)
    return [slim(c) for c in cards]


def slim(card: dict) -> dict:
    out = {k: card[k] for k in KEEP if k in card}
    if card.get("card_faces"):
        out["card_faces"] = [{k: f[k] for k in FACE_KEEP if k in f} for f in card["card_faces"]]
    return out


def write_raw(code: str, out_dir: Path = RAW_DIR) -> Path:
    cards = fetch_set(code)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{code.lower()}.json"
    path.write_text(json.dumps(cards, indent=1, ensure_ascii=False))
    return path
