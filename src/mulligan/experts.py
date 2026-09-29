"""What the experts say: podcast set reviews, distilled into grades and takes.

Limited Resources, Limited Level-Ups and Lords of Limited review every set
card by card, and TCGplayer publishes written grades. This
turns those episodes into data the site can put next to the simulator:

1. Transcribe each episode locally with Whisper (``data/raw/podcasts``; the
   audio and transcripts stay on this machine and are never committed).
2. ``extract``: Claude reads one transcript and returns each host's grade and a
   one-line paraphrased take per card, plus what they said about the format.
   Saved per episode under ``blog/data/experts/<set>/``.
3. ``synthesize``: Claude reads every episode's extract (not the transcripts)
   next to the simulator's numbers and writes one overview of the format:
   where the hosts agree, where they differ, and where the simulation disagrees.

Everything published is paraphrase with credit to the show; nothing is quoted
at length. Costs API money: run it when a new episode comes out.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cards.sets import load_set

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "podcasts"
OUT = ROOT / "blog" / "data"
MODEL = "claude-opus-5-5"
GRADES = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"]
COLORS = ["W", "U", "B", "R", "G"]
PAIRS = ["WU", "UB", "BR", "RG", "WG", "WB", "UR", "BG", "WR", "UG"]

LOL_HOSTS = ["Ethan", "Ben"]

# Episodes per set: transcript stem -> where it came from.
EPISODES = {
    "fra": {
        "lr-872-fra-cu-review": {
            "show": "Limited Resources", "title": "#872: Reality Fracture Set Review, "
            "Commons and Uncommons", "date": "2026-09-21",
            "url": "https://lrcast.com/", "hosts": ["Marshall", "Luis"]},
        "llu-fra-first-impressions": {
            "show": "Limited Level-Ups", "title": "#261: Reality Fracture First Impressions",
            "date": "2026-09-18", "url": "https://limitedlevelups.libsyn.com/",
            "hosts": ["Marc", "Alex"]},
        "llu-fra-cu-review": {
            "show": "Limited Level-Ups", "title": "Reality Fracture Common + Uncommon "
            "Set Review", "date": "2026-09-22", "url": "https://limitedlevelups.libsyn.com/",
            "hosts": ["Marc", "Alex"]},
        "llu-fra-rm-review": {
            "show": "Limited Level-Ups", "title": "Reality Fracture Rare + Mythic "
            "Set Review", "date": "2026-09-25", "url": "https://limitedlevelups.libsyn.com/",
            "hosts": ["Marc", "Alex"]},
        "lol-496-fra-first-impressions": {
            "show": "Lords of Limited", "title": "#496: Our First Impressions of Reality "
            "Fracture", "date": "2026-09-14", "url": "https://audioboom.com/posts/8952669",
            "hosts": LOL_HOSTS},
        "lol-497-fra-prerelease": {
            "show": "Lords of Limited", "title": "#497: Our Prerelease Guide for Reality "
            "Fracture", "date": "2026-09-21", "url": "https://audioboom.com/posts/8955341",
            "hosts": LOL_HOSTS},
        "lol-498-fra-early-guide": {
            "show": "Lords of Limited", "title": "#498: This is the Way! Reality Fracture "
            "Early Limited Guide", "date": "2026-09-28",
            "url": "https://audioboom.com/posts/8958057", "hosts": LOL_HOSTS},
        # Written, not audio: the six color articles fetched as one text file.
        "tcg-fra-set-review": {
            "show": "TCGplayer", "title": "Reality Fracture Limited Set Reviews",
            "date": "2026-09-25", "url": "https://www.tcgplayer.com/content/article/"
            "Reality-Fracture-Limited-Magic-The-Gathering-Set-Review-White/"
            "96d152d7-f0f7-498a-a6e2-fd1f738f0641/", "hosts": ["LSV", "Martin"]},
    },
}

# Hosts who publish their set-review grades as 17Lands tier lists. Those are
# the grades as they wrote them down, so they beat what we hear in the audio.
TIER_LISTS = {
    "fra": {("Limited Level-Ups", "Marc"): "e95fd9365a86464cae2451e34c39102a",
            ("Limited Level-Ups", "Alex"): "3ceeafffe39348e6824ea8605b3e6e89"},
}
TIER_URL = "https://www.17lands.com/card_tiers/data/{id}"

EXTRACT_SYSTEM = """You turn a Magic: The Gathering Limited podcast transcript into data.

A written article instead of a transcript has exact card names, and each
section says who wrote it; attribute by that.

A transcript is automatic speech recognition: card names are often misheard
("Ghoulta" for "Ghalta"), speakers are not labeled, and hosts talk over each
other. Match every card discussed to its exact name from the card list; skip
anything you cannot match with confidence.

The hosts are {hosts}; use exactly these names.

Rules:
- One entry per card per host who graded or clearly evaluated it. Tell the
  hosts apart by name, by the show's format ("Luis, what do you have?"), and by
  who is disagreeing with whom. If you cannot tell who said it, use "both".
- grade_said: the grade as spoken, on the show's own scale (e.g. "3.5", "B-").
  grade: the same on a letter scale. For a 0-5 scale use 5.0=A+, 4.5=A,
  4.0=A-, 3.5=B+, 3.0=B, 2.5=C+, 2.0=C, 1.5=C-/D+, 1.0=D, 0.5=D-, 0=F.
  "none" when they evaluated it without a grade.
- take: one sentence, your paraphrase of their reasoning (never a quote of
  more than a few words). Say what makes it good or bad, or when to play it.
- format: what they said about the format as a whole, colors, color pairs,
  mechanics, speed, and concrete drafting advice. Paraphrase; be specific.

The set's cards:
{cards}"""

SYNTH_SYSTEM = """You write the "what the experts say" page of a Limited draft site.

You get (1) structured notes extracted from several podcast episodes reviewing
the set, each host's grades and takes, and (2) the site's own simulation
results: bots drafting and playing the set many thousands of times.

Write for a player about to draft the format. Synthesize, don't list episode
by episode. Attribute opinions to the show and host ("Luis on Limited
Resources rates..."). Paraphrase only. Be concrete: name cards, pairs, numbers.
Where the experts and the simulation disagree, say so plainly and say which
has the better case, remembering that the simulation's bots are decent but not
expert players and that the experts haven't played the set yet either. Keep it
tight: each field a few sentences unless it says otherwise."""


def _schema(names: list[str]) -> dict:
    take = {"type": "object", "additionalProperties": False}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["hosts", "scale", "cards", "format"],
        "properties": {
            "hosts": {"type": "array", "items": {"type": "string"}},
            "scale": {"type": "string", "description": "The grading scale the show uses."},
            "cards": {"type": "array", "items": {
                **take, "required": ["card", "host", "grade_said", "grade", "take"],
                "properties": {
                    "card": {"type": "string", "enum": names},
                    "host": {"type": "string"},
                    "grade_said": {"type": "string"},
                    "grade": {"type": "string", "enum": [*GRADES, "none"]},
                    "take": {"type": "string"}}}},
            "format": {**take, "required": ["summary", "speed", "colors", "pairs",
                                            "mechanics", "advice"],
                       "properties": {
                "summary": {"type": "string"},
                "speed": {"type": "string"},
                "colors": {"type": "array", "items": {
                    **take, "required": ["color", "take"],
                    "properties": {"color": {"type": "string", "enum": COLORS},
                                   "take": {"type": "string"}}}},
                "pairs": {"type": "array", "items": {
                    **take, "required": ["pair", "take"],
                    "properties": {"pair": {"type": "string", "enum": PAIRS},
                                   "take": {"type": "string"}}}},
                "mechanics": {"type": "array", "items": {
                    **take, "required": ["name", "take"],
                    "properties": {"name": {"type": "string"}, "take": {"type": "string"}}}},
                "advice": {"type": "array", "items": {"type": "string"}}}},
        },
    }


SYNTH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["overview", "speed", "consensus", "debates", "colors", "pairs",
                 "sim_vs_experts", "advice"],
    "properties": {
        "overview": {"type": "string", "description": "Two or three short paragraphs "
                     "separated by blank lines: the format in a nutshell."},
        "speed": {"type": "string"},
        "consensus": {"type": "array", "items": {"type": "string"},
                      "description": "Points every show agrees on."},
        "debates": {"type": "array", "items": {"type": "string"},
                    "description": "Where hosts or shows disagree, and on what."},
        "colors": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["color", "take"],
            "properties": {"color": {"type": "string", "enum": COLORS},
                           "take": {"type": "string"}}}},
        "pairs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["pair", "experts", "sim"],
            "properties": {"pair": {"type": "string", "enum": PAIRS},
                           "experts": {"type": "string"},
                           "sim": {"type": "string",
                                   "description": "How the simulation's numbers compare."}}}},
        "sim_vs_experts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["card", "note"],
            "properties": {"card": {"type": "string"}, "note": {"type": "string"}}},
            "description": "The most instructive cards where experts and simulation "
                           "disagree, up to ten."},
        "advice": {"type": "array", "items": {"type": "string"},
                   "description": "Five to eight concrete drafting tips."},
    },
}


def _client():
    import os

    import anthropic
    env = ROOT / ".env"    # gitignored; KEY=value lines
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() and value and not line.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"'))
    return anthropic.Anthropic()


def _ask(client, system: str, user: str, schema: dict) -> dict:
    with client.beta.messages.stream(
        model=MODEL, max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"model stopped early: {message.stop_reason}")
    text = next(b.text for b in message.content if b.type == "text")
    u = message.usage
    print(f"  tokens: {u.input_tokens} in (+{u.cache_read_input_tokens or 0} cached), "
          f"{u.output_tokens} out", flush=True)
    return json.loads(text)


def extract(set_code: str, slug: str, client=None) -> Path:
    """One episode's transcript -> grades and takes."""
    data = load_set(set_code)
    names = sorted(e["name"] for e in data.entries.values()
                   if "Basic" not in e.get("supertypes", []))
    cards = "\n".join(f"{n} ({data.entries[n].get('cost', '')}) {data.entries[n]['types']}"
                      for n in names)
    transcript = (RAW / f"{slug}.txt").read_text()
    meta = EPISODES[set_code][slug]
    user = (f"{meta['show']}, {meta['title']} ({meta['date']}).\n\n"
            f"<transcript>\n{transcript}\n</transcript>")
    system = EXTRACT_SYSTEM.replace("{cards}", cards).replace(
        "{hosts}", " and ".join(meta["hosts"]))
    result = _ask(client or _client(), system, user, _schema(names))
    out = OUT / "experts" / set_code / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**meta, **result}, indent=1, ensure_ascii=False))
    return out


def tier_lists(set_code: str, refresh: bool = False) -> dict[str, dict[str, str]]:
    """"show|host" -> card -> grade, from the hosts' published tier lists."""
    import urllib.request
    path = OUT / "experts" / set_code / "tier-lists.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    out = {}
    for (show, host), tier_id in TIER_LISTS.get(set_code, {}).items():
        request = urllib.request.Request(TIER_URL.format(id=tier_id), headers={
            "User-Agent": "mulligan/0.1 (github.com/deknapp/mulligan)"})
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
        out[f"{show}|{host}"] = {r["name"].split(" // ")[0]: r["tier"] for r in rows
                                 if r["tier"] in GRADES}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out


def card_grades(episodes: list[dict], tiers: dict[str, dict[str, str]]) -> dict[str, dict]:
    """Card -> {"grades": one per show and host, "takes": every take, oldest
    first}. A grade given by "both" counts for each host of the show; a
    published tier-list grade beats one heard in the audio."""
    hosts = {ep["show"]: ep["hosts"] for ep in episodes}
    grades: dict[str, dict[tuple[str, str], dict]] = {}
    takes: dict[str, list[dict]] = {}
    for ep in sorted(episodes, key=lambda e: e["date"]):
        for c in ep["cards"]:
            takes.setdefault(c["card"], []).append(
                {"show": ep["show"], "host": c["host"], "take": c["take"]})
            if c["grade"] == "none":
                continue
            for host in hosts[ep["show"]] if c["host"] == "both" else [c["host"]]:
                grades.setdefault(c["card"], {})[ep["show"], host] = {
                    "show": ep["show"], "host": host, "grade": c["grade"],
                    "said": c["grade_said"]}
    for key, graded in tiers.items():
        show, host = key.split("|")
        for card, grade in graded.items():
            grades.setdefault(card, {})[show, host] = {
                "show": show, "host": host, "grade": grade, "said": f"{grade} (tier list)"}
    return {card: {"grades": list(grades.get(card, {}).values()),
                   "takes": takes.get(card, [])}
            for card in sorted(set(grades) | set(takes))}


def grade_points(grade: str) -> float | None:
    """A+ = 12 ... F = 0, for averaging."""
    return None if grade not in GRADES else float(len(GRADES) - 1 - GRADES.index(grade))


def _sim_summary(set_code: str) -> str:
    from .site.tools import export, latest_run
    run = latest_run(OUT.parent, set_code)
    d = export(run)
    mean = d["mean"]
    pairs = sorted(((p, r["w"] / r["g"], r["share"]) for p, r in d["pairs"].items()),
                   key=lambda x: -x[1])
    lines = [f"Simulation: {d['decks']} bot-drafted decks, {d['games']} games. "
             f"Average card win rate when drawn {mean:.1%}.", "Color pairs (win rate, share "
             "of decks):"]
    lines += [f"  {p} {rate:.1%} {share:.0%}" for p, rate, share in pairs]
    lines.append("Cards (win rate when drawn, games, average pick):")
    rated = sorted((c for c in d["cards"] if c["g"] >= 300), key=lambda c: -c["w"] / c["g"])
    lines += [f"  {c['n']} [{c['r'].upper()}] {c['w'] / c['g']:.1%} {c['g']} {c['ata']:.1f}"
              for c in rated]
    return "\n".join(lines)


def synthesize(set_code: str, client=None) -> Path:
    """Every extracted episode + the simulation -> one overview of the format."""
    folder = OUT / "experts" / set_code
    episodes = [json.loads((folder / f"{slug}.json").read_text())
                for slug in EPISODES[set_code] if (folder / f"{slug}.json").exists()]
    notes = json.dumps(episodes, ensure_ascii=False)
    user = (f"<expert_notes>\n{notes}\n</expert_notes>\n\n"
            f"<simulation>\n{_sim_summary(set_code)}\n</simulation>")
    synthesis = _ask(client or _client(), SYNTH_SYSTEM, user, SYNTH_SCHEMA)
    out = OUT / f"{set_code}-experts.json"
    out.write_text(json.dumps({
        "set": set_code,
        "episodes": [{k: e[k] for k in ("show", "title", "date", "url", "hosts")}
                     for e in episodes],
        "synthesis": synthesis,
        "cards": card_grades(episodes, tier_lists(set_code)),
    }, indent=1, ensure_ascii=False))
    return out
