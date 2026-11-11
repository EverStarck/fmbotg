# FMBot → Python/Telegram migration plan

The C# bot (`src/`, ~131k lines) stays as the reference implementation. This directory is the
Python/Telegram port, built in phases. Each phase ships a working bot.

## Platform mapping (Discord → Telegram)

| Discord concept (C#) | Telegram equivalent (Python) |
|---|---|
| NetCord gateway + sharding (`Program.cs`, `StartupService`) | aiogram long polling; switch to webhooks + multiple workers at scale |
| Slash commands + text commands with prefixes (`SlashCommands/`, `TextCommands/`, `PrefixService`) | `/commands` with free-text args (one implementation, no prefix setting) |
| Components V2 / embeds (`ResponseModel`, `GenericEmbedService`) | HTML-formatted messages, photo captions, inline keyboards |
| Button/select interactions (`Interactions/`, `ComponentInteractionTracker`) | `CallbackData` callback queries (64-byte limit: keep state server-side if larger) |
| Guild (`Guilds`, `GuildUsers`, `IndexService.refreshmembers`) | Group chat + `chat_members` table filled by `TrackMembersMiddleware` (Bot API cannot list members) |
| Discord user mentions | Reply-to-message, `@handle` (registered users), `lfm:username` |
| Ephemeral responses | Private chat with the bot (deep link `?start=…`) |
| Discord roles / permissions / channel toggles | Chat admin status (`getChatMember`), per-chat settings table |
| Webhooks (`WebhookService`) | Not needed: bot posts directly |
| Discord entitlements / Stripe supporters (`FMBot.Subscriptions`) | Telegram Stars payments (`sendInvoice` with `XTR`) and/or Stripe |
| Localization JSON + Weblate | Same JSON files can be reused; load per `from_user.language_code` |
| Hangfire jobs (`TimerService`) | APScheduler or arq (Redis) workers |
| Prometheus metrics (`LastFmStatistics`) | `prometheus_client` |
| EF Core + Dapper on Postgres | SQLAlchemy 2 async + Alembic on Postgres |
| gRPC link to web backend (`Protos/`) | `grpcio` with the same protos, if the web app is kept |

## Phase 1: core (done, this directory)

- Last.fm client with signing, private/public key split, retries on transient errors (`lastfm.py`)
- Web auth login with polling, logout (`handlers/user.py`)
- `/fm`, `/recent`, `/profile`
- `/topartists` `/topalbums` `/toptracks` with periods and paginated buttons
- `/artist` `/album` `/track` info with user playcounts, defaulting to now playing
- `/whoknows` `/whoknowsalbum` `/whoknowstrack` in groups (live Last.fm lookups, 5 min cache)
- `/chart` album collage, sizes up to 10x10 (Pillow)
- SQLite by default, Postgres via `DATABASE_URL`

Known phase 1 limitations:
- Who knows calls Last.fm once per member (cached). Fine for groups up to a few hundred linked users; phase 2 replaces it.
- Members only appear after they've sent a message the bot can see. With privacy mode on, that means a command.
- The schema is created with `create_all`; no migrations yet.

## Phase 2: play index (the foundation for most remaining features)

FMBot's speed comes from indexing every user's full scrobble history into Postgres
(`IndexService`, `UpdateService`, `SmallIndexRepository`, `user_plays`, `user_artists`,
`user_albums`, `user_tracks`). Almost everything in phases 3–5 reads from these tables.

- Postgres + Alembic migrations; tables: `user_plays`, `user_artists`, `user_albums`, `user_tracks`, `artists`, `albums`, `tracks`
- Initial import worker (paged `user.getRecentTracks`, 1000/page), incremental update job (`UpdateService`)
- Switch who knows to SQL (`WhoKnowsArtistService`, `WhoKnowsAlbumService`, `WhoKnowsTrackService`, `WhoKnowsFilterService`)
- Daily / custom / 2-year periods in `periods.py` (currently rejected with a message)
- Job runner (arq or APScheduler) replacing Hangfire `TimerService`

## Phase 3: Last.fm command parity

| C# source | Commands |
|---|---|
| `PlaySlashCommands`, `PlayBuilders` | plays, streak/streaks, overview, pace, milestone, year, recap, gaps, discoverydate, lastlistened, search |
| `ArtistSlashCommands`, `ArtistBuilders` | artistplays, artisttracks, artistalbums, artistpace, artistoverview, discoveries, taste, affinity, iceberg |
| `AlbumSlashCommands`, `AlbumBuilders` | albumplays, albumtracks, cover |
| `TrackSlashCommands`, `TrackBuilders` | trackplays, trackdetails, love/unlove (signed `track.love`), loved, receipt, scrobble (`track.scrobble`) |
| `ChartBuilders`, `ChartService`, `FMBot.Images` | artist charts, skip-empty, billboard/compare mode, time-listened |
| `UserSlashCommands`, `UserBuilder`, `FmSettingService`, `TemplateService` | settings, `/fm` modes and templates, privacy, botscrobbling, featured |
| `TopSlashCommands` options | billboard, extra large/small, time listened, no-singles, release year filters (`SettingService`) |

## Phase 4: social and group features

| C# source | Commands / features |
|---|---|
| `CrownService`, `CrownBuilders`, `CrownGuildSettingCommands` | crowns, crown, crown thresholds and seeding |
| `FriendsService`, `FriendBuilders` | friendsfm, addfriend, removefriend, friend who knows (`fwk*`) |
| Global who knows (`gwk*`) | global leaderboards across all users, with the privacy levels from `privacy` |
| `GenreService`, `TagService` | genre, wkgenre, fwkgenre |
| `CountryService`, `CountryBuilders` | country, countrychart, wkcountry |
| `GuildService`, `GuildBuilders`, `GuildSettingBuilder` | per-chat settings, server top lists, members, block/unblock (admin-gated) |
| `ChannelToggledCommandService`, `GuildDisabledCommandService` | per-chat disabled commands |
| `AutopostService`, `AutopostBuilders` | scheduled chart posts to a chat |
| `GameService`, `GameBuilders` | jumble / pixelation games, using inline keyboards |
| `ShortcutService` | per-user command shortcuts |

## Phase 5: integrations and platform

| C# source | Notes |
|---|---|
| `SpotifyService` | Spotify links and previews (client credentials) |
| `SpotifyRemoteService`, `SpotifyRemoteSlashCommands` | remote/queue/play/skip/like via user OAuth |
| `FMBot.AppleMusic`, `AppleMusicService` | Apple Music links and artwork |
| `FMBot.Discogs`, `DiscogsService` | collection commands (OAuth 1.0a) |
| `GeniusService` | lyrics search |
| `YoutubeService` | video links |
| `MusicBrainzService` | metadata (countries, release dates) |
| `OpenAiService` | AI features (judge) |
| `ImportService`, `ImportBuilders` | Spotify / Apple Music history imports (Telegram document uploads) |
| `EurovisionService` | Eurovision commands |
| `FMBot.Subscriptions`, `SupporterService`, `PremiumSettingBuilder` | supporter tiers; Telegram Stars or Stripe |
| `LocalizationService` + locale JSON | reuse the existing translation files |
| `AdminService`, `AdminCommands`, `OwnerCommands`, `CensorService` | owner tools, censoring, bot bans |
| `HelpService`, `FaqService` | full help/FAQ content |

## Not ported

- `WebhookService`, `BotListService`, `PrefixService`, Discord linked roles, `DisabledChannelService` (Discord-only)
- `FMBot.Youtube` project (already unused in the C# solution)
