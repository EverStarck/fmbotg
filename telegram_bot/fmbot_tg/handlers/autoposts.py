"""/autopost: group charts posted on a schedule (port of AutopostBuilders and its interactions)."""

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..autopost import CONTENT, MAX_PER_CHAT, SCHEDULES, Autoposter, describe, next_post_at
from ..context import UserFacingError, require_group, require_group_admin
from ..db import Database, utcnow

router = Router(name="autoposts")

USAGE = (
    "<code>/autopost add recap weekly</code> — every Monday, the group's top artists, albums and tracks of the week\n"
    "<code>/autopost add artists monthly</code> — also <code>albums</code> or <code>tracks</code>; on the 1st, for the month\n"
    "Add <code>alltime</code> to post all-time charts on that schedule.\n"
    "<code>/autopost now 3</code> · <code>/autopost pause 3</code> · <code>/autopost resume 3</code> · "
    "<code>/autopost remove 3</code>"
)


def _when(post) -> str:
    when = post.next_post_at
    return f" · next {when:%a %d %b} at {when:%H:%M} UTC" if post.enabled else ""


async def _find(message: Message, db: Database, raw: str | None):
    if not raw or not raw.lstrip("#").isdigit():
        raise UserFacingError("Add the autopost's number, e.g. <code>/autopost now 3</code>. /autopost lists them.")
    post_id = int(raw.lstrip("#"))
    post = next((p for p in await db.autoposts(message.chat.id) if p.id == post_id), None)
    if post is None:
        raise UserFacingError(f"There's no autopost #{post_id} in this group.")
    return post


@router.message(Command("autopost", "autoposts"))
async def autopost(message: Message, command: CommandObject, db: Database, autoposter: Autoposter,
                   bot: Bot) -> None:
    require_group(message)
    args = (command.args or "").lower().split()
    posts = await db.autoposts(message.chat.id)
    if not args:
        lines = ["<b>Autoposts</b>", ""]
        lines += [f"{describe(p)}{_when(p)}" for p in posts] or ["None yet."]
        await message.reply("\n".join(lines) + "\n\n" + USAGE + "\n\n<i>Only group admins can change these.</i>")
        return

    await require_group_admin(message)
    action, rest = args[0], args[1:]
    if action == "add":
        content = next((a for a in rest if a in CONTENT), None)
        schedule = next((a for a in rest if a in SCHEDULES), None)
        if content is None or schedule is None:
            raise UserFacingError(USAGE)
        if len(posts) >= MAX_PER_CHAT:
            raise UserFacingError(f"A group can have up to {MAX_PER_CHAT} autoposts. Remove one first.")
        post = await db.add_autopost(message.chat.id, content, schedule, "alltime" in rest or "all" in rest,
                                     next_post_at(schedule, utcnow()))
        await message.reply(f"Added {describe(post)}{_when(post)}. "
                            f"Try it now with <code>/autopost now {post.id}</code>.")
    elif action in ("now", "test"):
        await autoposter.post(await _find(message, db, rest[0] if rest else None), bot, scheduled=False)
    elif action in ("pause", "resume"):
        post = await _find(message, db, rest[0] if rest else None)
        values = {"enabled": action == "resume"}
        if action == "resume":
            values["next_post_at"] = next_post_at(post.schedule, utcnow())
        await db.update_autopost(post.id, **values)
        await message.reply(f"Autopost #{post.id} {'resumed' if action == 'resume' else 'paused'}.")
    elif action in ("remove", "delete"):
        post = await _find(message, db, rest[0] if rest else None)
        await db.remove_autopost(message.chat.id, post.id)
        await message.reply(f"Removed autopost #{post.id}.")
    else:
        raise UserFacingError(USAGE)
