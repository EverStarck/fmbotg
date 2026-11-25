"""Entry point: `python -m fmbotg` (port of FMBot.Bot Program.cs / Startup.cs)."""

import asyncio
import logging
from dataclasses import dataclass, fields

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import ErrorEvent

from .autopost import Autoposter
from .commands import COMMANDS
from .config import Settings
from .context import AliasMiddleware, Aliases
from .db import Database
from .domain import UserFacingError
from .games import GameSessions
from .handlers import all_routers
from .indexer import Indexer
from .judge import Judge
from .lastfm import LastFmClient, LastFmError
from .links import MusicLinks
from .members import Members
from .metadata import Metadata
from .middleware import TrackMembersMiddleware, membership_router
from .migrate import upgrade as run_migrations
from .moderation import BanMiddleware, Moderation
from .shortcuts import ShortcutMiddleware, Shortcuts
from .stats import Stats
from .tasks import KeyedTasks
from .toggles import CommandToggles, DisabledCommandsMiddleware

log = logging.getLogger("fmbotg")



async def _reply_error(event: ErrorEvent, text: str) -> None:
    update = event.update
    if update.message:
        await update.message.reply(text)
    elif update.callback_query:
        await update.callback_query.answer(text[:200], show_alert=True)


@dataclass
class Services:
    """Everything the handlers get injected, built once."""

    db: Database
    lastfm: LastFmClient
    settings: Settings
    indexer: Indexer
    stats: Stats
    metadata: Metadata
    autoposter: Autoposter
    toggles: CommandToggles
    shortcuts: Shortcuts
    links: MusicLinks
    judge: Judge
    moderation: Moderation
    aliases: Aliases
    game_sessions: GameSessions
    login_polls: KeyedTasks
    members: Members

    @classmethod
    def create(cls, db: Database, lastfm: LastFmClient, settings: Settings, indexer: Indexer) -> "Services":
        toggles = CommandToggles(db)
        members = Members(db, settings.whoknows_max_members)
        return cls(
            db=db, lastfm=lastfm, settings=settings, indexer=indexer,
            stats=Stats(db, lastfm, indexer),
            metadata=Metadata(db, lastfm, lastfm.http, settings.user_agent),
            autoposter=Autoposter(db, members),
            toggles=toggles,
            shortcuts=Shortcuts(db, toggles),
            links=MusicLinks(lastfm.http, settings.spotify_client_id, settings.spotify_client_secret,
                             settings.youtube_api_key),
            judge=Judge(lastfm.http, settings.openrouter_api_key, settings.openrouter_model,
                        settings.judge_daily_limit),
            moderation=Moderation(db, settings.owner_ids),
            aliases=Aliases(settings.fm_aliases),
            game_sessions=GameSessions(),
            login_polls=KeyedTasks(),
            members=members,
        )

    async def close(self) -> None:
        await self.indexer.stop()
        await self.autoposter.stop()
        await self.login_polls.stop()
        await self.metadata.close()


def build_dispatcher(services: Services) -> Dispatcher:
    dp = Dispatcher(**{f.name: getattr(services, f.name) for f in fields(services)})
    # Outer middlewares run in this order. Banned people first: nothing else should run for them,
    # not even member tracking. Shortcuts before disabled commands, so a shortcut can't reach one.
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(BanMiddleware(services.moderation))
        observer.outer_middleware(AliasMiddleware(services.aliases))
    dp.message.outer_middleware(TrackMembersMiddleware(services.db))
    dp.message.outer_middleware(ShortcutMiddleware(services.shortcuts))
    dp.message.outer_middleware(DisabledCommandsMiddleware(services.toggles))
    dp.include_router(membership_router)
    routers = all_routers()
    for router in routers:
        dp.include_router(router)
    services.toggles.register(routers)

    @dp.errors(ExceptionTypeFilter(UserFacingError))
    async def user_facing(event: ErrorEvent) -> None:
        await _reply_error(event, str(event.exception))

    @dp.errors(ExceptionTypeFilter(LastFmError))
    async def lastfm_error(event: ErrorEvent) -> None:
        await _reply_error(event, event.exception.user_message())

    @dp.errors()
    async def unexpected(event: ErrorEvent) -> None:
        log.exception("Unhandled error", exc_info=event.exception)
        await _reply_error(event, "Something went wrong. Please try again.")

    return dp


async def run() -> None:
    settings = Settings.from_env()
    db = Database(settings.database_url)

    http = httpx.AsyncClient(timeout=15, headers={"User-Agent": settings.user_agent})
    lastfm = LastFmClient(settings.lastfm_api_key, settings.lastfm_api_secret, settings.lastfm_private_key, http,
                          requests_per_second=settings.lastfm_rate_limit)
    bot = Bot(settings.telegram_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    indexer = Indexer(db, lastfm, notify=lambda chat_id, text: bot.send_message(chat_id, text))
    services = Services.create(db, lastfm, settings, indexer)
    dp = build_dispatcher(services)

    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            raise SystemExit("Telegram rejected TELEGRAM_BOT_TOKEN. Copy it again from @BotFather.") from None
        await services.moderation.load()
        await bot.set_my_commands(COMMANDS)
        log.info("Starting as @%s", me.username)
        indexer.start()
        services.autoposter.start(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await services.close()
        await lastfm.close()
        await db.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and Last.fm calls carry users' session keys (sk=) and the
    # API keys in the query string. Keep those out of the logs.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    run_migrations(Settings.from_env().database_url)
    asyncio.run(run())


if __name__ == "__main__":
    main()
