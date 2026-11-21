"""Account commands: /start, /help, /login, /logout, /profile (port of UserSlashCommands)."""

import asyncio
import logging
import time
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.deep_linking import create_start_link

from ..context import NOT_REGISTERED_SELF, NotRegistered, resolve_target
from ..db import Database
from ..formatting import ago, esc, link, num, user_link
from ..indexer import Indexer
from ..lastfm import LastFmClient, LastFmError

log = logging.getLogger(__name__)
router = Router(name="user")

# Strong references so the polling tasks aren't garbage collected mid-login.
_login_tasks: set[asyncio.Task] = set()

HELP_TEXT = """<b>FMBot for Telegram</b> — Last.fm stats in your chats.

<b>Account</b>
/login — connect your Last.fm account (private chat)
/logout — disconnect it
/profile — your Last.fm profile stats
/update — fetch new scrobbles (<code>/update full</code> re-imports everything)
/settings — time zone, /fm style and your own /fm template
/privacy — <code>global</code> shows your name in global who knows, <code>chat</code> (default) hides it

<b>Listening</b>
/fm — what you're playing now
/recent — your latest scrobbles
/plays — scrobble count, optionally for a period
/streak — how many times in a row you've played the current artist, album and track (/streaks for saved ones)
/pace — when you'll reach a scrobble goal, e.g. <code>/pace 100k</code>
/milestone — your Nth scrobble, e.g. <code>/milestone 10000</code> or <code>random</code>
/overview — your last few days, day by day
/love /unlove /loved — love tracks on Last.fm
/scrobble — scrobble a track, e.g. <code>/scrobble Radiohead - Idioteque</code>
/year — your year in review, e.g. <code>/year 2023</code>
/discoveries — artists you discovered in a period
/taste — compare your taste with someone (reply or <code>@handle</code>)
/gaps — artists, albums or tracks you came back to after 90+ days
/search — search your listening history

<b>Top lists</b> (add a period: today, yesterday, 3d, weekly, monthly, quarterly, half, yearly, 2y, alltime or a year like 2019)
/topartists /topalbums /toptracks — add <code>bb</code> to compare with the previous period
/recap — a period at a glance, with buttons for top lists, discoveries and a chart
/receipt — your top tracks as a store receipt

<b>Info</b> (defaults to what you're playing)
/artist /album /track — e.g. <code>/album Radiohead - OK Computer</code>
/artistplays /albumplays /trackplays — your plays, optionally for a period
/artisttracks /artistalbums — your top tracks and albums by an artist
/artistpace — when you'll reach a play goal on an artist
/artistoverview — you and one artist: plays, top tracks and albums, plays per year
/albumtracks /cover — an album's track list with your plays, and its cover
/discovery /albumdiscovery /trackdiscovery — when you first and last listened to something

<b>Groups</b>
/whoknows /whoknowsalbum /whoknowstrack — who listens to it the most here
/affinity — who here has the most similar taste to you
/crowns — your crowns here: the artists you have the most plays of (/crown for one artist's history, /crownleaderboard for everyone)
Admins: /crownthreshold, /togglecrowns, /crownseeder (hand out crowns from imported histories), /killcrown

<b>Friends</b> (people you follow on Last.fm, plus any you add)
/friendsfm — what your friends are playing
/friendwhoknows /fwka /fwkt — who of your friends listens to it the most
/friends /addfriend /removefriend — see and change your friends

<b>Global</b>
/globalwhoknows /gwka /gwkt — top listeners among everyone using this bot

<b>Charts</b>
/chart — album collage, e.g. <code>/chart 4x4 monthly</code>, add <code>notitles</code> to hide titles or <code>skip</code> to leave out albums without a cover

<b>Other users</b>: reply to their message, or add <code>@handle</code> or <code>lfm:username</code>."""


@router.message(CommandStart(deep_link=True, magic=F.args == "login"))
async def start_login(message: Message, bot: Bot, lastfm: LastFmClient, db: Database, indexer: Indexer) -> None:
    await login(message, bot, lastfm, db, indexer)


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("login"))
async def login(message: Message, bot: Bot, lastfm: LastFmClient, db: Database, indexer: Indexer) -> None:
    if message.chat.type != "private":
        url = await create_start_link(bot, "login")
        await message.reply(
            "For your privacy, logging in happens in a private chat.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Log in privately", url=url)]]
            ),
        )
        return

    token = await lastfm.get_token()
    prompt = await message.answer(
        "Tap the button below and allow access on Last.fm. "
        "I'll detect it automatically. The link is valid for about 5 minutes.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Connect Last.fm", url=lastfm.auth_url(token))]]
        ),
    )
    task = asyncio.create_task(
        _poll_login(bot, prompt.chat.id, prompt.message_id, message.from_user.id, token, lastfm, db, indexer)
    )
    _login_tasks.add(task)
    task.add_done_callback(_login_tasks.discard)


def _login_delay(attempt: int) -> float:
    # Like UserService.GetLoginAttemptDelay: fast at first, slower later.
    return 3 if attempt < 10 else 6


async def _poll_login(bot: Bot, chat_id: int, message_id: int, telegram_id: int, token: str,
                      lastfm: LastFmClient, db: Database, indexer: Indexer) -> None:
    async def update_prompt(text: str) -> None:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
        except TelegramAPIError:
            await bot.send_message(chat_id, text)  # prompt was deleted or is too old to edit

    deadline = time.monotonic() + 5 * 60
    attempt = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(_login_delay(attempt))
        attempt += 1
        try:
            username, session_key = await lastfm.get_session(token)
        except LastFmError as exc:
            if exc.code == LastFmError.TOKEN_NOT_AUTHORIZED or exc.is_transient:
                continue
            log.info("Login for %s failed: %s", telegram_id, exc)
            break
        except Exception:
            log.exception("Unexpected error while polling login for %s", telegram_id)
            break

        user = await db.link_lastfm(telegram_id, username, session_key)
        text = f"✅ Connected to Last.fm as {user_link(username)}.\nTry /fm to see what you're playing."
        if user.indexed_at is None and indexer.queue_full_import(user.id, notify_chat=chat_id):
            text += "\n\nI'm importing your scrobble history in the background and will message you when it's done."
        await update_prompt(text)
        return

    await update_prompt("Login timed out or was cancelled. Send /login to try again.")


@router.message(Command("logout"))
async def logout(message: Message, db: Database) -> None:
    removed = await db.unlink_lastfm(message.from_user.id)
    await message.reply(
        "Your Last.fm account has been disconnected." if removed else "You don't have a Last.fm account connected."
    )


@router.message(Command("profile", "stats", "me"))
async def profile(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                  indexer: Indexer) -> None:
    target, _ = await resolve_target(message, (command.args or "").split(), db)
    info = await lastfm.user_info(target.lastfm_username)

    registered = int((info.get("registered") or {}).get("unixtime") or 0)
    playcount = int(info.get("playcount") or 0)
    lines = [f"<b>Last.fm profile for {user_link(info.get('name', target.lastfm_username))}</b>"]
    if info.get("realname"):
        lines.append(esc(info["realname"]))
    lines.append("")
    lines.append(f"Scrobbles: <b>{num(playcount)}</b>")
    for label, key in (("Artists", "artist_count"), ("Albums", "album_count"), ("Tracks", "track_count")):
        if info.get(key):
            lines.append(f"{label}: <b>{num(int(info[key]))}</b>")
    if registered:
        since = datetime.fromtimestamp(registered, tz=timezone.utc)
        days = max(1, (datetime.now(timezone.utc) - since).days)
        lines.append(f"Scrobbling since: <b>{since:%d %B %Y}</b>")
        lines.append(f"Average per day: <b>{playcount / days:,.1f}</b>")
    if info.get("country") and info["country"] != "None":
        lines.append(f"Country: {esc(info['country'])}")
    if str(info.get("subscriber")) == "1":
        lines.append("Last.fm Pro subscriber ⭐")
    if target.user_id is not None:
        lines.append(_index_status(await db.get_user_by_id(target.user_id), indexer))
    lines.append("")
    lines.append(link("View on Last.fm", info.get("url", "")))
    await message.reply("\n".join(lines))


def _index_status(user, indexer: Indexer) -> str:
    if indexer.is_importing(user.id):
        return "History: <i>importing…</i>"
    if user.indexed_at is None:
        return "History: not imported (/update)"
    updated = ago(int(user.updated_at.timestamp())) if user.updated_at else "never"
    return f"History: imported · updated {updated}"


@router.message(Command("update", "index"))
async def update(message: Message, command: CommandObject, db: Database, indexer: Indexer) -> None:
    """Port of IndexSlashCommands.update: fetch new scrobbles, or `/update full` to re-import."""
    user = await db.get_user(message.from_user.id)
    if user is None or not user.lastfm_username:
        raise NotRegistered(NOT_REGISTERED_SELF)

    full = (command.args or "").strip().lower() in ("full", "force", "f")
    if indexer.is_importing(user.id):
        await message.reply("Your scrobble history is already being imported. I'll let you know when it's done.")
        return
    if full or user.indexed_at is None:
        # Notify in private only, so group chats don't get a delayed message out of nowhere.
        notify = message.chat.id if message.chat.type == "private" else None
        indexer.queue_full_import(user.id, notify_chat=notify)
        await message.reply("Importing your full scrobble history. This can take a few minutes for big profiles."
                            + ("" if notify else " Check /profile for progress."))
        return

    added = await indexer.update(user.id)
    await message.reply(f"Added {num(added)} new scrobbles." if added else "You're already up to date.")
