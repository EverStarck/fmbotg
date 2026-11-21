"""Game logic for /jumble and /pixelation (port of GameService): picking an answer, scrambling,
pixelating and checking guesses. Telegram handling lives in handlers/games.py."""

import io
import random
import re
import unicodedata
from collections.abc import Sequence

from PIL import Image

JUMBLE_SECONDS = 25
PIXELATION_SECONDS = 40
# Pixel blocks across the cover, from unrecognisable to nearly clear
PIXELATION_LEVELS = (6, 10, 16, 26, 42)

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


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def answer_is_right(correct: str, guess: str) -> bool:
    """Exact after normalising, the answer inside a longer guess, or one typo (two for long names)."""
    wanted = {normalize_answer(correct), normalize_answer(remove_edition_suffix(correct))} - {""}
    given = normalize_answer(remove_edition_suffix(guess))
    if not given:
        return False
    if any(w == given or w in given for w in wanted):
        return True
    distance = min(levenshtein(given, w) for w in wanted)
    return (len(given) > 4 and distance == 1) or (len(given) > 10 and distance == 2)


def scramble(name: str, rng: random.Random | None = None) -> str:
    """Shuffle the letters of each word, keeping word lengths, and never return the name itself."""
    rng = rng or random.Random()
    words = name.upper().split()
    for _ in range(10):
        shuffled = []
        for word in words:
            letters = list(word)
            rng.shuffle(letters)
            shuffled.append("".join(letters))
        result = " ".join(shuffled)
        if result != name.upper():
            return result
    return " ".join(reversed(words)) if len(words) > 1 else name.upper()[::-1]


def pick(candidates: Sequence[tuple[str, int]], recent: set[str], rng: random.Random | None = None,
         ) -> tuple[str, int] | None:
    """A random well-known answer from (name, your plays) (simplified GameService.PickArtistForJumble).

    The minimum playcount drops as the pool shrinks, so small libraries still get a game, and
    recent answers are skipped while there are others.
    """
    rng = rng or random.Random()
    pool = [(n, p) for n, p in candidates if 2 < len(n) < 40]
    if not pool:
        return None
    fresh = [(n, p) for n, p in pool if normalize_answer(n) not in recent] or pool
    for minimum in (30, 15, 5, 1):
        eligible = [(n, p) for n, p in fresh if p >= minimum]
        if len(eligible) >= 5 or minimum == 1:
            return rng.choice(eligible or fresh)
    return None


def first_letter_hint(name: str) -> str:
    """'Radiohead' -> 'R________'; spaces and punctuation stay visible."""
    return "".join(c if i == 0 or not c.isalnum() else "_" for i, c in enumerate(name))


def pixelate(image: bytes, blocks: int) -> bytes:
    """Cover image -> JPEG with `blocks` x `blocks` pixels, scaled back up."""
    with Image.open(io.BytesIO(image)) as source:
        cover = source.convert("RGB")
    size = 600
    cover = cover.resize((size, size))
    small = cover.resize((blocks, blocks), Image.Resampling.BILINEAR)
    out = io.BytesIO()
    small.resize((size, size), Image.Resampling.NEAREST).save(out, "JPEG", quality=85)
    return out.getvalue()
