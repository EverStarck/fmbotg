"""/crowns, /crown, /crownleaderboard and the admin crown settings
(port of CrownBuilders and CrownGuildSettingCommands)."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..context import (
    UserFacingError, require_group, require_group_admin, resolve_subject, resolve_target,
)
from ..crowns import display_name
from ..db import DEFAULT_CROWN_THRESHOLD, Database, name_key
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, plural, truncate, user_link
from ..lastfm import LastFmClient
from ..stats import Stats

router = Router(name="crowns")
SHOWN = 25


async def _crowns_enabled(message: Message, db: Database) -> int:
    """The chat's crown threshold, or an error if crowns are off."""
    settings = await db.chat_settings(message.chat.id)
    if settings.crowns_disabled:
        raise UserFacingError("Crowns are turned off in this group. Admins can turn them on with /togglecrowns.")
    return settings.crown_threshold or DEFAULT_CROWN_THRESHOLD


@router.message(Command("crowns", "cws"))
async def crowns(message: Message, command: CommandObject, db: Database) -> None:
    require_group(message)
    threshold = await _crowns_enabled(message, db)
    target, _ = await resolve_target(message, (command.args or "").split(), db)
    if target.user_id is None:
        raise UserFacingError("Only people who connected Last.fm to this bot can hold crowns.")
    held = await db.user_crowns(message.chat.id, target.user_id)
    who = "You don't" if target.is_self else f"{user_link(target.lastfm_username)} doesn't"
    if not held:
        raise UserFacingError(f"{who} have any crowns here yet. Crowns go to whoever has the most plays of an "
                              f"artist (at least {threshold}) when someone runs /whoknows for it.")
    lines = [f"<b>Crowns for {user_link(target.lastfm_username)}</b> in {esc(message.chat.title or 'this chat')}", ""]
    for i, crown in enumerate(held[:SHOWN], 1):
        lines.append(f"{i}. {esc(crown.artist)} · <b>{plural(crown.playcount, 'play')}</b>")
    lines += ["", f"<code>{plural(len(held), 'crown')}</code>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("crown", "cw"))
async def crown(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, stats: Stats) -> None:
    require_group(message)
    await _crowns_enabled(message, db)
    query = " ".join((command.args or "").split())
    target = None if query else (await resolve_target(message, [], db))[0]
    artist = (await stats.canonical_subject("artist", await resolve_subject("artist", query, target, lastfm)))[0]

    history = await db.crown_history(message.chat.id, artist)
    if not history:
        raise UserFacingError(f"Nobody holds the crown for <b>{esc(artist)}</b> yet. "
                              f"Run <code>/whoknows {esc(artist)}</code> to claim it.")
    lines = [f"<b>Crown for {esc(artist)}</b>", ""]
    current = next(((c, u) for c, u in history if c.active), None)
    if current:
        c, u = current
        lines.append(f"👑 <b>{esc(display_name(u))}</b> · {plural(c.playcount, 'play')}, "
                     f"held since {c.created_at:%d %b %Y}")
    past = [(c, u) for c, u in history if not c.active]
    if past:
        lines += ["", "<b>Before that</b>"]
        for c, u in past:
            lines.append(f"• {esc(display_name(u))} · claimed {c.created_at:%d %b %Y} with "
                         f"{plural(c.start_playcount, 'play')}")
    await message.reply("\n".join(lines))


@router.message(Command("crownleaderboard", "cl", "topcrowns"))
async def crown_leaderboard(message: Message, db: Database, settings: Settings) -> None:
    require_group(message)
    await _crowns_enabled(message, db)
    members = await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
    rows = await db.crown_leaderboard(message.chat.id, [m.id for m in members])
    if not rows:
        raise UserFacingError("Nobody has a crown here yet. Run /whoknows for an artist to hand one out.")
    lines = [f"<b>Most crowns</b> in {esc(message.chat.title or 'this chat')}", ""]
    for i, (user, count) in enumerate(rows, 1):
        name = esc(display_name(user))
        if user.telegram_id == message.from_user.id:
            name = f"<b>{name}</b>"
        lines.append(f"{i}. {name} · {plural(count, 'crown')}")
    await message.reply("\n".join(lines))


# --- admin ----------------------------------------------------------------------------------

@router.message(Command("crownthreshold", "ct"))
async def crown_threshold(message: Message, command: CommandObject, db: Database) -> None:
    require_group(message)
    value = (command.args or "").strip()
    current = (await db.chat_settings(message.chat.id)).crown_threshold or DEFAULT_CROWN_THRESHOLD
    if not value:
        await message.reply(f"Crowns need at least <b>{plural(current, 'play')}</b> here. "
                            f"Admins can change it, e.g. <code>/crownthreshold 50</code>.")
        return
    await require_group_admin(message)
    if not value.isdigit() or not 1 <= int(value) <= 100_000:
        raise UserFacingError("Use a number of plays from 1 to 100000, e.g. <code>/crownthreshold 50</code>.")
    await db.update_chat_settings(message.chat.id, crown_threshold=int(value))
    await message.reply(f"Crowns now need at least <b>{plural(int(value), 'play')}</b>. Crowns people already "
                        "hold stay until someone with more plays takes them.")


@router.message(Command("togglecrowns", "crownstoggle"))
async def toggle_crowns(message: Message, db: Database) -> None:
    await require_group_admin(message)
    disabled = not (await db.chat_settings(message.chat.id)).crowns_disabled
    await db.update_chat_settings(message.chat.id, crowns_disabled=disabled)
    await message.reply("Crowns are now <b>off</b> in this group. Existing crowns are kept for when you turn "
                        "them back on." if disabled else "Crowns are now <b>on</b> in this group.")


@router.message(Command("killcrown", "kc"))
async def kill_crown(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                     stats: Stats) -> None:
    await require_group_admin(message)
    query = " ".join((command.args or "").split())
    if not query:
        raise UserFacingError("Which artist? e.g. <code>/killcrown Radiohead</code>. "
                              "<code>/killcrown all</code> removes every crown in this group.")
    if query.lower() == "all":
        removed = await db.delete_crowns(message.chat.id)
        await message.reply(f"Removed all crowns and their history ({plural(removed, 'record')}).")
        return
    artist = (await stats.canonical_subject("artist", (query,)))[0]
    if not await db.delete_crowns(message.chat.id, artist):
        raise UserFacingError(f"There's no crown for <b>{esc(artist)}</b> here.")
    await message.reply(f"Removed the crown for <b>{esc(artist)}</b> and its history.")


@router.message(Command("crownseeder", "seedcrowns"))
async def crown_seeder(message: Message, db: Database, settings: Settings) -> None:
    """Hand out crowns for every artist at once from imported histories (port of CrownService.SeedCrownsAsync).

    Only fills artists without a crown; existing crowns still change hands through /whoknows.
    """
    await require_group_admin(message)
    threshold = await _crowns_enabled(message, db)
    members = [m for m in await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
               if m.indexed_at is not None]
    if not members:
        raise UserFacingError("Nobody here has an imported history yet, so there's nothing to seed from.")

    best: dict[str, tuple[str, int, int]] = {}  # artist key -> (artist, user id, plays)
    for artist, user_id, plays in await db.crown_candidates([m.id for m in members], threshold):
        key = name_key(artist)
        if key not in best or plays > best[key][2]:
            best[key] = (artist, user_id, plays)
    created = await db.seed_crowns(message.chat.id, list(best.values()))
    await message.reply(f"Seeded <b>{plural(created, 'crown')}</b> from the imported histories of "
                        f"{plural(len(members), 'member')} (at least {plural(threshold, 'play')} each). "
                        "Artists that already had a crown were left alone.")
