"""User-facing messages: English source text with a Brazilian Portuguese catalogue.

Code writes messages in English through `_()`. The language comes from the
current request (the web interface sends it) or, on the command line, from the
usual locale variables (LANGUAGE, LC_ALL, LC_MESSAGES, LANG).
"""

from __future__ import annotations

from contextlib import contextmanager
import contextvars
import os

SUPPORTED = ("en", "pt-BR")
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("acervo_language", default=None)

# English source -> Portuguese. Placeholders use str.format names.
PT_BR: dict[str, str] = {}


def normalize_language(value: str | None) -> str | None:
    """"pt", "pt_BR.UTF-8", "pt-br" -> "pt-BR"; "en_US" -> "en"; unknown -> None."""
    if not value:
        return None
    code = value.strip().split(".")[0].split("@")[0].replace("_", "-").casefold()
    if code.startswith("pt"):
        return "pt-BR"
    if code.startswith("en"):
        return "en"
    return None


def environment_language() -> str:
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        for part in os.environ.get(name, "").split(":"):
            found = normalize_language(part)
            if found:
                return found
    return "en"


def current_language() -> str:
    return _current.get() or environment_language()


@contextmanager
def language(value: str | None):
    """Use a language for the duration of a request; invalid values fall back to the default."""
    token = _current.set(normalize_language(value))
    try:
        yield current_language()
    finally:
        _current.reset(token)


def _(text: str, **values) -> str:
    template = PT_BR.get(text, text) if current_language() == "pt-BR" else text
    return template.format(**values) if values else template


def register(entries: dict[str, str]) -> None:
    """Add catalogue entries (used by modules that keep their translations close by)."""
    for source, translation in entries.items():
        if PT_BR.get(source, translation) != translation:
            raise ValueError(f"Conflicting translation for: {source}")
        PT_BR[source] = translation
