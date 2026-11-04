"""Sorting and filtering of tracks for the library views."""

import re

from acervo.library.versions import classify
from acervo.messages import _
from acervo.search import normalize


def first_number(values):
    match = re.search(r"\d+", " ".join(values or []))
    return int(match.group()) if match else None


def sort_text(track, field):
    return normalize(" ".join(track.get(field) or [])) or None


SORT_VALUES = {
    "title": lambda t: sort_text(t, "titulo"),
    "artist": lambda t: sort_text(t, "artista"),
    "album": lambda t: sort_text(t, "album"),
    "genre": lambda t: sort_text(t, "genero"),
    "year": lambda t: first_number(t.get("ano")),
    "duration": lambda t: t.get("duracao_segundos"),
    "modified": lambda t: (t.get("assinatura_antes") or {}).get("mtime_ns"),
}


def sort_tracks(rows, sort="default", direction="asc"):
    if sort != "default" and sort not in SORT_VALUES:
        raise ValueError(_("Invalid sort order"))
    if direction not in ("asc", "desc"):
        raise ValueError(_("Invalid sort direction"))
    if sort == "default":
        return rows
    # Tracks of one album stay in disc and track order inside any equal value.
    ordered = sorted(
        rows,
        key=lambda t: (
            sort_text(t, "artista_album") or sort_text(t, "artista") or "",
            sort_text(t, "album") or "",
            first_number(t.get("disco")) or 0,
            first_number(t.get("faixa")) or 0,
            sort_text(t, "titulo") or "",
        ),
    )
    value = SORT_VALUES[sort]
    known = [t for t in ordered if value(t) is not None]
    return sorted(known, key=value, reverse=direction == "desc") + [t for t in ordered if value(t) is None]


def album_key(track):
    return (tuple(track["artista_album"] or track["artista"]), tuple(track["album"]))


def track_version(track):
    return classify(" / ".join(track.get("titulo", [])), " / ".join(track.get("album", [])))


def version_matches(version, choice):
    marks = set(version["marcas"])
    if choice == "todas":
        return True
    if choice == "demo-remix":
        return bool(marks & {"demo", "remix"})
    if choice == "estudio":
        return version["tipo"] == "estudio" and "coletanea" not in marks
    return choice in marks
