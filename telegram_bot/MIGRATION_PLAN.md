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

Known limitations, still open:
- Members only appear after they've sent a message the bot can see. With privacy mode on, that means a command.

## Phase 2: play index (done)

- Alembic migrations, run at startup (`migrate.py`); databases created by phase 1 are stamped and upgraded
- `user_plays` table with lower-cased match keys and indexes for user/time, artist, album and track lookups
- `Indexer`: queued full imports (1000 scrobbles/page, bounded by the start time), incremental updates from
  the last indexed scrobble, a per-user lock, and a background loop that refreshes active users and retries
  failed imports (replaces Hangfire `TimerService` for now)
- Import after `/login`, `/update` and `/update full`, history status in `/profile`; switching or unlinking
  a Last.fm account deletes the old plays
- Who knows: SQL counts for indexed members, live Last.fm lookups for the rest, autocorrected subject names
- Time range periods: today, yesterday, 1–6 days, two-year and calendar years, from the index; ranges up
  to 8 days also work live for unindexed users. `/chart` supports them, looking covers up through `album.getInfo`

Deferred from phase 2:
- `user_artists`/`user_albums`/`user_tracks` aggregate tables. FMBot keeps them so all-time counts match
  Last.fm's own totals; here aggregates are computed from `user_plays`, which is fine until it gets slow.
- Moving the indexer to separate worker processes (arq + Redis) when one process is no longer enough.

## Phase 3: Last.fm command parity (done)

Done:
- Settings (migration 0003): `/settings`, `/timezone` (IANA names; calendar periods and overviews use it), `/fm` compact mode
- `PlayBuilders`: plays, streak (live page, then continued from the index), pace, milestone (incl. random), overview
- Artist/album/track plays with periods (`artistplays`, `albumplays`, `trackplays`; periods other than all-time use the index)
- `artisttracks`, `artistalbums`, `discovery` (discoverydate + lastlistened, artists only), from the index
- `love`, `unlove`, `loved` (signed POST write calls)
- `year` (index-based year in review), `discoveries`, `artistpace`, `taste` (overlap of both listening distributions)
- `albumtracks` (index counts, live fallback capped at 30 tracks), `cover` (original-size image), `scrobble`
- `discovery` for albums and tracks (`albumdiscovery`, `trackdiscovery`)
- Saved streaks (migration 0004, saved at 25+ plays like FMBot, kept across `/update full`), `streaks`
- `gaps` (first 90+ day break per item, SQL window function), `search`, `artistoverview`, `affinity` (group taste neighbours)
- `/chart skip`
- Billboard mode for top lists (`bb`: rank changes vs the previous window of the same length, or the previous year)
- `/fm` templates (migration 0005) with FMBot's `{{variable}}` names; `$$title`/`$$description`/`$$footer` map to bold/plain/italic
- `recap` (overview, top lists, discoveries, album chart views) and `receipt` (Pillow image)
- Chart and receipt images use a Unicode font (Noto CJK in Docker, `FONT_PATH` override)

Moved to later phases, because they depend on data this phase doesn't have:
- Privacy levels: they control visibility in global who knows, so they come with it in phase 4
- Template variables for crowns, chat ranks and listeners: phase 4
- Iceberg, artist charts, track details (audio features), time-listened charts and recap's listening time:
  need Spotify data (artist images, popularity, track lengths), phase 5
- Recap's genre and country pages: phase 4 genres/countries; its bot statistics pages need a command log

## Phase 4: social and group features

Done so far (migration 0006):
- Privacy level (`/privacy`, `/settings privacy`): `global` names you in global who knows, `chat` (default) shows "Private user"
- Global who knows (`gwk`, `gwka`, `gwkt`) from imported histories, naming members of the current group regardless
- Friends: Last.fm follows (`user.getFriends`) plus up to 20 added with `addfriend`; `friends`, `removefriend`,
  `friendsfm`, friend who knows (`fwk`, `fwka`, `fwkt`)
- Crowns: claimed and stolen through `/whoknows` with crown history kept, `crowns`, `crown`, `crownleaderboard`;
  admin-only `crownthreshold`, `togglecrowns`, `crownseeder` (from imported histories), `killcrown`
- Genres and countries (migration 0007, `artist_metadata` cache): `topgenres`, `genre`, `whoknowsgenre`,
  `fwkgenre`, `topcountries`, `country`, `whoknowscountry`, `fwkcountry`, plus recap's genre and country views.
  Genres are filtered Last.fm tags (FMBot uses Spotify genres, which need phase 5); countries are from
  MusicBrainz in a 1 request/second background queue. `countrychart` is a text list, not FMBot's map image.

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
