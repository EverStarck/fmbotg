"""/settings, /timezone, /love, /unlove, /loved (port of UserSlashCommands settings and the love
commands in TrackSlashCommands)."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import NOT_REGISTERED_SELF, NotRegistered, Target, UserFacingError, resolve_subject, resolve_target
from ..db import Database
from ..formatting import TELEGRAM_TEXT_LIMIT, ago, esc, link, plural, truncate, user_link
from ..lastfm import LastFmClient

router = Router(name="settings")
FM_MODES = ("full", "compact")

SETTINGS_HELP = (
    "<b>Settings</b>\n"
    "<code>/settings timezone Europe/Amsterdam</code> — time zone for today, yesterday, years and overviews\n"
    "<code>/settings fm compact</code> — /fm style: <code>full</code> or <code>compact</code>"
)


def find_timezone(name: str) -> str | None:
    """Case-insensitive IANA lookup ('europe/amsterdam' -> 'Europe/Amsterdam'), returning the canonical name.

    Checked against the zone list first: on case-insensitive file systems ZoneInfo() would accept
    the lowercase spelling, which then breaks after moving to Linux.
    """
    lowered = name.lower()
    match = next((tz for tz in available_timezones() if tz.lower() == lowered), None)
    if match:
        return match
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        return None


async def _self(message: Message, db: Database) -> Target:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.lastfm_username:
        raise NotRegistered(NOT_REGISTERED_SELF)
    return Target.from_user(user, True)


@router.message(Command("settings", "set"))
async def settings(message: Message, command: CommandObject, db: Database) -> None:
    args = (command.args or "").split()
    target = await _self(message, db)
    if not args:
        now = datetime.now(target.tz)
        await message.reply(
            f"{SETTINGS_HELP}\n\nTime zone: <b>{esc(str(target.tz))}</b> (now {now:%H:%M})\n"
            f"/fm style: <b>{target.fm_mode}</b>"
        )
        return

    setting, value = args[0].lower(), " ".join(args[1:]).strip()
    if setting in ("timezone", "tz"):
        await _set_timezone(message, db, value)
    elif setting in ("fm", "fmmode", "mode"):
        if value.lower() not in FM_MODES:
            raise UserFacingError("Choose <code>full</code> or <code>compact</code>.")
        await db.update_settings(message.from_user.id, fm_mode=value.lower())
        await message.reply(f"/fm style set to <b>{value.lower()}</b>.")
    else:
        await message.reply(SETTINGS_HELP)


async def _set_timezone(message: Message, db: Database, value: str) -> None:
    if not value:
        raise UserFacingError("Add a time zone, e.g. <code>/timezone Europe/Amsterdam</code>. "
                              "Find yours at https://en.wikipedia.org/wiki/List_of_tz_database_time_zones")
    name = find_timezone(value)
    if name is None:
        raise UserFacingError(f"Unknown time zone <code>{esc(value)}</code>. Use a name like "
                              "<code>America/New_York</code>.")
    await db.update_settings(message.from_user.id, timezone=name)
    now = datetime.now(ZoneInfo(name))
    await message.reply(f"Time zone set to <b>{esc(name)}</b> (it's {now:%H:%M} there).")


@router.message(Command("timezone", "tz"))
async def timezone_cmd(message: Message, command: CommandObject, db: Database) -> None:
    await _self(message, db)
    await _set_timezone(message, db, (command.args or "").strip())


async def _love(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, loved: bool) -> None:
    target = await _self(message, db)
    artist, track = await resolve_subject("track", command.args or "", target, lastfm)
    await lastfm.love(artist, track, target.session_key, loved=loved)
    verb = "❤️ Loved" if loved else "💔 Unloved"
    await message.reply(f"{verb} <b>{esc(track)}</b> by <b>{esc(artist)}</b>.")


@router.message(Command("love", "l"))
async def love(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    await _love(message, command, lastfm, db, loved=True)


@router.message(Command("unlove", "ul"))
async def unlove(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    await _love(message, command, lastfm, db, loved=False)


@router.message(Command("loved", "lovedtracks"))
async def loved_tracks(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, _ = await resolve_target(message, (command.args or "").split(), db)
    result = await lastfm.loved_tracks(target.lastfm_username, limit=15)
    if not result.tracks:
        raise UserFacingError(f"{user_link(target.lastfm_username)} hasn't loved any tracks yet.")
    lines = [f"<b>Loved tracks for {user_link(target.lastfm_username)}</b>", ""]
    for track in result.tracks:
        lines.append(f"❤️ {link(track.track, track.url)} — {esc(track.artist)}"
                     + (f" <i>· {ago(track.timestamp)}</i>" if track.timestamp else ""))
    lines += ["", f"<code>{plural(result.total, 'loved track')}</code>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))
