"""The set compiler: Scryfall cards -> the card data format in ``schema``.

Compilation is split in two, so the LLM only does the part that needs reading:

1. ``skeleton`` — deterministic. Cost, types, stats, evergreen keywords, ward,
   basic-land mana, adventure faces: everything Scryfall already gives as
   structured data. No model is involved, so none of it can be hallucinated.
2. Semantics — the rules text. A model (or a person) writes the ``triggers``,
   ``statics``, ``abilities``, ``effects`` and so on for each card, or marks it
   ``unsupported`` with a reason. ``compile_with_llm`` does this with the
   Anthropic API; the prompt is ``PROMPT`` plus the format reference in
   ``schema``'s docstring, and ``tests/test_sets.py`` checks the result loads.

The output is checked in (``cards/data/<code>.json``), so a set is compiled
once, reviewed like code, and everyone else just plays it.
"""

from __future__ import annotations

import json
import re

from ..engine.types import Keyword
from .schema import CardDataError, card

EVERGREEN = {k.value for k in Keyword}
BASIC_MANA = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
SUPERTYPES = {"Legendary", "Basic", "Snow", "World"}
CARD_TYPES = {"Land", "Creature", "Artifact", "Enchantment", "Instant", "Sorcery"}


def _types(type_line: str) -> tuple[list[str], list[str], list[str]]:
    left, _, right = type_line.partition("—")
    words = left.split()
    supertypes = [w for w in words if w in SUPERTYPES]
    types = [w for w in words if w in CARD_TYPES]
    subtypes = right.split() if right else []
    return supertypes, types, subtypes


def _stat(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value) if value.lstrip("-").isdigit() else None


def _face(face: dict) -> dict:
    supertypes, types, subtypes = _types(face.get("type_line", ""))
    out: dict = {"name": face["name"], "cost": face.get("mana_cost", "")}
    if supertypes:
        out["supertypes"] = supertypes
    out["types"] = types
    if subtypes:
        out["subtypes"] = [s for s in subtypes if s != "Adventure"]
    power, toughness = face.get("power"), face.get("toughness")
    if power is not None:
        out["power"] = _stat(power)
        out["toughness"] = _stat(toughness)
    text = face.get("oracle_text", "")
    keywords = []
    for line in text.split("\n"):
        # A line that is nothing but keywords, like "Flying, deathtouch".
        parts = [p.strip().lower() for p in re.sub(r"\(.*?\)", "", line).split(",")]
        if parts and all(p in EVERGREEN for p in parts if p):
            keywords.extend(p for p in parts if p)
    if keywords:
        out["keywords"] = sorted(set(keywords))
    ward = re.search(r"^Ward \{(\d+)\}", text, re.MULTILINE)
    if ward:
        out["ward"] = int(ward.group(1))
    return out


def skeleton(raw: dict) -> dict:
    """The mechanical part of a card entry, straight from Scryfall fields."""
    faces = raw.get("card_faces") or [raw]
    entry = _face(faces[0] if raw.get("layout") == "adventure" else {**raw, **faces[0]}
                  if raw.get("card_faces") else raw)
    entry["name"] = raw["name"].split(" // ")[0] if raw.get("layout") == "adventure" else raw[
        "name"]
    if raw.get("layout") == "adventure" and len(faces) > 1:
        entry["adventure"] = _face(faces[1])
        if "power" not in entry and raw.get("power") is not None:
            entry["power"], entry["toughness"] = _stat(raw["power"]), _stat(raw["toughness"])
    if "Basic" in entry.get("supertypes", ()) and entry["name"] in BASIC_MANA:
        entry["mana"] = [BASIC_MANA[entry["name"]]]
    entry["rarity"] = raw.get("rarity", "common")
    entry["collector_number"] = raw.get("collector_number", "")
    if raw.get("arena_id"):
        entry["arena_id"] = raw["arena_id"]
    entry["color_identity"] = raw.get("color_identity", [])
    entry["oracle"] = "\n".join(
        (f"[{f['name']}] " if len(faces) > 1 else "") + f.get("oracle_text", "") for f in faces)
    return entry


def merge(base: dict, semantics: dict) -> dict:
    """Overlay a card's compiled semantics on its skeleton. An adventure's
    semantics go under ``adventure`` and merge into that face."""
    out = dict(base)
    for key, value in semantics.items():
        if key == "adventure" and isinstance(value, dict) and "adventure" in out:
            out["adventure"] = {**out["adventure"], **value}
        elif key == "adventure" and value is None:
            out.pop("adventure", None)
        else:
            out[key] = value
    return out


def validate(entry: dict) -> str | None:
    """None if the entry loads, else the error."""
    if entry.get("unsupported"):
        return None
    try:
        card(entry)
    except CardDataError as exc:
        return str(exc)
    return None


PROMPT = """You are compiling Magic: The Gathering cards into a declarative data format
for a rules engine. For each card, return a JSON object with ONLY the rules-text
semantics (the engine already has cost, types, stats and evergreen keywords).

Use exactly the vocabulary in the format reference below. If a card needs a rule
the vocabulary cannot express, either express the rest and list what you left
out in "approximations", or, if what is left would misrepresent the card, return
{{"unsupported": "<reason>"}}. Never invent keys or effect names.

FORMAT REFERENCE:
{reference}

CARD:
{card}

Return only the JSON object."""


def compile_with_llm(raw_cards: list[dict], model: str = "claude-opus-5",
                     client=None) -> list[dict]:  # pragma: no cover - network
    """Compile a set's rules text with the Anthropic API. Costs money: run it
    once per set and commit the output."""
    import anthropic

    from . import schema
    client = client or anthropic.Anthropic()
    out = []
    for raw in raw_cards:
        base = skeleton(raw)
        message = client.messages.create(
            model=model, max_tokens=4000,
            messages=[{"role": "user", "content": PROMPT.format(
                reference=schema.__doc__, card=json.dumps(raw, ensure_ascii=False))}])
        text = message.content[0].text
        semantics = json.loads(text[text.index("{"): text.rindex("}") + 1])
        entry = merge(base, semantics)
        error = validate(entry)
        if error:
            entry = {**base, "unsupported": f"compiler output did not load: {error}"}
        out.append(entry)
    return out
