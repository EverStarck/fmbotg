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
from ..lastfm import LastFmClient, LastFmError
from ..templates import EXAMPLE_TEMPLATE, MAX_TEMPLATE_LENGTH, VARIABLES, unknown_variables

router = Router(name="settings")
FM_MODES = ("full", "compact", "template")

SETTINGS_HELP = (
    "<b>Settings</b>\n"
    "<code>/settings timezone Europe/Amsterdam</code> — time zone for today, yesterday, years and overviews\n"
    "<code>/settings fm compact</code> — /fm style: <code>full</code>, <code>compact</code> or <code>template</code>\n"
    "<code>/settings template</code> + text on the next lines — your own /fm layout "
    "(<code>/settings template</code> alone shows the variables)"
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
            + (f"\n\nYour template:\n<pre>{esc(target.fm_template)}</pre>" if target.fm_template else "")
        )
        return

    setting, value = args[0].lower(), " ".join(args[1:]).strip()
    if setting in ("timezone", "tz"):
        await _set_timezone(message, db, value)
    elif setting in ("fm", "fmmode", "mode"):
        if value.lower() not in FM_MODES:
            raise UserFacingError("Choose <code>full</code>, <code>compact</code> or <code>template</code>.")
        if value.lower() == "template" and not target.fm_template:
            raise UserFacingError("Set a template first with <code>/settings template</code>.")
        await db.update_settings(message.from_user.id, fm_mode=value.lower())
        await message.reply(f"/fm style set to <b>{value.lower()}</b>.")
    elif setting in ("template", "fmtemplate"):
        # Keep line breaks: everything after the first word, unsplit
        raw = (command.args or "").split(None, 1)[1] if len(args) > 1 else ""
        await _set_template(message, db, raw.strip())
    else:
        await message.reply(SETTINGS_HELP)


async def _set_template(message: Message, db: Database, text: str) -> None:
    if not text:
        names = " ".join(f"<code>{{{{{name}}}}}</code>" for name in sorted(VARIABLES))
        await message.reply(
            "Write your /fm layout after <code>/settings template</code>, using variables in double braces. "
            "Lines starting with <code>$$title:</code> are bold and <code>$$footer:</code> italic. Example:\n"
            f"<pre>/settings template\n{esc(EXAMPLE_TEMPLATE)}</pre>\n"
            f"Variables: {names}\n\n<code>/settings template off</code> goes back to the normal /fm."
        )
        return
    if text.lower() in ("off", "reset", "remove"):
        await db.update_settings(message.from_user.id, fm_template=None, fm_mode="full")
        await message.reply("Template removed; /fm is back to the full style.")
        return
    if len(text) > MAX_TEMPLATE_LENGTH:
        raise UserFacingError(f"Templates can be at most {MAX_TEMPLATE_LENGTH} characters.")
    unknown = unknown_variables(text)
    await db.update_settings(message.from_user.id, fm_template=text, fm_mode="template")
    reply = "Template saved and /fm switched to it. Try /fm."
    if unknown:
        reply += "\nThese variables aren't supported here and will be empty: " + ", ".join(
            f"<code>{esc(u)}</code>" for u in unknown)
    await message.reply(reply)


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


@router.message(Command("scrobble", "sb"))
async def scrobble(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    """Scrobble a track now (port of TrackBuilders.ScrobbleAsync). Format: Artist - Track."""
    target = await _self(message, db)
    query = (command.args or "").strip()
    if not query:
        raise UserFacingError("Add a track, e.g. <code>/scrobble Radiohead - Idioteque</code>.")
    artist, track = await resolve_subject("track", query, target, lastfm)
    try:
        info = await lastfm.track_info(artist, track)
        artist = (info.get("artist") or {}).get("name", artist)
        track = info.get("name", track)
        album = (info.get("album") or {}).get("title")
    except LastFmError:
        album = None  # unknown to Last.fm; scrobble it as typed
    if not await lastfm.scrobble(artist, track, target.session_key, album=album):
        raise UserFacingError("Last.fm ignored that scrobble.")
    await message.reply(f"Scrobbled <b>{esc(track)}</b> by <b>{esc(artist)}</b>"
                        + (f" from <i>{esc(album)}</i>" if album else "") + ".")
