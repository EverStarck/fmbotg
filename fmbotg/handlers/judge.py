"""/judge, /roast, /compliment (port of the judge command)."""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..context import resolve_target
from ..domain import STALE_BUTTON, Target, UserFacingError
from ..db import Database
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, truncate, user_link
from ..judge import Judge, JudgeUnavailable
from ..periods import ALL_TIME, Period, parse_period, period_from_key
from ..stats import Stats

router = Router(name="judge")


class JudgeButton(CallbackData, prefix="jd"):
    mode: str
    period: str
    user: str
    by: int  # only the person who asked can press it, since it uses their daily judgements


async def _judge(judge: Judge, stats: Stats, telegram_id: int, mode: str, target: Target, period: Period) -> str:
    artists = await stats.top(target, "artists", period, page=1, page_size=15)
    tracks = await stats.top(target, "tracks", period, page=1, page_size=10)
    if not artists.items:
        raise UserFacingError(f"No artists for {user_link(target.lastfm_username)} ({esc(period.label)}).")
    try:
        text = await judge.run(telegram_id, mode, period.label, [(i.name, i.playcount) for i in artists.items],
                               [(i.name, i.artist or "", i.playcount) for i in tracks.items])
    except JudgeUnavailable as exc:
        raise UserFacingError(str(exc)) from None
    title = "🔥 Roast" if mode == "roast" else "✨ Compliment"
    left = judge.remaining(telegram_id)
    return truncate(f"<b>{title} for {user_link(target.lastfm_username)}</b> · {esc(period.label)}\n\n{esc(text)}\n\n"
                    f"<i>By {esc(judge.model_name)} · {left} left today</i>", TELEGRAM_TEXT_LIMIT)


async def _start(message: Message, command: CommandObject, db: Database, judge: Judge) -> tuple[Target, Period]:
    if not judge.enabled:
        raise UserFacingError("Judging isn't set up on this bot. The owner can add an OpenRouter API key "
                              "(<code>OPENROUTER_API_KEY</code>) to turn it on.")
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, _ = await resolve_target(message, args, db)
    return target, period


@router.message(Command("judge", "ai"))
async def judge_menu(message: Message, command: CommandObject, db: Database, judge: Judge) -> None:
    target, period = await _start(message, command, db, judge)
    try:
        buttons = [InlineKeyboardButton(text=label, callback_data=JudgeButton(
            mode=mode, period=period.key, user=target.lastfm_username, by=message.from_user.id).pack())
            for mode, label in (("compliment", "✨ Compliment"), ("roast", "🔥 Roast"))]
    except ValueError:
        # Telegram button data is limited to 64 bytes; a long or odd lfm: name doesn't fit
        raise UserFacingError("Use <code>/roast</code> or <code>/compliment</code> directly for this user.") from None
    await message.reply(f"Judge {user_link(target.lastfm_username)}'s taste ({esc(period.label)}) with {esc(judge.model_name)}. "
                        f"You have {judge.remaining(message.from_user.id)} left today.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))


@router.message(Command("roast", "compliment"))
async def judge_direct(message: Message, command: CommandObject, db: Database, stats: Stats, judge: Judge) -> None:
    target, period = await _start(message, command, db, judge)
    await message.bot.send_chat_action(message.chat.id, "typing")
    await message.reply(await _judge(judge, stats, message.from_user.id, command.command.lower(), target, period))


@router.callback_query(JudgeButton.filter(F.mode.in_(("compliment", "roast"))))
async def judge_button(query: CallbackQuery, callback_data: JudgeButton, db: Database, stats: Stats,
                       judge: Judge) -> None:
    if not isinstance(query.message, Message):
        await query.answer(STALE_BUTTON, show_alert=True)
        return
    if query.from_user.id != callback_data.by:
        await query.answer("Only the person who asked can pick. Run /judge yourself.", show_alert=True)
        return
    period = period_from_key(callback_data.period) or ALL_TIME
    user = await db.get_user_by_lastfm(callback_data.user)
    target = Target.from_user(user, False) if user else Target(callback_data.user, None, False)
    await query.answer("Thinking…")
    await query.message.edit_reply_markup(reply_markup=None)
    try:
        text = await _judge(judge, stats, query.from_user.id, callback_data.mode, target, period)
    except UserFacingError as exc:
        text = str(exc)
    await query.message.reply(text)
