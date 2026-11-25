"""Owner tools (port of the relevant AdminCommands/OwnerCommands) and /whoami."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import resolve_target
from ..domain import UserFacingError
from ..db import Database
from ..moderation import Moderation
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, num, truncate, user_link

router = Router(name="owner")


def _require_owner(message: Message, moderation: Moderation) -> None:
    if not moderation.is_owner(message.from_user.id):
        raise UserFacingError("Only the bot's owner can do that. Owners are set with BOT_OWNER_IDS in .env.")


async def _target_user(message: Message, command: CommandObject, db: Database):
    args = (command.args or "").split()
    if len(args) == 1 and args[0].lstrip("-").isdigit():  # a raw Telegram id
        user = await db.get_user(int(args[0]))
        if user is None:
            raise UserFacingError("I haven't seen that Telegram id.")
        return user
    if not args and not message.reply_to_message:
        raise UserFacingError("Reply to their message, or add their <code>@handle</code>, "
                              "<code>lfm:username</code> or Telegram id.")
    target, _ = await resolve_target(message, args, db)
    user = await db.get_user_by_id(target.user_id) if target.user_id else None
    if user is None:
        raise UserFacingError("They haven't used this bot.")
    return user


def _who(user) -> str:
    name = esc(user.display_name or user.lastfm_username or str(user.telegram_id))
    return f"{name} ({user_link(user.lastfm_username)})" if user.lastfm_username else name


@router.message(Command("whoami", "myid"))
async def whoami(message: Message, moderation: Moderation) -> None:
    owner = " You're an owner of this bot." if moderation.is_owner(message.from_user.id) else ""
    await message.reply(f"Your Telegram id is <code>{message.from_user.id}</code>.{owner}")


@router.message(Command("botban", "globalblock"))
async def bot_ban(message: Message, command: CommandObject, db: Database, moderation: Moderation) -> None:
    """Port of globalblockadd: the bot ignores them everywhere."""
    _require_owner(message, moderation)
    user = await _target_user(message, command, db)
    if moderation.is_owner(user.telegram_id):
        raise UserFacingError("Owners can't be banned.")
    await moderation.set_banned(user, True)
    await message.reply(f"Banned {_who(user)}. The bot now ignores them everywhere. Undo with /botunban.")


@router.message(Command("botunban", "globalunblock"))
async def bot_unban(message: Message, command: CommandObject, db: Database, moderation: Moderation) -> None:
    _require_owner(message, moderation)
    user = await _target_user(message, command, db)
    await moderation.set_banned(user, False)
    await message.reply(f"Unbanned {_who(user)}.")


@router.message(Command("hideglobal", "addbotted"))
async def hide_global(message: Message, command: CommandObject, db: Database, moderation: Moderation) -> None:
    """Port of addbotted: leaves fake or botted scrobblers out of global who knows."""
    _require_owner(message, moderation)
    user = await _target_user(message, command, db)
    await moderation.set_hidden(user, True)
    await message.reply(f"{_who(user)} is now left out of global who knows. Undo with /unhideglobal.")


@router.message(Command("unhideglobal", "removebotted"))
async def unhide_global(message: Message, command: CommandObject, db: Database, moderation: Moderation) -> None:
    _require_owner(message, moderation)
    user = await _target_user(message, command, db)
    await moderation.set_hidden(user, False)
    await message.reply(f"{_who(user)} is back in global who knows.")


def _censor_args(command: CommandObject) -> tuple[str, str | None]:
    text = " ".join((command.args or "").split())
    if not text:
        raise UserFacingError("Add an artist (<code>/censor Artist</code>, every album) or an album "
                              "(<code>/censor Artist - Album</code>).")
    if " - " in text:
        artist, album = text.split(" - ", 1)
        return artist.strip(), album.strip()
    return text, None


@router.message(Command("censor", "addcensored"))
async def censor(message: Message, command: CommandObject, db: Database, moderation: Moderation) -> None:
    """Port of addcensoredalbum/addcensoredartist: their images are never shown."""
    _require_owner(message, moderation)
    artist, album = _censor_args(command)
    if not await moderation.censor(artist, album):
        raise UserFacingError("That's already censored.")
    what = f"<b>{esc(album)}</b> by {esc(artist)}" if album else f"every album by <b>{esc(artist)}</b>"
    await message.reply(f"Censored {what}: no covers in charts, /fm or pixelation.")


@router.message(Command("uncensor", "removecensored"))
async def uncensor(message: Message, command: CommandObject, db: Database, moderation: Moderation) -> None:
    _require_owner(message, moderation)
    artist, album = _censor_args(command)
    if not await moderation.uncensor(artist, album):
        raise UserFacingError("That isn't censored.")
    await message.reply("Removed from the censor list.")


@router.message(Command("moderation", "censored", "banned"))
async def moderation_list(message: Message, db: Database, moderation: Moderation) -> None:
    _require_owner(message, moderation)
    lines = ["<b>Moderation</b>", ""]
    flagged = await db.flagged_users()
    banned = [u for u in flagged if u.bot_banned]
    hidden = [u for u in flagged if u.global_hidden]
    lines.append("Banned: " + (", ".join(_who(u) for u in banned) or "nobody"))
    lines.append("Hidden from global who knows: " + (", ".join(_who(u) for u in hidden) or "nobody"))
    censored = await db.censored()
    lines.append("Censored: " + (", ".join(esc(f"{c.artist} - {c.album}" if c.album else f"{c.artist} (all)")
                                         for c in censored) or "nothing"))
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("botstats"))
async def bot_stats(message: Message, db: Database, moderation: Moderation) -> None:
    _require_owner(message, moderation)
    stats = await db.bot_stats()
    lines = ["<b>Bot stats</b>", ""] + [f"{name.capitalize()}: <b>{num(value)}</b>" for name, value in stats.items()]
    await message.reply("\n".join(lines))
