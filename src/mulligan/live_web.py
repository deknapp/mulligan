"""The live pick helper in a browser: a small local web server next to
``mulligan live``.

``snapshot`` turns the draft so far into plain JSON: every pack you've seen,
ranked the way the helper ranked it at the time, the card you took, and where
your colors stood after each pick. The page (``live_web.html``) polls
``/state.json`` and draws it; nothing leaves your machine except card images,
fetched once from Scryfall and cached.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .draft_log import DraftState
from .paths import cache_dir
from .pick_advice import PAIR_NAMES, Ratings, advise, commitment, lane

PAGE = Path(__file__).with_name("live_web.html")
IMAGE_VERSIONS = ("normal", "art_crop")
SCRYFALL = "https://api.scryfall.com/cards/named"
USER_AGENT = "mulligan/0.1 (+https://github.com/deknapp/mulligan)"


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def _row(a) -> dict:
    c = a.card
    return {"name": c.name, "colors": c.colors, "cost": c.cost, "rarity": c.rarity,
            "grade": c.grade, "z": None if c.z is None else round(c.z, 3),
            "score": None if a.score is None else round(100 * a.score, 2),
            "sim": _pct(c.sim), "sim_games": c.sim_games,
            "real": _pct(c.real), "real_games": c.real_games,
            "removal": c.removal, "land": c.makes, "why": a.why}


def _lane(ratings: Ratings, picks: list[str]) -> dict:
    weight, top = lane(ratings, picks)
    pair = "".join(k for k in "WUBRG" if k in top) if len(top) == 2 else ""
    return {"weight": {k: round(v, 3) for k, v in weight.items()}, "top": top,
            "pair": pair, "pair_name": PAIR_NAMES.get(pair, ""),
            "commit": round(commitment(weight, len(picks)), 3)}


class Snapshots:
    """Builds the page's JSON. Packs you've already picked from never change,
    so their advice is computed once."""

    def __init__(self):
        self._done: dict[tuple, dict] = {}

    def __call__(self, state: DraftState, names, ratings: Ratings, note: str,
                 set_code: str, pairs: dict | None = None) -> dict:
        order = sorted(state.packs)
        taken = dict(zip(sorted(state.picks), names(state.picked), strict=True))
        steps, picks = [], []
        for key in order:
            got = taken.get(key)
            cache_key = (key, got, len(picks), tuple(state.packs[key]))
            step = self._done.get(cache_key) if got else None
            if step is None:
                ranked = advise(ratings, names(state.packs[key]), picks, key[1])
                step = {"pack": key[0], "pick": key[1], "taken": got,
                        "cards": [_row(a) for a in ranked], "before": _lane(ratings, picks)}
                if got:
                    self._done[cache_key] = step
            steps.append(step)
            if got:
                picks.append(got)
        # Picks whose pack never reached the log (a restart mid-draft) still count.
        for key in sorted(set(taken) - set(state.packs)):
            picks.append(taken[key])
        pool = []
        for name in names(state.picked):
            c = ratings.get(name)
            pool.append({"name": name, "colors": c.colors, "cost": c.cost, "grade": c.grade,
                         "z": None if c.z is None else round(c.z, 3), "land": c.makes})
        status = "complete" if state.complete else ("drafting" if state.cards else "none")
        return {"event": state.event, "set": set_code, "status": status,
                "pack": state.pack, "pick": state.pick, "waiting": state.waiting,
                "note": note, "steps": steps, "pool": pool, "now": _lane(ratings, picks),
                "pairs": pairs or {}}


def pair_strength(sim: dict | None) -> dict:
    """Each color pair's simulated deck win rate and share of decks."""
    out = {}
    for p, v in ((sim or {}).get("pairs") or {}).items():
        if v.get("g"):
            out[p] = {"wr": round(v["w"] / v["g"], 4), "share": v.get("share"),
                      "name": PAIR_NAMES.get(p, p)}
    return out


class CardImages:
    """Card images from Scryfall, one request at a time (Scryfall asks for no
    more than ten a second), cached on disk."""

    def __init__(self, offline: bool = False):
        self.offline = offline
        self.lock = threading.Lock()
        self.missing: set[tuple] = set()

    def get(self, set_code: str, version: str, name: str) -> bytes | None:
        safe = re.sub(r"[^\w\-]+", "_", name).strip("_")
        path = cache_dir("images", set_code or "any", version, f"{safe}.jpg")
        if path.exists():
            return path.read_bytes()
        key = (set_code, version, name)
        if self.offline or key in self.missing:
            return None
        with self.lock:
            if path.exists():
                return path.read_bytes()
            data = None
            for params in ({"exact": name, "set": set_code}, {"exact": name}):
                if not params.get("set", True):
                    continue
                query = urllib.parse.urlencode({**params, "format": "image",
                                                "version": version})
                req = urllib.request.Request(f"{SCRYFALL}?{query}",
                                             headers={"User-Agent": USER_AGENT,
                                                      "Accept": "image/*"})
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = resp.read()
                    break
                except OSError:
                    pass
                finally:
                    time.sleep(0.12)
            if data is None:
                self.missing.add(key)
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data


class LiveServer:
    """Serves the page and the latest snapshot on localhost."""

    def __init__(self, port: int = 8765, offline: bool = False):
        self.version = 0
        self.body = b'{"v":0,"status":"none"}'
        self.images = CardImages(offline)
        self.set_code = ""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):     # quiet: the terminal shows the draft
                pass

            def _send(self, code: int, body: bytes, kind: str, cache: bool = False):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(url.query)
                try:
                    if url.path in ("/", "/index.html"):
                        self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                    elif url.path == "/state.json":
                        seen = query.get("v", [""])[0]
                        body = (b'{"v":%d}' % server.version
                                if seen == str(server.version) else server.body)
                        self._send(200, body, "application/json")
                    elif url.path.startswith("/img/"):
                        version, _, name = url.path[5:].partition("/")
                        name = urllib.parse.unquote(name)
                        data = (server.images.get(server.set_code, version, name)
                                if version in IMAGE_VERSIONS else None)
                        if data is None:
                            self._send(404, b"", "text/plain")
                        else:
                            self._send(200, data, "image/jpeg", cache=True)
                    else:
                        self._send(404, b"not found", "text/plain")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        for attempt in range(20):             # the next free port if this one's taken
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port + attempt), Handler)
                break
            except OSError:
                if attempt == 19:
                    raise
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def publish(self, snap: dict) -> None:
        self.set_code = snap.get("set") or self.set_code
        self.version += 1
        self.body = json.dumps({"v": self.version, **snap}).encode()

    def close(self) -> None:
        self.httpd.shutdown()
