"""Entry point: `python -m fmbot_tg` (port of FMBot.Bot Program.cs / Startup.cs)."""

import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import BotCommand, ErrorEvent

from .autopost import Autoposter
from .config import Settings
from .context import UserFacingError, configure_aliases
from .db import Database
from .handlers import all_routers
from .indexer import Indexer
from .lastfm import LastFmClient, LastFmError
from .links import MusicLinks
from .metadata import Metadata
from .middleware import TrackMembersMiddleware, membership_router
from .migrate import upgrade as run_migrations
from .stats import Stats
from .shortcuts import ShortcutMiddleware, Shortcuts
from .toggles import CommandToggles, DisabledCommandsMiddleware

log = logging.getLogger("fmbot_tg")

COMMANDS = [
    BotCommand(command="fm", description="What you're listening to now"),
    BotCommand(command="recent", description="Your latest scrobbles"),
    BotCommand(command="plays", description="Scrobble count, optionally for a period"),
    BotCommand(command="streak", description="Your current listening streak"),
    BotCommand(command="overview", description="Your last few days, day by day"),
    BotCommand(command="topartists", description="Top artists (add a period like monthly)"),
    BotCommand(command="topalbums", description="Top albums"),
    BotCommand(command="toptracks", description="Top tracks"),
    BotCommand(command="artist", description="Artist info and your plays"),
    BotCommand(command="album", description="Album info and your plays"),
    BotCommand(command="track", description="Track info and your plays"),
    BotCommand(command="artistplays", description="Your plays on an artist"),
    BotCommand(command="artisttracks", description="Your top tracks by an artist"),
    BotCommand(command="discovery", description="When you discovered an artist"),
    BotCommand(command="love", description="Love the track you're playing"),
    BotCommand(command="year", description="Your year in review"),
    BotCommand(command="taste", description="Compare your taste with someone"),
    BotCommand(command="affinity", description="Who here has the most similar taste"),
    BotCommand(command="gaps", description="Music you came back to after a long break"),
    BotCommand(command="search", description="Search your listening history"),
    BotCommand(command="artistoverview", description="You and one artist, in detail"),
    BotCommand(command="streaks", description="Your saved streaks"),
    BotCommand(command="albumtracks", description="Album track list with your plays"),
    BotCommand(command="cover", description="Full-size album cover"),
    BotCommand(command="whoknows", description="Who listens to an artist the most here"),
    BotCommand(command="whoknowsalbum", description="Who listens to an album the most here"),
    BotCommand(command="whoknowstrack", description="Who listens to a track the most here"),
    BotCommand(command="globalwhoknows", description="Who listens to an artist the most, globally"),
    BotCommand(command="friendsfm", description="What your friends are playing"),
    BotCommand(command="friendwhoknows", description="Who of your friends listens to an artist most"),
    BotCommand(command="friends", description="Your Last.fm follows and added friends"),
    BotCommand(command="addfriend", description="Add a friend, e.g. /addfriend username"),
    BotCommand(command="topgenres", description="Your top genres, optionally for a period"),
    BotCommand(command="genre", description="Your top artists in a genre, or an artist's genres"),
    BotCommand(command="topcountries", description="Where your top artists are from"),
    BotCommand(command="country", description="Your top artists from a country"),
    BotCommand(command="whoknowsgenre", description="Who listens to a genre the most here"),
    BotCommand(command="groupartists", description="Top artists across this group"),
    BotCommand(command="members", description="Last.fm users in this group"),
    BotCommand(command="groupsettings", description="This group's settings"),
    BotCommand(command="jumble", description="Guess the artist from scrambled letters"),
    BotCommand(command="pixelation", description="Guess the album from a pixelated cover"),
    BotCommand(command="autopost", description="Post group charts on a schedule (admins)"),
    BotCommand(command="shortcut", description="Your own command shortcuts"),
    BotCommand(command="iceberg", description="Your artists from mainstream to obscure"),
    BotCommand(command="playtime", description="How long you've listened, optionally for a period"),
    BotCommand(command="lyrics", description="Lyrics of what you're playing"),
    BotCommand(command="links", description="Spotify, YouTube and Deezer links for a track"),
    BotCommand(command="crowns", description="Your crowns in this group"),
    BotCommand(command="crown", description="Who holds the crown for an artist"),
    BotCommand(command="crownleaderboard", description="Who has the most crowns here"),
    BotCommand(command="chart", description="Album collage, e.g. /chart 4x4 monthly"),
    BotCommand(command="recap", description="A period at a glance"),
    BotCommand(command="receipt", description="Your top tracks as a receipt"),
    BotCommand(command="profile", description="Last.fm profile stats"),
    BotCommand(command="update", description="Fetch your latest scrobbles"),
    BotCommand(command="settings", description="Time zone and /fm style"),
    BotCommand(command="privacy", description="Show or hide your name in global who knows"),
    BotCommand(command="login", description="Connect your Last.fm account"),
    BotCommand(command="logout", description="Disconnect your Last.fm account"),
    BotCommand(command="help", description="All commands"),
]


async def _reply_error(event: ErrorEvent, text: str) -> None:
    update = event.update
    if update.message:
        await update.message.reply(text)
    elif update.callback_query:
        await update.callback_query.answer(text[:200], show_alert=True)


def build_dispatcher(db: Database, lastfm: LastFmClient, settings: Settings, indexer: Indexer,
                     metadata: Metadata | None = None, autoposter: Autoposter | None = None) -> Dispatcher:
    configure_aliases(settings.fm_aliases)
    metadata = metadata or Metadata(db, lastfm, lastfm.http, settings.user_agent)
    autoposter = autoposter or Autoposter(db, settings.whoknows_max_members)
    toggles = CommandToggles(db)
    shortcuts = Shortcuts(db, toggles)
    links = MusicLinks(lastfm.http, settings.spotify_client_id, settings.spotify_client_secret, settings.youtube_api_key)
    dp = Dispatcher(db=db, lastfm=lastfm, settings=settings, indexer=indexer, stats=Stats(db, lastfm, indexer),
                    metadata=metadata, toggles=toggles, shortcuts=shortcuts, autoposter=autoposter, links=links)
    dp.message.outer_middleware(TrackMembersMiddleware(db))
    # Shortcuts first, so a disabled command can't be reached through one
    dp.message.outer_middleware(ShortcutMiddleware(shortcuts))
    dp.message.outer_middleware(DisabledCommandsMiddleware(toggles))
    dp.include_router(membership_router)
    routers = all_routers()
    for router in routers:
        dp.include_router(router)
    toggles.register(routers)

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
    lastfm = LastFmClient(settings.lastfm_api_key, settings.lastfm_api_secret, settings.lastfm_private_key, http)
    bot = Bot(settings.telegram_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    indexer = Indexer(db, lastfm, notify=lambda chat_id, text: bot.send_message(chat_id, text))
    metadata = Metadata(db, lastfm, http, settings.user_agent)
    autoposter = Autoposter(db, settings.whoknows_max_members)
    dp = build_dispatcher(db, lastfm, settings, indexer, metadata, autoposter)

    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            raise SystemExit("Telegram rejected TELEGRAM_BOT_TOKEN. Copy it again from @BotFather.") from None
        await bot.set_my_commands(COMMANDS)
        log.info("Starting as @%s", me.username)
        indexer.start()
        autoposter.start(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await indexer.stop()
        await autoposter.stop()
        await metadata.close()
        await lastfm.close()
        await db.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_migrations(Settings.from_env().database_url)
    asyncio.run(run())


if __name__ == "__main__":
    main()
