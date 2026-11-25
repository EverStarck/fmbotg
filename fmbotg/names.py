"""Canonical forms of music names. A leaf module: it imports nothing from the bot.

- `name_key`: case-insensitive match key used in the database (portable replacement for citext)
- `normalize_answer`: loose form for comparing guesses and names across services
- `genre_key`: 'Hip Hop', 'hip-hop' and 'hiphop' are the same genre
"""

import re
import unicodedata


def name_key(value: str | None) -> str:
    return (value or "").strip().lower()[:512]


def genre_key(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name.lower())


_REPLACEMENTS = {"Ø": "O", "ø": "o", "ß": "ss", "æ": "ae", "Æ": "Ae", "å": "a", "Å": "A", "Λ": "A",
                 "&": "and", "…": "..."}
_DROPPED = set("?!'’‘:-()/\" .,")
_EDITION = re.compile(
    r"\s*[(\[][^)\]]*(deluxe|edition|remaster|expanded|anniversary|version|bonus|explicit)[^)\]]*[)\]]\s*$"
    r"|\s+-\s+[^-]*(deluxe|edition|remaster|expanded|anniversary|version)[^-]*$",
    re.IGNORECASE,
)


def remove_edition_suffix(name: str) -> str:
    """'OK Computer (Remastered)' -> 'OK Computer' (port of StringExtensions.RemoveEditionSuffix)."""
    previous = None
    while previous != name:
        previous, name = name, _EDITION.sub("", name)
    return name


def normalize_answer(text: str) -> str:
    """Lowercase, no accents, no punctuation or spaces (port of GameService.NormalizeAnswer)."""
    text = "".join(_REPLACEMENTS.get(c, c) for c in text.strip())
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn" and c not in _DROPPED)
    return unicodedata.normalize("NFC", text).lower()
