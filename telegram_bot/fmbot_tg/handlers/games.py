"""/jumble and /pixelation (port of GameBuilders and the game interactions).

One game per chat. Groups with Telegram's privacy mode on (the default) only send the bot replies
to its own messages, so players answer by replying to the game message; in private chats and
groups where the bot is an admin, any message counts as a guess.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import count

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message,
    ReactionTypeEmoji,
)

from .. import moderation
from ..context import UserFacingError, self_user
from ..db import Database, name_key
from ..formatting import esc, plural
from ..games import (
    JUMBLE_SECONDS, PIXELATION_LEVELS, PIXELATION_SECONDS, answer_is_right, first_letter_hint, normalize_answer,
    pick, pixelate, scramble,
)
from ..lastfm import LastFmClient, LastFmError, as_list
from ..metadata import Metadata, flag
from .info import full_size_image

log = logging.getLogger(__name__)
router = Router(name="games")

CANDIDATES = 250  # top artists/albums a game picks from
RECENT_ANSWERS = 30  # per person, skipped in their next games
_ids = count(1)


class GameButton(CallbackData, prefix="gm"):
    action: str  # "h" hint, "r" reshuffle / clearer, "g" give up
    game: int


@dataclass
class Game:
    id: int
    kind: str  # "jumble" | "pixelation"
    chat_id: int
    answer: str
    artist: str
    hints: list[str]
    shown: int
    started: float = field(default_factory=time.monotonic)
    message_id: int | None = None
    scrambled: str = ""
    cover: bytes | None = None
    level: int = 0
    timer: asyncio.Task | None = None
    done: bool = False


_games: dict[int, Game] = {}  # chat id -> running game
_recent: dict[int, deque] = {}  # telegram user id -> recent answers (normalised)


def _remember(user_id: int, answer: str) -> None:
    _recent.setdefault(user_id, deque(maxlen=RECENT_ANSWERS)).append(normalize_answer(answer))


def _buttons(game: Game) -> InlineKeyboardMarkup:
    second = "🔀 Reshuffle" if game.kind == "jumble" else "🔍 Clearer"
    row = [InlineKeyboardButton(text="💡 Hint", callback_data=GameButton(action="h", game=game.id).pack())]
    if game.kind == "jumble" or game.level < len(PIXELATION_LEVELS) - 1:
        row.append(InlineKeyboardButton(text=second, callback_data=GameButton(action="r", game=game.id).pack()))
    row.append(InlineKeyboardButton(text="🏳️ Give up", callback_data=GameButton(action="g", game=game.id).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _text(game: Game, private: bool) -> str:
    how = "Send your guess." if private else "Reply to this message with your guess."
    hints = "\n".join(f"• {h}" for h in game.hints[:game.shown])
    if game.kind == "jumble":
        return (f"🔀 <b>Jumble</b> · which artist is this?\n\n<code>{esc(game.scrambled)}</code>\n\n"
                f"{hints}\n\n<i>{how} {JUMBLE_SECONDS} seconds.</i>")
    return (f"🖼 <b>Pixelation</b> · which album is this?\n\n{hints}\n\n"
            f"<i>{how} {PIXELATION_SECONDS} seconds; it gets clearer as time passes.</i>")


def _ensure_free(chat_id: int) -> None:
    if chat_id in _games:
        raise UserFacingError("A game is already running here. Finish it first, or give up with its button.")


async def _finish(bot: Bot, game: Game, winner: str | None = None, guess_message: Message | None = None) -> None:
    if game.done:
        return
    game.done = True
    _games.pop(game.chat_id, None)
    if game.timer and game.timer is not asyncio.current_task():
        game.timer.cancel()

    elapsed = time.monotonic() - game.started
    subject = "artist" if game.kind == "jumble" else "album"
    answer = f"<b>{esc(game.answer)}</b>" + (f" by {esc(game.artist)}" if game.kind == "pixelation" else "")
    if winner:
        text = f"✅ {esc(winner)} got it in {elapsed:.1f}s! The {subject} was {answer}."
    elif winner is None and guess_message is None and elapsed >= _limit(game) - 1:
        text = f"⏰ Time's up! The {subject} was {answer}."
    else:
        text = f"🏳️ Game over. The {subject} was {answer}."

    try:  # take the buttons off the game message
        await bot.edit_message_reply_markup(chat_id=game.chat_id, message_id=game.message_id, reply_markup=None)
    except TelegramBadRequest:
        pass
    if game.kind == "pixelation" and game.cover:
        try:
            await bot.edit_message_media(
                chat_id=game.chat_id, message_id=game.message_id,
                media=InputMediaPhoto(media=BufferedInputFile(pixelate(game.cover, 600), "cover.jpg"),
                                      caption=text))
        except TelegramBadRequest:
            pass
    if guess_message is not None:
        await guess_message.reply(text)
    else:
        await bot.send_message(game.chat_id, text, reply_to_message_id=game.message_id)


def _limit(game: Game) -> int:
    return JUMBLE_SECONDS if game.kind == "jumble" else PIXELATION_SECONDS


async def _run_timer(bot: Bot, game: Game) -> None:
    try:
        if game.kind == "pixelation":
            steps = len(PIXELATION_LEVELS) - 1
            for _ in range(steps):
                await asyncio.sleep(PIXELATION_SECONDS / (steps + 1))
                if game.done:
                    return
                await _clearer(bot, game)
            await asyncio.sleep(PIXELATION_SECONDS / (steps + 1))
        else:
            await asyncio.sleep(JUMBLE_SECONDS)
        await _finish(bot, game)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Game timer failed")
        _games.pop(game.chat_id, None)


async def _clearer(bot: Bot, game: Game) -> None:
    if game.level >= len(PIXELATION_LEVELS) - 1:
        return
    game.level += 1
    image = await asyncio.to_thread(pixelate, game.cover, PIXELATION_LEVELS[game.level])
    try:
        await bot.edit_message_media(
            chat_id=game.chat_id, message_id=game.message_id,
            media=InputMediaPhoto(media=BufferedInputFile(image, "cover.jpg"),
                                  caption=_text(game, game.chat_id > 0)),
            reply_markup=_buttons(game))
    except TelegramBadRequest:
        pass


# --- starting games ---------------------------------------------------------------------------

async def _artist_hints(artist: str, plays: int, lastfm: LastFmClient, metadata: Metadata) -> list[str]:
    """Port of GameService.GetJumbleArtistHints: genres, country and your plays first, extras on request."""
    hints, extras = [], []
    genres = (await metadata.genres([artist], wait=4)).values.get(name_key(artist)) or []
    if genres:
        hints.append("Genres: " + ", ".join(esc(g) for g in genres[:3]))
    country = (await metadata.countries([artist], wait=2)).values.get(name_key(artist))
    if country:
        hints.append(f"From {flag(country[0])} {esc(country[1])}")
    hints.append(f"You've played them {plural(plays, 'time')}")
    try:
        info = await lastfm.artist_info(artist)
        listeners = int((info.get("stats") or {}).get("listeners") or 0)
        similar = [s["name"] for s in as_list((info.get("similar") or {}).get("artist")) if s.get("name")]
        if similar:
            extras.append("Similar to " + ", ".join(esc(s) for s in similar[:2]))
        if listeners:
            extras.append(f"{plural(listeners, 'listener')} on Last.fm")
    except LastFmError:
        pass
    extras.append(f"Starts with <code>{esc(first_letter_hint(artist))}</code>")
    return hints + extras


@router.message(Command("jumble", "j"))
async def jumble(message: Message, lastfm: LastFmClient, db: Database, metadata: Metadata, bot: Bot) -> None:
    _ensure_free(message.chat.id)
    me = await self_user(message, db)
    top = await lastfm.top(me.lastfm_username, "artists", "overall", limit=CANDIDATES)
    chosen = pick([(i.name, i.playcount) for i in top.items], set(_recent.get(message.from_user.id, ())))
    if chosen is None:
        raise UserFacingError("You need some more scrobbled artists to play jumble.")
    _ensure_free(message.chat.id)  # someone may have started one while we looked things up
    artist, plays = chosen
    hints = await _artist_hints(artist, plays, lastfm, metadata)
    shown = min(3, len(hints) - 1)
    game = Game(next(_ids), "jumble", message.chat.id, artist, artist, hints, shown, scrambled=scramble(artist))
    _ensure_free(message.chat.id)
    _games[message.chat.id] = game
    _remember(message.from_user.id, artist)
    sent = await message.answer(_text(game, message.chat.type == "private"), reply_markup=_buttons(game))
    game.message_id = sent.message_id
    game.started = time.monotonic()
    game.timer = asyncio.create_task(_run_timer(bot, game))


@router.message(Command("pixelation", "pixel", "px"))
async def pixelation(message: Message, lastfm: LastFmClient, db: Database, metadata: Metadata, bot: Bot) -> None:
    _ensure_free(message.chat.id)
    me = await self_user(message, db)
    top = await lastfm.top(me.lastfm_username, "albums", "overall", limit=CANDIDATES)
    covers = {(i.name, i.playcount): i for i in top.items if i.image and not moderation.is_censored(i.artist, i.name)}
    recent = set(_recent.get(message.from_user.id, ()))
    album = None
    for _ in range(3):  # a cover can fail to download; try another album
        chosen = pick(list(covers), recent)
        if chosen is None:
            break
        item = covers.pop(chosen)
        try:
            response = await lastfm.http.get(full_size_image(item.image), timeout=15)
            response.raise_for_status()
            album, image = item, response.content
            break
        except Exception:
            continue
    if album is None:
        raise UserFacingError("You need some more scrobbled albums with covers to play pixelation.")

    released = await metadata.release_date(album.artist or "", album.name, wait=2)
    hints = [f"You've played it {plural(album.playcount, 'time')}",
             *([f"Released in {released[:4]}"] if released else []),
             f"By an artist starting with <code>{esc(first_letter_hint(album.artist or '?'))}</code>",
             f"The album starts with <code>{esc(first_letter_hint(album.name))}</code>",
             f"By <b>{esc(album.artist or '?')}</b>"]
    _ensure_free(message.chat.id)
    game = Game(next(_ids), "pixelation", message.chat.id, album.name, album.artist or "", hints, 1, cover=image)
    _games[message.chat.id] = game
    _remember(message.from_user.id, album.name)
    first = await asyncio.to_thread(pixelate, image, PIXELATION_LEVELS[0])
    sent = await message.answer_photo(BufferedInputFile(first, "cover.jpg"),
                                      caption=_text(game, message.chat.type == "private"), reply_markup=_buttons(game))
    game.message_id = sent.message_id
    game.started = time.monotonic()
    game.timer = asyncio.create_task(_run_timer(bot, game))


# --- playing --------------------------------------------------------------------------------

@router.callback_query(GameButton.filter())
async def game_button(query: CallbackQuery, callback_data: GameButton, bot: Bot) -> None:
    game = next((g for g in _games.values() if g.id == callback_data.game), None)
    if game is None:
        await query.answer("This game has ended.")
        return
    private = game.chat_id > 0
    if callback_data.action == "g":
        await query.answer()
        await _finish(bot, game, winner="")
        return
    if callback_data.action == "h":
        if game.shown >= len(game.hints):
            await query.answer("No more hints.")
            return
        game.shown += 1
    elif game.kind == "jumble":
        game.scrambled = scramble(game.answer)
    else:
        await query.answer()
        await _clearer(bot, game)
        return
    await query.answer()
    try:
        if game.kind == "jumble":
            await query.message.edit_text(_text(game, private), reply_markup=_buttons(game))
        else:
            await query.message.edit_caption(caption=_text(game, private), reply_markup=_buttons(game))
    except TelegramBadRequest:
        pass


class GuessFilter(Filter):
    """A plain text message in a chat with a running game."""

    async def __call__(self, message: Message) -> bool:
        return (message.chat.id in _games and bool(message.text) and not message.text.startswith("/")
                and message.from_user is not None and not message.from_user.is_bot)


@router.message(GuessFilter(), F.text)
async def guess(message: Message, bot: Bot) -> None:
    game = _games.get(message.chat.id)
    if game is None or game.done:
        return
    if answer_is_right(game.answer, message.text):
        await _finish(bot, game, winner=message.from_user.full_name, guess_message=message)
        return
    # Only react to clear guesses, so ordinary chat in groups where we see everything stays untouched
    replied = message.reply_to_message and message.reply_to_message.message_id == game.message_id
    if message.chat.type == "private" or replied:
        try:
            await bot.set_message_reaction(message.chat.id, message.message_id, [ReactionTypeEmoji(emoji="👎")])
        except TelegramBadRequest:
            pass
