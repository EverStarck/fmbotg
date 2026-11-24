"""/jumble and /pixelation (port of GameBuilders and the game interactions).

One game per chat. Groups with Telegram's privacy mode on (the default) only send the bot replies
to its own messages, so players answer by replying to the game message; in private chats and
groups where the bot is an admin, any message counts as a guess.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from PIL import UnidentifiedImageError

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message,
    ReactionTypeEmoji,
)

from ..context import self_user
from ..domain import UserFacingError
from ..db import Database
from ..names import name_key
from ..formatting import esc, plural
from ..games import (
    JUMBLE_SECONDS, PIXELATION_LEVELS, PIXELATION_SECONDS, GameSessions, answer_is_right, first_letter_hint, pick,
    pixelate, scramble,
)
from ..lastfm import LastFmClient, LastFmError, as_list, full_size_image
from ..moderation import Moderation
from ..metadata import Metadata, flag

log = logging.getLogger(__name__)
router = Router(name="games")

CANDIDATES = 250  # top artists/albums a game picks from


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

    @property
    def subject(self) -> str:
        return "artist" if self.kind == "jumble" else "album"

    @property
    def seconds(self) -> int:
        return JUMBLE_SECONDS if self.kind == "jumble" else PIXELATION_SECONDS

    @property
    def private(self) -> bool:
        return self.chat_id > 0


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


async def _finish(bot: Bot, sessions: GameSessions, game: Game, winner: str | None = None,
                  guess_message: Message | None = None) -> None:
    if game.done:
        return
    game.done = True
    sessions.release(game)
    if game.timer and game.timer is not asyncio.current_task():
        game.timer.cancel()

    elapsed = time.monotonic() - game.started
    subject = game.subject
    answer = f"<b>{esc(game.answer)}</b>" + (f" by {esc(game.artist)}" if game.kind == "pixelation" else "")
    if winner:
        text = f"✅ {esc(winner)} got it in {elapsed:.1f}s! The {subject} was {answer}."
    elif winner is None and guess_message is None and elapsed >= game.seconds - 1:
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


async def _run_timer(bot: Bot, sessions: GameSessions, game: Game) -> None:
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
        await _finish(bot, sessions, game)
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Game timer failed")
        game.done = True
        sessions.release(game)


async def _clearer(bot: Bot, game: Game) -> None:
    if game.level >= len(PIXELATION_LEVELS) - 1:
        return
    game.level += 1
    image = await asyncio.to_thread(pixelate, game.cover, PIXELATION_LEVELS[game.level])
    if game.done:  # finished while we were drawing: don't cover up the revealed album
        return
    try:
        await bot.edit_message_media(
            chat_id=game.chat_id, message_id=game.message_id,
            media=InputMediaPhoto(media=BufferedInputFile(image, "cover.jpg"),
                                  caption=_text(game, game.private)),
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


async def _start(bot: Bot, sessions: GameSessions, game: Game, send) -> None:
    """Register the game, send it and start its timer. If sending fails, the chat is freed again,
    so a failed start can't leave a game nobody can finish."""
    sessions.claim(game)
    try:
        sent = await send()
    except BaseException:
        game.done = True
        sessions.release(game)
        raise
    game.message_id = sent.message_id
    game.started = time.monotonic()
    game.timer = asyncio.create_task(_run_timer(bot, sessions, game))


@router.message(Command("jumble", "j"))
async def jumble(message: Message, lastfm: LastFmClient, db: Database, metadata: Metadata, bot: Bot,
                 game_sessions: GameSessions) -> None:
    game_sessions.ensure_free(message.chat.id)
    me = await self_user(message, db)
    top = await lastfm.top(me.lastfm_username, "artists", "overall", limit=CANDIDATES)
    chosen = pick([(i.name, i.playcount) for i in top.items], game_sessions.recent(message.from_user.id))
    if chosen is None:
        raise UserFacingError("You need some more scrobbled artists to play jumble.")
    artist, plays = chosen
    hints = await _artist_hints(artist, plays, lastfm, metadata)
    game = Game(game_sessions.new_id(), "jumble", message.chat.id, artist, artist, hints, min(3, len(hints) - 1),
                scrambled=scramble(artist))
    await _start(bot, game_sessions, game,
                 lambda: message.answer(_text(game, game.private), reply_markup=_buttons(game)))
    game_sessions.remember(message.from_user.id, artist)


async def _fetch_cover(lastfm: LastFmClient, item) -> tuple[bytes, bytes] | None:
    """(original cover, first pixelated image), or None if the cover can't be downloaded or read."""
    try:
        response = await lastfm.http.get(full_size_image(item.image), timeout=15)
        response.raise_for_status()
        return response.content, await asyncio.to_thread(pixelate, response.content, PIXELATION_LEVELS[0])
    except (httpx.HTTPError, UnidentifiedImageError, OSError):
        return None


@router.message(Command("pixelation", "pixel", "px"))
async def pixelation(message: Message, lastfm: LastFmClient, db: Database, metadata: Metadata, bot: Bot,
                     moderation: Moderation, game_sessions: GameSessions) -> None:
    game_sessions.ensure_free(message.chat.id)
    me = await self_user(message, db)
    top = await lastfm.top(me.lastfm_username, "albums", "overall", limit=CANDIDATES)
    covers = {(i.name, i.playcount): i for i in top.items if i.image and not moderation.is_censored(i.artist, i.name)}
    recent = game_sessions.recent(message.from_user.id)
    album = images = None
    for _ in range(3):  # a cover can fail to download or be broken; try another album
        chosen = pick(list(covers), recent)
        if chosen is None:
            break
        item = covers.pop(chosen)
        if images := await _fetch_cover(lastfm, item):
            album = item
            break
    if album is None:
        raise UserFacingError("You need some more scrobbled albums with covers to play pixelation.")

    released = await metadata.release_date(album.artist or "", album.name, wait=2)
    hints = [f"You've played it {plural(album.playcount, 'time')}",
             *([f"Released in {released[:4]}"] if released else []),
             f"By an artist starting with <code>{esc(first_letter_hint(album.artist or '?'))}</code>",
             f"The album starts with <code>{esc(first_letter_hint(album.name))}</code>",
             f"By <b>{esc(album.artist or '?')}</b>"]
    cover, first = images
    game = Game(game_sessions.new_id(), "pixelation", message.chat.id, album.name, album.artist or "", hints, 1,
                cover=cover)
    await _start(bot, game_sessions, game, lambda: message.answer_photo(
        BufferedInputFile(first, "cover.jpg"), caption=_text(game, game.private), reply_markup=_buttons(game)))
    game_sessions.remember(message.from_user.id, album.name)


# --- playing --------------------------------------------------------------------------------

@router.callback_query(GameButton.filter())
async def game_button(query: CallbackQuery, callback_data: GameButton, bot: Bot,
                      game_sessions: GameSessions) -> None:
    game = game_sessions.by_id(callback_data.game)
    if game is None:
        await query.answer("This game has ended.")
        return
    private = game.private
    if callback_data.action == "g":
        await query.answer()
        await _finish(bot, game_sessions, game, winner="")
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

    async def __call__(self, message: Message, game_sessions: GameSessions) -> bool:
        return (game_sessions.in_chat(message.chat.id) is not None and bool(message.text)
                and not message.text.startswith("/") and message.from_user is not None and not message.from_user.is_bot)


@router.message(GuessFilter(), F.text)
async def guess(message: Message, bot: Bot, game_sessions: GameSessions) -> None:
    game = game_sessions.in_chat(message.chat.id)
    if game is None or game.done:
        return
    if answer_is_right(game.answer, message.text):
        await _finish(bot, game_sessions, game, winner=message.from_user.full_name, guess_message=message)
        return
    # Only react to clear guesses, so ordinary chat in groups where we see everything stays untouched
    replied = message.reply_to_message and message.reply_to_message.message_id == game.message_id
    if message.chat.type == "private" or replied:
        try:
            await bot.set_message_reaction(message.chat.id, message.message_id, [ReactionTypeEmoji(emoji="👎")])
        except TelegramBadRequest:
            pass
