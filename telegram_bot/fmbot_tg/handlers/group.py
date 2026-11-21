"""Group administration and group-wide stats (port of GuildBuilders, GuildSettingBuilder and the
server top lists): /groupsettings, /members, /block, /unblock, /blocked, /disable, /enable,
/disabled, /groupartists, /groupalbums, /grouptracks."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..context import UserFacingError, require_group, require_group_admin, resolve_target
from ..db import DEFAULT_CROWN_THRESHOLD, Database
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, link, plural, truncate, user_link
from ..periods import ALL_TIME, Period, parse_period
from ..stats import music_url
from ..toggles import PROTECTED, CommandToggles

router = Router(name="group")
GROUP_TOP_SHOWN = 15


def _chat(message: Message) -> str:
    return esc(message.chat.title or "this group")


# --- overview ---------------------------------------------------------------------------------

@router.message(Command("groupsettings", "chatsettings", "serversettings", "gs"))
async def group_settings(message: Message, db: Database, toggles: CommandToggles) -> None:
    require_group(message)
    settings = await db.chat_settings(message.chat.id)
    blocked = await db.blocked_users(message.chat.id)
    disabled = sorted(await toggles.disabled(message.chat.id))
    crowns = ("off" if settings.crowns_disabled
              else f"on, at least {plural(settings.crown_threshold or DEFAULT_CROWN_THRESHOLD, 'play')}")
    await message.reply(
        f"<b>Settings for {_chat(message)}</b>\n\n"
        f"Crowns: <b>{crowns}</b> (/togglecrowns, /crownthreshold)\n"
        f"Disabled commands: <b>{esc(', '.join('/' + c for c in disabled)) if disabled else 'none'}</b> "
        "(/disable, /enable)\n"
        f"Blocked from leaderboards: <b>{len(blocked)}</b> (/block, /unblock, /blocked)\n\n"
        "<i>Only group admins can change these.</i>"
    )


@router.message(Command("members", "chatmembers", "registered"))
async def members(message: Message, db: Database, settings: Settings) -> None:
    """Who the bot knows in this group (port of GuildBuilders.MemberOverviewAsync)."""
    require_group(message)
    people = await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
    if not people:
        raise UserFacingError("Nobody here has connected Last.fm yet, or I haven't seen them send a message. "
                              "Use /login, then send any command here.")
    lines = [f"<b>Last.fm users in {_chat(message)}</b>", ""]
    for user in sorted(people, key=lambda u: (u.display_name or u.lastfm_username).lower()):
        name = esc(user.display_name or user.lastfm_username)
        imported = "" if user.indexed_at else " <i>(no imported history)</i>"
        lines.append(f"• {name} · {user_link(user.lastfm_username)}{imported}")
    indexed = sum(1 for u in people if u.indexed_at)
    lines += ["", f"<code>{plural(len(people), 'member')} · {indexed} with an imported history</code>",
              "<i>People show up after connecting Last.fm and sending a message here.</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


# --- blocking ---------------------------------------------------------------------------------

async def _block_target(message: Message, command: CommandObject, db: Database):
    args = (command.args or "").split()
    if not args and not message.reply_to_message:
        raise UserFacingError("Reply to their message, or add their <code>@handle</code> or "
                              "<code>lfm:username</code>.")
    target, _ = await resolve_target(message, args, db)
    if target.user_id is None:
        raise UserFacingError("They haven't connected Last.fm to this bot, so they aren't on any leaderboard here.")
    return target


@router.message(Command("block", "blockuser"))
async def block(message: Message, command: CommandObject, db: Database) -> None:
    """Leave someone out of who knows, crowns and group charts here (port of guild user blocking)."""
    await require_group_admin(message)
    target = await _block_target(message, command, db)
    if not await db.block_user(message.chat.id, target.user_id):
        raise UserFacingError(f"{user_link(target.lastfm_username)} is already blocked here.")
    await message.reply(f"{user_link(target.lastfm_username)} won't show up in who knows, crowns or group charts "
                        "here anymore. Their crowns go to the next listener when someone runs /whoknows. "
                        "Undo with /unblock.")


@router.message(Command("unblock", "unblockuser"))
async def unblock(message: Message, command: CommandObject, db: Database) -> None:
    await require_group_admin(message)
    target = await _block_target(message, command, db)
    if not await db.unblock_user(message.chat.id, target.user_id):
        raise UserFacingError(f"{user_link(target.lastfm_username)} isn't blocked here.")
    await message.reply(f"{user_link(target.lastfm_username)} is back on this group's leaderboards.")


@router.message(Command("blocked", "blockedusers"))
async def blocked(message: Message, db: Database) -> None:
    require_group(message)
    users = await db.blocked_users(message.chat.id)
    if not users:
        await message.reply("Nobody is blocked here.")
        return
    lines = [f"<b>Blocked in {_chat(message)}</b>", ""]
    lines += [f"• {esc(u.display_name or u.lastfm_username or '?')}"
              + (f" · {user_link(u.lastfm_username)}" if u.lastfm_username else "") for u in users]
    await message.reply("\n".join(lines))


# --- disabled commands ------------------------------------------------------------------------

def _parse_commands(args: str, toggles: CommandToggles) -> tuple[set[str], list[str]]:
    found, unknown = set(), []
    for word in args.split():
        name = toggles.resolve(word)
        if name is None:
            unknown.append(word)
        else:
            found.add(name)
    return found, unknown


@router.message(Command("disable", "disablecommand"))
async def disable(message: Message, command: CommandObject, toggles: CommandToggles) -> None:
    await require_group_admin(message)
    wanted, unknown = _parse_commands(command.args or "", toggles)
    if not wanted and not unknown:
        raise UserFacingError("Which commands? e.g. <code>/disable crowns topgenres</code>. "
                              "Aliases work too: <code>/disable wk</code> turns off /whoknows.")
    protected = wanted & PROTECTED
    wanted -= PROTECTED
    current = await toggles.disabled(message.chat.id)
    if wanted:
        await toggles.save(message.chat.id, current | wanted)
    lines = []
    if wanted:
        lines.append("Turned off here: " + ", ".join(f"/{c}" for c in sorted(wanted))
                     + ". I'll ignore them in this group; they still work in private chat.")
    if protected:
        lines.append("Can't turn off " + ", ".join(f"/{c}" for c in sorted(protected)) + ".")
    if unknown:
        lines.append("Unknown commands: " + ", ".join(esc(u) for u in unknown) + ".")
    await message.reply("\n".join(lines))


@router.message(Command("enable", "enablecommand"))
async def enable(message: Message, command: CommandObject, toggles: CommandToggles) -> None:
    await require_group_admin(message)
    current = await toggles.disabled(message.chat.id)
    if (command.args or "").strip().lower() == "all":
        await toggles.save(message.chat.id, set())
        await message.reply("All commands are on again here.")
        return
    wanted, unknown = _parse_commands(command.args or "", toggles)
    if not wanted and not unknown:
        raise UserFacingError("Which commands? e.g. <code>/enable crowns</code>, or <code>/enable all</code>.")
    turned_on = wanted & current
    if turned_on:
        await toggles.save(message.chat.id, current - turned_on)
    lines = []
    if turned_on:
        lines.append("Turned back on: " + ", ".join(f"/{c}" for c in sorted(turned_on)) + ".")
    if wanted - current:
        lines.append("Already on: " + ", ".join(f"/{c}" for c in sorted(wanted - current)) + ".")
    if unknown:
        lines.append("Unknown commands: " + ", ".join(esc(u) for u in unknown) + ".")
    await message.reply("\n".join(lines))


@router.message(Command("disabled", "disabledcommands"))
async def disabled(message: Message, toggles: CommandToggles) -> None:
    require_group(message)
    current = sorted(await toggles.disabled(message.chat.id))
    if not current:
        await message.reply("All commands are on in this group.")
        return
    await message.reply("Turned off in this group: " + ", ".join(f"/{c}" for c in current)
                        + "\nAdmins can turn them back on with /enable.")


# --- group top lists --------------------------------------------------------------------------

async def group_top_text(db: Database, chat_id: int, chat_title: str, kinds: list[str], period: Period,
                         max_members: int, shown: int = GROUP_TOP_SHOWN) -> str:
    """Top artists/albums/tracks across a group's imported histories (port of the server charts).
    Several kinds make a recap with one section each."""
    people = [m for m in await db.chat_lastfm_users(chat_id, max_members) if m.indexed_at]
    if not people:
        raise UserFacingError("Group charts use imported histories, and nobody here has one yet. "
                              "It's imported automatically after /login.")
    from_ts, to_ts = (0, 2**62) if period == ALL_TIME else period.bounds()
    title = "Top " + kinds[0] if len(kinds) == 1 else "Recap"
    lines = [f"<b>{title} in {esc(chat_title)}</b> · {esc(period.label)}"]
    found = False
    for kind in kinds:
        rows = await db.group_top([m.id for m in people], kind, from_ts, to_ts, limit=shown)
        found = found or bool(rows)
        lines.append("" if len(kinds) == 1 else f"\n<b>Top {kind}</b>")
        for i, (name, artist, plays, listeners) in enumerate(rows, 1):
            by = f" — {esc(artist)}" if artist else ""
            lines.append(f"{i}. {link(name, music_url(artist or name, name, kind))}{by} · "
                         f"<b>{plural(plays, 'play')}</b> · {plural(listeners, 'listener')}")
    if not found:
        raise UserFacingError(f"No plays here for {esc(period.label)}.")
    lines += ["", f"<code>From {plural(len(people), 'member')} with an imported history</code>"]
    return truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT)


async def _group_top(message: Message, command: CommandObject, kind: str, db: Database, settings: Settings) -> None:
    require_group(message)
    period, _ = parse_period((command.args or "").split())
    await message.reply(await group_top_text(db, message.chat.id, message.chat.title or "this group", [kind], period,
                                             settings.whoknows_max_members))


@router.message(Command("groupartists", "serverartists", "sa", "gta"))
async def group_artists(message: Message, command: CommandObject, db: Database, settings: Settings) -> None:
    await _group_top(message, command, "artists", db, settings)


@router.message(Command("groupalbums", "serveralbums", "sab", "gtab"))
async def group_albums(message: Message, command: CommandObject, db: Database, settings: Settings) -> None:
    await _group_top(message, command, "albums", db, settings)


@router.message(Command("grouptracks", "servertracks", "st", "gtt"))
async def group_tracks(message: Message, command: CommandObject, db: Database, settings: Settings) -> None:
    await _group_top(message, command, "tracks", db, settings)
