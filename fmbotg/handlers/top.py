"""/topartists, /topalbums, /toptracks with inline pagination (port of TopSlashCommands)."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..context import resolve_target
from ..domain import STALE_BUTTON, Target, UserFacingError
from ..db import Database
from ..formatting import esc, link, num, plural, user_link
from ..lastfm import TopKind
from ..periods import Period, parse_period, period_from_key
from ..stats import Stats

router = Router(name="top")
PAGE_SIZE = 10

KINDS: dict[str, TopKind] = {"a": "artists", "b": "albums", "t": "tracks"}
KIND_KEYS = {kind: key for key, kind in KINDS.items()}
BILLBOARD_WORDS = {"bb", "billboard", "compare"}  # SettingService billboard aliases


class TopPage(CallbackData, prefix="top"):
    kind: str
    period: str
    page: int
    bb: int = 0  # billboard mode
    user: str = ""  # Last.fm usernames are at most 15 chars, which keeps us under Telegram's 64 bytes


def rank_change(rank: int, previous_rank: int | None) -> str:
    if previous_rank is None:
        return ""
    if previous_rank == 0:
        return " 🆕"
    if previous_rank > rank:
        return f" ▲{previous_rank - rank}"
    if previous_rank < rank:
        return f" ▼{rank - previous_rank}"
    return " ＝"


async def render_top(stats: Stats, target: Target, kind: TopKind, period: Period, page: int,
                     billboard: bool = False):
    user = target.lastfm_username
    result = await stats.top(target, kind, period, page=page, page_size=PAGE_SIZE, billboard=billboard)
    if not result.items:
        raise UserFacingError(f"No {kind} for {user_link(user)} in this period ({esc(period.label)}).")

    mode = " · billboard" if billboard else ""
    lines = [f"<b>Top {kind} for {user_link(user)}</b> · {esc(period.label)}{mode}", ""]
    start = (page - 1) * PAGE_SIZE
    for offset, item in enumerate(result.items, start=1):
        name = link(item.name, item.url)
        if item.artist:
            name = f"{name} — {esc(item.artist)}"
        rank = start + offset
        lines.append(f"{rank}. {name} · <b>{num(item.playcount)}</b>{rank_change(rank, item.previous_rank)}")
    lines += ["", f"<code>Page {page}/{result.total_pages} · {plural(result.total, 'different ' + kind[:-1])}</code>"]

    def button(text: str, to_page: int) -> InlineKeyboardButton:
        data = TopPage(kind=KIND_KEYS[kind], period=period.key, page=to_page, bb=int(billboard), user=user)
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
    billboard = any(a.lower() in BILLBOARD_WORDS for a in args)
    args = [a for a in args if a.lower() not in BILLBOARD_WORDS]
    target, _ = await resolve_target(message, args, db)
    text, markup = await render_top(stats, target, kind, period, page=1, billboard=billboard)
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
    if not isinstance(query.message, Message):  # older than 48 hours, or deleted
        await query.answer(STALE_BUTTON, show_alert=True)
        return
    period = period_from_key(callback_data.period)
    if period is None:
        await query.answer()
        return
    user = await db.get_user_by_lastfm(callback_data.user)
    target = Target.from_user(user, False) if user else Target(callback_data.user, None, False)
    text, markup = await render_top(stats, target, KINDS[callback_data.kind], period, callback_data.page,
                                    billboard=bool(callback_data.bb))
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # "message is not modified" on double taps
    await query.answer()
