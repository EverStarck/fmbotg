"""/topartists, /topalbums, /toptracks with inline pagination (port of TopSlashCommands)."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..context import UserFacingError, resolve_target
from ..db import Database
from ..formatting import esc, link, num, user_link
from ..lastfm import LastFmClient, TopKind
from ..periods import BY_KEY, Period, UnsupportedPeriod, parse_period

router = Router(name="top")
PAGE_SIZE = 10

KINDS: dict[str, TopKind] = {"a": "artists", "b": "albums", "t": "tracks"}
KIND_KEYS = {kind: key for key, kind in KINDS.items()}


class TopPage(CallbackData, prefix="top"):
    kind: str
    period: str
    page: int
    user: str  # Last.fm usernames are at most 15 chars, which keeps us under Telegram's 64 bytes


async def render_top(lastfm: LastFmClient, user: str, kind: TopKind, period: Period, page: int):
    result = await lastfm.top(user, kind, period.api, limit=PAGE_SIZE, page=page)
    if not result.items:
        raise UserFacingError(f"No {period.label} top {kind} for {user_link(user)}.")

    lines = [f"<b>Top {period.label} {kind} for {user_link(user)}</b>", ""]
    start = (page - 1) * PAGE_SIZE
    for offset, item in enumerate(result.items, start=1):
        name = link(item.name, item.url)
        if item.artist:
            name = f"{name} — {esc(item.artist)}"
        lines.append(f"{start + offset}. {name} · <b>{num(item.playcount)}</b>")
    lines += ["", f"<code>Page {page}/{result.total_pages} · {num(result.total)} different {kind}</code>"]

    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(
            text="◀️", callback_data=TopPage(kind=KIND_KEYS[kind], period=period.key, page=page - 1, user=user).pack()))
    if page < result.total_pages:
        buttons.append(InlineKeyboardButton(
            text="▶️", callback_data=TopPage(kind=KIND_KEYS[kind], period=period.key, page=page + 1, user=user).pack()))
    markup = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
    return "\n".join(lines), markup


async def _top_command(message: Message, command: CommandObject, kind: TopKind, lastfm: LastFmClient,
                       db: Database) -> None:
    try:
        period, args = parse_period((command.args or "").split())
    except UnsupportedPeriod as exc:
        raise UserFacingError(esc(str(exc))) from exc
    target, _ = await resolve_target(message, args, db)
    text, markup = await render_top(lastfm, target.lastfm_username, kind, period, page=1)
    await message.reply(text, reply_markup=markup)


@router.message(Command("topartists", "ta", "artists"))
async def top_artists(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    await _top_command(message, command, "artists", lastfm, db)


@router.message(Command("topalbums", "tab", "albums"))
async def top_albums(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    await _top_command(message, command, "albums", lastfm, db)


@router.message(Command("toptracks", "tt", "tracks"))
async def top_tracks(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    await _top_command(message, command, "tracks", lastfm, db)


@router.callback_query(TopPage.filter(F.kind.in_(KINDS) & F.period.in_(BY_KEY)))
async def top_page(query: CallbackQuery, callback_data: TopPage, lastfm: LastFmClient) -> None:
    text, markup = await render_top(
        lastfm, callback_data.user, KINDS[callback_data.kind], BY_KEY[callback_data.period], callback_data.page
    )
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified" on double taps
    await query.answer()
