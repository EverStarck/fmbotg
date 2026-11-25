"""Game logic for /jumble and /pixelation (port of GameService): picking an answer, scrambling,
pixelating and checking guesses. Telegram handling lives in handlers/games.py."""

import io
import random
from collections import deque
from collections.abc import Sequence
from itertools import count

from PIL import Image

from .domain import UserFacingError
from .names import normalize_answer, remove_edition_suffix

JUMBLE_SECONDS = 25
PIXELATION_SECONDS = 40
# Pixel blocks across the cover, from unrecognisable to nearly clear
PIXELATION_LEVELS = (6, 10, 16, 26, 42)
MIN_CONTAINED_LENGTH = 5  # shorter answers must be guessed exactly
RECENT_ANSWERS = 30  # per person, skipped in their next games

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
    # The answer inside a longer guess ("it's radiohead!") counts, but not for very short answers,
    # or ordinary chat like "not fair" would win a game whose answer is "Air"
    if any(w == given or (len(w) >= MIN_CONTAINED_LENGTH and w in given) for w in wanted):
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


class GameSessions:
    """Running games (one per chat) and each person's recent answers, so games don't repeat.

    Games live in memory only; a restart ends them.
    """

    def __init__(self):
        self._by_chat: dict[int, object] = {}
        self._by_id: dict[int, object] = {}
        self._recent: dict[int, deque] = {}
        self._ids = count(1)

    def new_id(self) -> int:
        return next(self._ids)

    def ensure_free(self, chat_id: int) -> None:
        if chat_id in self._by_chat:
            raise UserFacingError("A game is already running here. Finish it first, or give up with its button.")

    def claim(self, game) -> None:
        """Register a game for its chat, or raise if one is already running there."""
        self.ensure_free(game.chat_id)
        self._by_chat[game.chat_id] = game
        self._by_id[game.id] = game

    def release(self, game) -> None:
        if self._by_chat.get(game.chat_id) is game:
            del self._by_chat[game.chat_id]
        self._by_id.pop(game.id, None)

    def in_chat(self, chat_id: int):
        return self._by_chat.get(chat_id)

    def by_id(self, game_id: int):
        return self._by_id.get(game_id)

    def remember(self, telegram_id: int, answer: str) -> None:
        self._recent.setdefault(telegram_id, deque(maxlen=RECENT_ANSWERS)).append(normalize_answer(answer))

    def recent(self, telegram_id: int) -> set[str]:
        return set(self._recent.get(telegram_id, ()))
