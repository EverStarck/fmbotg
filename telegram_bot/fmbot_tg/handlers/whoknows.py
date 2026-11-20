"""/whoknows for artists, albums and tracks within a Telegram group (port of WhoKnows*Commands).

Members with an imported history are counted from the play index; the rest fall back to a
live Last.fm lookup per member (cached for 5 minutes, concurrency-limited).
"""

import html

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..context import UserFacingError, resolve_subject, resolve_target
from ..db import Database
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, lastfm_user_url, num, plural, truncate
from ..lastfm import LastFmClient
from ..stats import Stats

router = Router(name="whoknows")
SHOWN = 15


async def _whoknows(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient, db: Database,
                    stats: Stats, settings: Settings) -> None:
    if message.chat.type == "private":
        raise UserFacingError("Who knows only works in groups. Add me to a group chat to compare listening.")

    query = " ".join((command.args or "").split())
    if query:
        # A search was given; the caller doesn't need to be registered.
        subject = await resolve_subject(kind, query, None, lastfm)
    else:
        target, _ = await resolve_target(message, [], db)
        subject = await resolve_subject(kind, "", target, lastfm)
    subject = await stats.canonical_subject(kind, subject)

    members = await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
    if not members:
        raise UserFacingError("Nobody in this chat has connected Last.fm yet. Use /login to get started.")

    results = await stats.whoknows_counts(kind, subject, members, message.from_user.id)
    listeners = sorted(((m, c) for m, c in results if c), key=lambda pair: pair[1], reverse=True)

    title = " - ".join(subject)
    if not listeners:
        raise UserFacingError(f"Nobody here has listened to <b>{esc(title)}</b> yet.")

    lines = [f"<b>Who knows {esc(title)}</b> in {esc(message.chat.title or 'this chat')}", ""]
    for position, (member, plays) in enumerate(listeners[:SHOWN], start=1):
        name = member.display_name or member.lastfm_username
        entry = f'<a href="{html.escape(lastfm_user_url(member.lastfm_username))}">{esc(name)}</a>'
        if member.telegram_id == message.from_user.id:
            entry = f"<b>{entry}</b>"
        marker = "👑" if position == 1 else f"{position}."
        lines.append(f"{marker} {entry} · <b>{plural(plays, 'play')}</b>")

    caller_rank = next((i for i, (m, _) in enumerate(listeners, 1) if m.telegram_id == message.from_user.id), None)
    if caller_rank and caller_rank > SHOWN:
        lines.append(f"…\n{caller_rank}. <b>you</b> · {plural(listeners[caller_rank - 1][1], 'play')}")

    total = sum(c for _, c in listeners)
    failed = sum(1 for _, c in results if c is None)
    footer = f"{plural(len(listeners), 'listener')} · {plural(total, 'play')} · {num(total // len(listeners))} avg"
    if failed:
        footer += f" · {failed} couldn't be fetched"
    lines += ["", f"<code>{footer}</code>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("whoknows", "wk", "w"))
async def whoknows_artist(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                          stats: Stats, settings: Settings) -> None:
    await _whoknows(message, command, "artist", lastfm, db, stats, settings)


@router.message(Command("whoknowsalbum", "wka", "wa"))
async def whoknows_album(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                         stats: Stats, settings: Settings) -> None:
    await _whoknows(message, command, "album", lastfm, db, stats, settings)


@router.message(Command("whoknowstrack", "wkt", "wt"))
async def whoknows_track(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                         stats: Stats, settings: Settings) -> None:
    await _whoknows(message, command, "track", lastfm, db, stats, settings)
