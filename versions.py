"""Classifies tracks and albums: studio, live, acoustic, demo, remix, instrumental.

It only looks at the title qualifiers ("- Live", "(Acoustic Version)") and the album
name, so "Live and Learn" and "To Live Is to Die" are still studio recordings.
"""

from __future__ import annotations

import re

from search import normalize

KINDS = ("ao_vivo", "acustico", "demo", "remix", "instrumental", "estudio")
LABELS = {
    "ao_vivo": "Ao vivo", "acustico": "Acústico", "demo": "Demo", "remix": "Remix",
    "instrumental": "Instrumental", "estudio": "Estúdio", "remaster": "Remaster",
    "coletanea": "Coletânea", "edicao_especial": "Edição especial",
}

_LIVE = r"\b(live|ao vivo|en vivo|en directo|in concert|unplugged)\b"
QUALIFIER = {
    "ao_vivo": re.compile(_LIVE),
    "acustico": re.compile(r"\b(acoustic|acustic[oa]|unplugged|desplugado)\b"),
    "demo": re.compile(r"\bdemo\b"),
    "remix": re.compile(r"\b(remix|rmx|club mix|extended mix|dub mix)\b"),
    "instrumental": re.compile(r"\binstrumental\b"),
    "remaster": re.compile(r"\bremaster(ed)?\b"),
}
ALBUM = {
    # "Holy Live", "Live After Death", "Live in São Paulo"; not "Long Live Rock 'n' Roll".
    "ao_vivo": re.compile(r"(^live\b|\blive$|\blive (at|in|from|on|em|no|na)\b|\b(ao vivo|en vivo|en directo|in concert|unplugged)\b)"),
    "acustico": re.compile(r"\b(acoustic|acustic[oa]|unplugged|desplugado)\b"),
    "demo": re.compile(r"\bdemos?\b"),
    "remix": re.compile(r"\b(remix|remixes|remixed)\b"),
    "coletanea": re.compile(r"\b(greatest hits|best of|the very best|essential|anthology|collection|melhores|grandes sucessos|maiores sucessos|the singles)\b"),
    "edicao_especial": re.compile(r"\b(deluxe|expanded|anniversary|edition|edicao|box set)\b"),
    "remaster": re.compile(r"\bremaster(ed)?\b"),
}


def qualifiers(title: str) -> list[str]:
    """Parts of the title inside parentheses/brackets and after " - "."""
    parts = [a or b for a, b in re.findall(r"\(([^)]*)\)|\[([^\]]*)\]", title)]
    plain = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)
    parts += plain.split(" - ")[1:]
    return [normalize(p) for p in parts if p.strip()]


def classify(title: str = "", album: str = "", album_kind: str = "") -> dict:
    marks = set()
    for part in qualifiers(title or ""):
        marks.update(name for name, pattern in QUALIFIER.items() if pattern.search(part))
    album_text = normalize(album or "")
    if album_text:
        marks.update(name for name, pattern in ALBUM.items() if pattern.search(album_text))
        for part in qualifiers(album):
            marks.update(name for name, pattern in QUALIFIER.items() if pattern.search(part))
    if (album_kind or "").casefold() == "compilation":
        marks.add("coletanea")
    kind = next((t for t in KINDS if t in marks), "estudio")
    marks.add(kind)
    order = list(KINDS) + ["remaster", "coletanea", "edicao_especial"]
    marks = sorted(marks, key=order.index)
    return {"tipo": kind, "marcas": marks, "rotulos": [LABELS[m] for m in marks]}


# Temporary aliases for server.py during the English rename (step B); remove when server.py is updated.
classificar = classify
