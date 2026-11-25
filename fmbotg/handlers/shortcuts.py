"""/shortcut: add, list and remove your own command shortcuts (port of the shortcut commands)."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import self_user
from ..domain import UserFacingError
from ..db import Database
from ..formatting import esc
from ..shortcuts import MAX_SHORTCUTS, NAME_RE, Shortcuts

router = Router(name="shortcuts")

USAGE = ("<code>/shortcut add mine /fm lfm:yourname</code> — then <code>/mine</code> runs that command\n"
         "<code>/shortcut remove mine</code>\n"
         "Anything you type after a shortcut is added to the end, e.g. <code>/mine compact</code>.")


@router.message(Command("shortcut", "shortcuts", "sc"))
async def shortcut(message: Message, command: CommandObject, db: Database, shortcuts: Shortcuts) -> None:
    me = await self_user(message, db)
    args = (command.args or "").split(maxsplit=2)
    current = await shortcuts.for_user(message.from_user.id)

    if not args:
        if not current:
            await message.reply(f"<b>Shortcuts</b>\nYou have none yet.\n\n{USAGE}")
            return
        lines = ["<b>Your shortcuts</b>", ""]
        lines += [f"/{name} → <code>{esc(output)}</code>" for name, output in current.items()]
        await message.reply("\n".join(lines) + f"\n\n{USAGE}")
        return

    action = args[0].lower()
    if action in ("remove", "delete", "rm"):
        if len(args) < 2:
            raise UserFacingError("Which one? e.g. <code>/shortcut remove mine</code>.")
        name = args[1].lstrip("/").lower()
        if not await db.remove_shortcut(me.id, name):
            raise UserFacingError(f"You don't have a /{esc(name)} shortcut.")
        shortcuts.forget(message.from_user.id)
        await message.reply(f"Removed /{esc(name)}.")
        return

    if action != "add" or len(args) < 3:
        raise UserFacingError(USAGE)
    name, output = args[1].lstrip("/").lower(), " ".join(args[2].split()) if "\n" not in args[2] else args[2].strip()
    if not NAME_RE.match(name):
        raise UserFacingError("Shortcut names can use letters, digits and _ (up to 32), like Telegram commands.")
    if shortcuts.toggles.resolve(name) is not None:
        raise UserFacingError(f"/{esc(name)} is already a command, so it can't be a shortcut.")
    if not output.startswith("/"):
        output = "/" + output
    target = output.split()[0]
    if shortcuts.toggles.resolve(target) is None:
        raise UserFacingError(f"<code>{esc(target)}</code> isn't a command I know. A shortcut has to run one, "
                              "e.g. <code>/fm</code> or <code>/chart 4x4 monthly</code>.")
    if name not in current and len(current) >= MAX_SHORTCUTS:
        raise UserFacingError(f"You can have up to {MAX_SHORTCUTS} shortcuts. Remove one first.")
    if len(output) > 500:
        raise UserFacingError("That command is too long for a shortcut.")
    is_new = await db.set_shortcut(me.id, name, output)
    shortcuts.forget(message.from_user.id)
    await message.reply(f"{'Added' if is_new else 'Updated'} /{esc(name)} → <code>{esc(output)}</code>. "
                        "It works in every chat; Telegram won't list it in the / menu.")
