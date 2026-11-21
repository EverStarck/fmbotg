"""/recap with switchable views and /receipt (ports of RecapBuilders and TrackBuilders.ReceiptAsync)."""

import asyncio
from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..charts import render_receipt
from ..context import STALE_BUTTON, Target, UserFacingError, resolve_target
from ..db import Database
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, link, num, plural, truncate, user_link
from ..lastfm import LastFmClient, LastFmError
from ..periods import ALL_TIME, YEARLY, Period, parse_period, period_from_key
from ..stats import Stats
from .chart import album_chart

router = Router(name="recap")

# RecapPage, minus genres/countries (phase 4) and bot stats (no command log yet)
VIEWS = {"o": "Overview", "a": "Artists", "b": "Albums", "t": "Tracks", "d": "Discoveries", "c": "Album chart"}
KIND_BY_VIEW = {"a": "artists", "b": "albums", "t": "tracks"}


class RecapView(CallbackData, prefix="rc"):
    view: str
    period: str
    user: str


async def _scrobbles(lastfm: LastFmClient, target: Target, period: Period) -> int:
    if period == ALL_TIME:
        return int((await lastfm.user_info(target.lastfm_username)).get("playcount") or 0)
    from_ts, to_ts = period.bounds(tz=target.tz)
    return await lastfm.scrobble_count(target.lastfm_username, from_ts, to_ts, target.session_key)


async def _new_artists(db: Database, target: Target, period: Period) -> list | None:
    if not target.indexed or target.user_id is None or period == ALL_TIME:
        return None
    from_ts, to_ts = period.bounds(tz=target.tz)
    return await db.discoveries(target.user_id, from_ts, to_ts, limit=100_000)


async def render_view(view: str, target: Target, period: Period, lastfm: LastFmClient, db: Database,
                      stats: Stats) -> str:
    user, label = user_link(target.lastfm_username), esc(period.label)
    header = f"<b>Recap for {user}</b> · {label} · {VIEWS[view]}"

    if view == "o":
        scrobbles, artists, albums, tracks, new = await asyncio.gather(
            _scrobbles(lastfm, target, period),
            stats.top(target, "artists", period, page=1, page_size=3),
            stats.top(target, "albums", period, page=1, page_size=3),
            stats.top(target, "tracks", period, page=1, page_size=3),
            _new_artists(db, target, period),
        )
        lines = [header, "", f"Scrobbles: <b>{num(scrobbles)}</b>"]
        if period != ALL_TIME:
            from_ts, to_ts = period.bounds(tz=target.tz)
            lines[-1] += f" ({scrobbles / max(1, (to_ts - from_ts) / 86400):,.1f} per day)"
        lines.append(f"Artists: <b>{num(artists.total)}</b> · albums: <b>{num(albums.total)}</b> · "
                     f"tracks: <b>{num(tracks.total)}</b>")
        if new is not None:
            lines.append(f"New artists: <b>{num(len(new))}</b>")
        for title, result in (("🎤 Top artists", artists), ("💿 Top albums", albums), ("🎶 Top tracks", tracks)):
            if result.items:
                lines += ["", f"<b>{title}</b>"]
                for i, item in enumerate(result.items, 1):
                    by = f" — {esc(item.artist)}" if item.artist else ""
                    lines.append(f"{i}. {link(item.name, item.url)}{by} · {num(item.playcount)}")
        return "\n".join(lines)

    if view in KIND_BY_VIEW:
        result = await stats.top(target, KIND_BY_VIEW[view], period, page=1, page_size=15)
        lines = [header, ""]
        for i, item in enumerate(result.items, 1):
            by = f" — {esc(item.artist)}" if item.artist else ""
            lines.append(f"{i}. {link(item.name, item.url)}{by} · <b>{num(item.playcount)}</b>")
        return "\n".join(lines) if result.items else f"{header}\n\nNothing here for this period."

    if view == "d":
        new = await _new_artists(db, target, period)
        if new is None:
            return (f"{header}\n\nDiscoveries need an imported history and a period other than all-time. "
                    "Run /update to import it.")
        lines = [header, "", f"<b>{plural(len(new), 'new artist')}</b>", ""]
        for i, (artist, first, plays) in enumerate(new[:15], 1):
            day = datetime.fromtimestamp(first, target.tz)
            lines.append(f"{i}. {esc(artist)} · {plural(plays, 'play')} · <i>since {day:%d %b}</i>")
        return "\n".join(lines)
    raise ValueError(view)


def _keyboard(current: str, period: Period, user: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("• " if key == current else "") + title,
                             callback_data=RecapView(view=key, period=period.key, user=user).pack())
        for key, title in VIEWS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:3], buttons[3:]])


async def _target_for(db: Database, lastfm_username: str) -> Target:
    user = await db.get_user_by_lastfm(lastfm_username)
    return Target.from_user(user, False) if user else Target(lastfm_username, None, False)


@router.message(Command("recap", "rc"))
async def recap(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, stats: Stats) -> None:
    period, args = parse_period((command.args or "").split(), default=YEARLY)
    target, _ = await resolve_target(message, args, db)
    text = await render_view("o", target, period, lastfm, db, stats)
    await message.reply(truncate(text, TELEGRAM_TEXT_LIMIT),
                        reply_markup=_keyboard("o", period, target.lastfm_username))


@router.callback_query(RecapView.filter(F.view.in_(VIEWS)))
async def recap_view(query: CallbackQuery, callback_data: RecapView, lastfm: LastFmClient, db: Database,
                     stats: Stats) -> None:
    if not isinstance(query.message, Message):  # older than 48 hours, or deleted
        await query.answer(STALE_BUTTON, show_alert=True)
        return
    period = period_from_key(callback_data.period)
    if period is None:
        await query.answer()
        return
    target = await _target_for(db, callback_data.user)

    if callback_data.view == "c":
        # A photo can't replace a text message in place; send it as a reply instead.
        await query.answer("Making the chart…")
        await query.bot.send_chat_action(query.message.chat.id, ChatAction.UPLOAD_PHOTO)
        image = await album_chart(stats, lastfm, target, period, 3, 3)
        await query.message.reply_photo(
            BufferedInputFile(image, filename="recap.jpg"),
            caption=f"Album chart for {user_link(target.lastfm_username)} · {esc(period.label)}",
        )
        return

    text = await render_view(callback_data.view, target, period, lastfm, db, stats)
    try:
        await query.message.edit_text(truncate(text, TELEGRAM_TEXT_LIMIT),
                                      reply_markup=_keyboard(callback_data.view, period, target.lastfm_username))
    except TelegramBadRequest:
        pass  # same view tapped twice
    await query.answer()


@router.message(Command("receipt", "rcpt"))
async def receipt(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                  stats: Stats) -> None:
    """Top tracks as a store receipt (port of TrackBuilders.ReceiptAsync)."""
    period, args = parse_period((command.args or "").split())
    target, _ = await resolve_target(message, args, db)
    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    top = await stats.top(target, "tracks", period, page=1, page_size=10)
    if not top.items:
        raise UserFacingError(f"No tracks for {user_link(target.lastfm_username)} ({esc(period.label)}).")
    try:
        total = await _scrobbles(lastfm, target, period)
    except LastFmError:
        total = sum(item.playcount for item in top.items)
    image = await asyncio.to_thread(
        render_receipt, target.lastfm_username, period.label, f"{datetime.now(target.tz):%d %b %Y}",
        [(item.name, item.artist or "", item.playcount) for item in top.items], total,
    )
    await message.reply_photo(BufferedInputFile(image, filename="receipt.png"),
                              caption=f"Receipt for {user_link(target.lastfm_username)} · {esc(period.label)}")
