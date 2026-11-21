"""/friends, /friendsfm, /addfriend, /removefriend (port of FriendsService and FriendBuilders).

Your friends are the people you follow on Last.fm plus anyone you add here with /addfriend,
so most people don't have to set anything up.
"""

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import UserFacingError, apply_alias, self_user
from ..db import Database, User
from ..formatting import TELEGRAM_TEXT_LIMIT, ago, esc, link, truncate, user_link
from ..lastfm import LastFmClient, LastFmError

router = Router(name="friends")

MAX_ADDED = 20  # added with /addfriend
MAX_SHOWN = 25  # friends looked up per /friendsfm or friend who knows
FRIEND_LOOKUPS = 8  # concurrent Last.fm calls for /friendsfm


async def friend_names(me: User, db: Database, lastfm: LastFmClient) -> list[str]:
    """Added friends first, then Last.fm follows; no duplicates and never yourself."""
    added = await db.friends(me.id)
    try:
        following = await lastfm.following(me.lastfm_username, limit=MAX_SHOWN * 2)
    except LastFmError:
        following = []  # still show the friends added here
    seen, names = {me.lastfm_username.lower()}, []
    for name in added + following:
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names[:MAX_SHOWN]


async def _name_from_arg(message: Message, arg: str, db: Database) -> str:
    """A nickname, lfm:name, @handle or a plain Last.fm username -> Last.fm username."""
    arg = apply_alias(message.chat.id, [arg])[0]
    if arg.lower().startswith("lfm:"):
        arg = arg[4:]
    elif arg.startswith("@"):
        user = await db.get_user_by_handle(arg)
        if user is None or not user.lastfm_username:
            raise UserFacingError(f"{esc(arg)} hasn't connected Last.fm to this bot. "
                                  "Use <code>lfm:username</code> instead.")
        return user.lastfm_username
    if not arg:
        raise UserFacingError("That name is empty.")
    return arg


async def _names_from_message(message: Message, command: CommandObject, db: Database) -> list[str]:
    args = (command.args or "").split()
    if args:
        return [await _name_from_arg(message, arg, db) for arg in args]
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        user = await db.get_user(reply.from_user.id)
        if user is None or not user.lastfm_username:
            raise UserFacingError(f"{esc(reply.from_user.full_name)} hasn't connected Last.fm to this bot.")
        return [user.lastfm_username]
    return []


@router.message(Command("addfriend", "addfriends", "af"))
async def add_friend(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    me = await self_user(message, db)
    names = await _names_from_message(message, command, db)
    if not names:
        raise UserFacingError("Add a Last.fm username, an <code>@handle</code>, or reply to someone, e.g. "
                              "<code>/addfriend rj</code>.")
    current = await db.friends(me.id)
    added, skipped = [], []
    for name in names:
        try:
            name = (await lastfm.user_info(name))["name"]  # checks it exists and fixes the capitalisation
        except LastFmError as exc:
            if exc.code != LastFmError.NOT_FOUND:
                raise
            skipped.append(f"{esc(name)} (no such Last.fm user)")
            continue
        if name.lower() == me.lastfm_username.lower():
            skipped.append("yourself")
        elif len(current) + len(added) >= MAX_ADDED:
            skipped.append(f"{esc(name)} (you can add up to {MAX_ADDED})")
        elif await db.add_friend(me.id, name):
            added.append(user_link(name))
        else:
            skipped.append(f"{esc(name)} (already added)")

    lines = []
    if added:
        lines.append("Added " + ", ".join(added) + ". Try /friendsfm.")
    if skipped:
        lines.append("Skipped " + ", ".join(skipped) + ".")
    await message.reply("\n".join(lines))


@router.message(Command("removefriend", "removefriends", "rf", "unfriend"))
async def remove_friend(message: Message, command: CommandObject, db: Database) -> None:
    me = await self_user(message, db)
    names = await _names_from_message(message, command, db)
    if not names:
        raise UserFacingError("Which friend? e.g. <code>/removefriend rj</code>.")
    removed = [name for name in names if await db.remove_friend(me.id, name)]
    missing = [name for name in names if name not in removed]
    lines = []
    if removed:
        lines.append("Removed " + ", ".join(esc(n) for n in removed) + ".")
    if missing:
        lines.append("Not in your added friends: " + ", ".join(esc(n) for n in missing)
                     + ". People you follow on Last.fm can only be unfollowed there.")
    await message.reply("\n".join(lines))


@router.message(Command("friends", "friendlist"))
async def friends(message: Message, lastfm: LastFmClient, db: Database) -> None:
    me = await self_user(message, db)
    added = await db.friends(me.id)
    try:
        following = await lastfm.following(me.lastfm_username, limit=MAX_SHOWN * 2)
    except LastFmError:
        following = []
    lines = [f"<b>Friends of {user_link(me.lastfm_username)}</b>"]
    if following:
        lines += ["", "<b>Following on Last.fm</b>", ", ".join(user_link(n) for n in following)]
    if added:
        lines += ["", "<b>Added here</b>", ", ".join(user_link(n) for n in added)]
    if not following and not added:
        lines += ["", "Nobody yet. Follow people on Last.fm, or add them with <code>/addfriend username</code>."]
    else:
        lines += ["", "<i>/friendsfm shows what they're playing; /fwk who listens to an artist most.</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("friendsfm", "ffm", "friendsnp"))
async def friends_fm(message: Message, lastfm: LastFmClient, db: Database) -> None:
    me = await self_user(message, db)
    names = await friend_names(me, db, lastfm)
    if not names:
        raise UserFacingError("You have no friends here yet. Follow people on Last.fm, or add them with "
                              "<code>/addfriend username</code>.")

    limit = asyncio.Semaphore(FRIEND_LOOKUPS)

    async def latest(name: str):
        async with limit:
            try:
                result = await lastfm.recent_tracks(name, limit=1, extended=False)
            except LastFmError:
                return name, None  # private profile or deleted account
        return name, result.tracks[0] if result.tracks else None

    results = await asyncio.gather(*(latest(n) for n in names))
    playing = [(n, s) for n, s in results if s is not None]
    hidden = len(results) - len(playing)
    # Now playing first, then most recent
    playing.sort(key=lambda pair: (not pair[1].now_playing, -(pair[1].timestamp or 0)))

    lines = ["<b>What your friends are playing</b>", ""]
    for name, scrobble in playing:
        when = "▶️" if scrobble.now_playing else "•"
        tail = "" if scrobble.now_playing else f" <i>· {ago(scrobble.timestamp)}</i>"
        lines.append(f"{when} {link(scrobble.track, scrobble.url)} — {esc(scrobble.artist)} · "
                     f"{user_link(name)}{tail}")
    if hidden:
        lines += ["", f"<i>{hidden} with no scrobbles or a private profile.</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))
