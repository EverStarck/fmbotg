"""/topartists, /topalbums, /toptracks with inline pagination (port of TopSlashCommands)."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..context import Target, UserFacingError, resolve_target
from ..db import Database
from ..formatting import esc, link, num, plural, user_link
from ..lastfm import TopKind
from ..periods import Period, parse_period, period_from_key
from ..stats import Stats

router = Router(name="top")
PAGE_SIZE = 10

KINDS: dict[str, TopKind] = {"a": "artists", "b": "albums", "t": "tracks"}
KIND_KEYS = {kind: key for key, kind in KINDS.items()}


class TopPage(CallbackData, prefix="top"):
    kind: str
    period: str
    page: int
    user: str  # Last.fm usernames are at most 15 chars, which keeps us under Telegram's 64 bytes


async def render_top(stats: Stats, target: Target, kind: TopKind, period: Period, page: int):
    user = target.lastfm_username
    result = await stats.top(target, kind, period, page=page, page_size=PAGE_SIZE)
    if not result.items:
        raise UserFacingError(f"No {kind} for {user_link(user)} in this period ({esc(period.label)}).")

    lines = [f"<b>Top {kind} for {user_link(user)}</b> · {esc(period.label)}", ""]
    start = (page - 1) * PAGE_SIZE
    for offset, item in enumerate(result.items, start=1):
        name = link(item.name, item.url)
        if item.artist:
            name = f"{name} — {esc(item.artist)}"
        lines.append(f"{start + offset}. {name} · <b>{num(item.playcount)}</b>")
    lines += ["", f"<code>Page {page}/{result.total_pages} · {plural(result.total, 'different ' + kind[:-1])}</code>"]

    def button(text: str, to_page: int) -> InlineKeyboardButton:
        data = TopPage(kind=KIND_KEYS[kind], period=period.key, page=to_page, user=user)
        return InlineKeyboardButton(text=text, callback_data=data.pack())

    buttons = []
    if page > 1:
        buttons.append(button("◀️", page - 1))
    if page < result.total_pages:
        buttons.append(button("▶️", page + 1))
    markup = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
    return "\n".join(lines), markup


async def _top_command(message: Message, command: CommandObject, kind: TopKind, stats: Stats,
                       db: Database) -> None:
    period, args = parse_period((command.args or "").split())
    target, _ = await resolve_target(message, args, db)
    text, markup = await render_top(stats, target, kind, period, page=1)
    await message.reply(text, reply_markup=markup)


@router.message(Command("topartists", "ta", "artists"))
async def top_artists(message: Message, command: CommandObject, stats: Stats, db: Database) -> None:
    await _top_command(message, command, "artists", stats, db)


@router.message(Command("topalbums", "tab", "albums"))
async def top_albums(message: Message, command: CommandObject, stats: Stats, db: Database) -> None:
    await _top_command(message, command, "albums", stats, db)


@router.message(Command("toptracks", "tt", "tracks"))
async def top_tracks(message: Message, command: CommandObject, stats: Stats, db: Database) -> None:
    await _top_command(message, command, "tracks", stats, db)


@router.callback_query(TopPage.filter(F.kind.in_(KINDS)))
async def top_page(query: CallbackQuery, callback_data: TopPage, stats: Stats, db: Database) -> None:
    period = period_from_key(callback_data.period)
    if period is None:
        await query.answer()
        return
    user = await db.get_user_by_lastfm(callback_data.user)
    target = (Target(user.lastfm_username, user.session_key, False, user.id, user.indexed_at is not None)
              if user else Target(callback_data.user, None, False))
    text, markup = await render_top(stats, target, KINDS[callback_data.kind], period, callback_data.page)
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified" on double taps
    await query.answer()
