"""Classifica faixas e álbuns: estúdio, ao vivo, acústico, demo, remix, instrumental.

Só olha os qualificadores do título ("- Live", "(Acoustic Version)") e o nome do
álbum. Assim "Live and Learn" e "To Live Is to Die" continuam sendo de estúdio.
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
    # "Holy Live", "Live After Death", "Live in São Paulo"; não "Long Live Rock 'n' Roll".
    "ao_vivo": re.compile(r"(^live\b|\blive$|\blive (at|in|from|on|em|no|na)\b|\b(ao vivo|en vivo|en directo|in concert|unplugged)\b)"),
    "acustico": re.compile(r"\b(acoustic|acustic[oa]|unplugged|desplugado)\b"),
    "demo": re.compile(r"\bdemos?\b"),
    "remix": re.compile(r"\b(remix|remixes|remixed)\b"),
    "coletanea": re.compile(r"\b(greatest hits|best of|the very best|essential|anthology|collection|melhores|grandes sucessos|maiores sucessos|the singles)\b"),
    "edicao_especial": re.compile(r"\b(deluxe|expanded|anniversary|edition|edicao|box set)\b"),
    "remaster": re.compile(r"\bremaster(ed)?\b"),
}


def qualifiers(titulo: str) -> list[str]:
    """Trechos entre parênteses/colchetes e depois de " - " no título."""
    parts = [a or b for a, b in re.findall(r"\(([^)]*)\)|\[([^\]]*)\]", titulo)]
    plain = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", titulo)
    parts += plain.split(" - ")[1:]
    return [normalize(p) for p in parts if p.strip()]


def classify(titulo: str = "", album: str = "", tipo_album: str = "") -> dict:
    marks = set()
    for part in qualifiers(titulo or ""):
        marks.update(name for name, pattern in QUALIFIER.items() if pattern.search(part))
    album_text = normalize(album or "")
    if album_text:
        marks.update(name for name, pattern in ALBUM.items() if pattern.search(album_text))
        for part in qualifiers(album):
            marks.update(name for name, pattern in QUALIFIER.items() if pattern.search(part))
    if (tipo_album or "").casefold() == "compilation":
        marks.add("coletanea")
    kind = next((t for t in KINDS if t in marks), "estudio")
    marks.add(kind)
    order = list(KINDS) + ["remaster", "coletanea", "edicao_especial"]
    marks = sorted(marks, key=order.index)
    return {"tipo": kind, "marcas": marks, "rotulos": [LABELS[m] for m in marks]}


# Temporary aliases for server.py during the English rename (step B); remove when server.py is updated.
classificar = classify
