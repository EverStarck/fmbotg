# Contributing and selfhosting

fmbotg is a Python port of [.fmbot](https://github.com/fmbot-discord/fmbot) for Telegram. Anyone can
run their own copy: this page covers setting it up, the API keys it needs, every command, and how the
code is organised.

For code contributions, small fixes are welcome as a PR against the default branch. For larger changes,
open an issue first so we can agree on the approach before you spend time on it.

The code is source-available under the same Apache 2.0 with Commons Clause license as .fmbot
(see [LICENSE.MD](LICENSE.MD)).

## Running the bot

Get the API keys below, install [uv](https://docs.astral.sh/uv/), then configure and run:

```bash
uv sync               # creates .venv with Python 3.12 and all dependencies
cp .env.example .env  # fill in the values
uv run fmbotg
```

### With Docker Compose (bot + Postgres)

`docker-compose.yml` runs the bot with its own Postgres database. Fill in `.env` (including
`POSTGRES_PASSWORD`; the compose file points the bot at its database), then:

```bash
docker compose up -d --build
docker compose logs -f bot
```

The database lives in the `postgres-data` volume, so redeploys keep everyone's data.

### On Coolify

1. Create a resource from this repository and pick the **Docker Compose** build pack.
2. Fill in the environment variables Coolify lists: `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
   `LASTFM_API_KEY` and `LASTFM_API_SECRET` are required, the rest are optional.
3. Leave the domain empty: the bot fetches updates from Telegram, nothing connects to it.
4. Deploy. Run only one copy: two bots polling with the same token conflict.

### Without Compose

`docker build -t fmbotg . && docker run --env-file .env -v fmbot-data:/data fmbotg` runs the bot alone with
SQLite in the `fmbot-data` volume.

## Getting API keys

All configuration lives in `.env`; `.env.example` lists every key with a comment.

### Telegram

1. Talk to [@BotFather](https://t.me/BotFather) and create a bot with `/newbot`.
2. Put the token in `TELEGRAM_BOT_TOKEN`.

### Last.fm

1. Create an [API account](https://www.last.fm/api/account/create) (no callback URL needed).
2. Put the key and secret in `LASTFM_API_KEY` and `LASTFM_API_SECRET`.

### Optional

- `DATABASE_URL`: SQLite by default (`fmbot.db`); a `postgresql+asyncpg://` URL works too.
- `OPENROUTER_API_KEY`: enables `/judge`, `/roast` and `/compliment`.
- `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`, `YOUTUBE_API_KEY`: exact links instead of search links.
- `MUSICBRAINZ_CONTACT`: a URL or email so MusicBrainz can reach you, as they ask.
- `BOT_OWNER_IDS`: your Telegram id (from `/whoami`) for the owner tools.
- `FM_ALIASES`: nicknames per group, see below. Keep it in `.env` so friends' usernames stay private.

## Commands

| Command | Aliases | What it does |
|---|---|---|
| `/login`, `/logout` | | Connect or disconnect Last.fm (login always happens in a private chat) |
| `/fm` | `/np` | Now playing, with track/artist/total playcounts |
| `/recent [n]` | `/rt` | Latest scrobbles |
| `/profile` | `/stats`, `/me` | Last.fm profile stats and history import status |
| `/update [full]` | `/index` | Fetch new scrobbles; `full` re-imports your whole history |
| `/plays [period]` | `/p` | Scrobble count |
| `/artistplays`, `/albumplays`, `/trackplays` | `/ap`, `/abp`, `/tp` | Your plays on something, optionally for a period |
| `/streak`, `/streaks` | `/str` | Current artist/album/track streak; streaks of 25+ plays are saved |
| `/pace [goal] [period]` | `/pc` | When you'll reach a scrobble goal |
| `/milestone [n\|random]` | `/ms` | Your Nth scrobble |
| `/overview [days]` | `/ov` | Day-by-day summary, up to 8 days |
| `/artisttracks`, `/artistalbums` | `/at`, `/aa` | Your top tracks/albums by an artist |
| `/discovery`, `/albumdiscovery`, `/trackdiscovery` | `/dd`, `/abdd`, `/tdd` | When you first and last played something |
| `/artistpace [goal]` | `/apc` | When you'll reach a play goal on an artist |
| `/albumtracks`, `/cover` | `/abt`, `/co` | Album track list with your plays; full-size cover |
| `/year [year]` | `/yr` | Year in review: totals, top lists, new artists |
| `/discoveries [period]` | `/ds` | Artists you first listened to in a period |
| `/taste` | `/t` | Compare top artists with someone (reply, `@handle` or `lfm:name`) |
| `/scrobble Artist - Track` | `/sb` | Scrobble a track now |
| `/gaps [artists\|albums\|tracks]` | | Things you came back to after 90+ days away |
| `/search text` | `/find` | Search your listening history |
| `/artistoverview` | `/ao` | You and one artist: plays, top tracks/albums, plays per year |
| `/affinity` | `/aff` | Who in the group has the most similar taste to you |
| `/love`, `/unlove`, `/loved` | `/l`, `/ul` | Love tracks on Last.fm, list loved tracks |
| `/settings`, `/timezone` | `/tz` | Time zone, `/fm` style (`full`/`compact`/`template`) and a custom `/fm` template |
| `/topartists`, `/topalbums`, `/toptracks` | `/ta`, `/tab`, `/tt` | Top lists with page buttons; `bb` shows rank changes vs the previous period |
| `/recap [period]` | `/rc` | A period at a glance, with buttons for top lists, discoveries and an album chart |
| `/receipt [period]` | `/rcpt` | Your top tracks as a store-receipt image |
| `/artist`, `/album`, `/track` | `/a`, `/ab`, `/tr` | Info and your plays; defaults to what you're playing |
| `/whoknows`, `/whoknowsalbum`, `/whoknowstrack` | `/wk`, `/wka`, `/wkt` | Group leaderboard for an artist, album or track; artist runs hand out crowns |
| `/globalwhoknows`, `/gwka`, `/gwkt` | `/gwk` | Leaderboard across everyone with an imported history |
| `/friendsfm` | `/ffm` | What your friends are playing |
| `/friendwhoknows`, `/fwka`, `/fwkt` | `/fwk` | Who knows among your friends |
| `/friends`, `/addfriend`, `/removefriend` | `/af`, `/rf` | Your friends: people you follow on Last.fm plus up to 20 added here |
| `/crowns`, `/crown`, `/crownleaderboard` | `/cws`, `/cw`, `/cl` | Crowns you hold in a group, one artist's crown history, most crowns |
| `/crownthreshold`, `/togglecrowns`, `/crownseeder`, `/killcrown` | `/ct`, `/kc` | Group admins: plays needed for a crown, turn crowns off, hand out crowns from imported histories, remove one |
| `/privacy global\|chat` | | Whether global who knows shows your name |
| `/groupartists`, `/groupalbums`, `/grouptracks [period]` | `/sa`, `/sab`, `/st` | Top lists across the group's imported histories, with listener counts |
| `/members`, `/groupsettings` | `/gs` | Who in the group has connected Last.fm; the group's crown, command and block settings |
| `/block`, `/unblock`, `/blocked` | | Group admins: leave someone out of this group's who knows, crowns and charts |
| `/disable`, `/enable`, `/disabled` | | Group admins: turn commands off in this group (any alias works, e.g. `/disable wk`) |
| `/autopost` | | Group admins: post the group's charts or a recap every Monday or on the 1st |
| `/jumble`, `/pixelation` | `/j`, `/px`, `/pxa` | Guess one of your artists from scrambled letters, or an album from a pixelated cover (`/pixelation artist` or `/pxa`: guess its artist instead) |
| `/shortcut add <name> <command>` | `/sc` | Your own commands, e.g. `/mine` for `/fm lfm:you`; extra words are added to the end |
| `/iceberg [period]` | `/ice` | Your artists from mainstream (tip) to obscure (bottom), banded by Last.fm listeners |
| `/playtime [period]`, `/timeleaderboard` | `/pt`, `/tlb` | Listening time from track lengths; the group leaderboard version |
| `/lyrics` | `/lyr` | Lyrics of what you're playing or `Artist - Track` (from LRCLIB) |
| `/links`, `/spotify`, `/youtube`, `/deezer` | `/sp`, `/yt`, `/dz` | Links to a track |
| `/judge`, `/roast`, `/compliment` | | An AI compliments or roasts your taste through OpenRouter (needs `OPENROUTER_API_KEY`; `OPENROUTER_MODEL` defaults to `anthropic/claude-opus-5.5`; `JUDGE_DAILY_LIMIT` per person per day) |
| `/help <command>`, `/faq`, `/whoami` | | One command's description and aliases; common questions; your Telegram id |
| `/botban`, `/botunban`, `/hideglobal`, `/unhideglobal`, `/censor`, `/uncensor`, `/moderation`, `/botstats` | | Owner only (`BOT_OWNER_IDS`): ignore someone everywhere, leave someone out of global who knows, hide an album's or artist's covers, bot totals |
| `/topgenres [period]`, `/topcountries [period]` | `/tg`, `/tc` | Your top genres, or where your top artists are from |
| `/genre [genre or artist]`, `/country [country or artist]` | `/g` | Your top artists in a genre or from a country; with an artist, its genres or country |
| `/whoknowsgenre`, `/whoknowscountry` | `/wkg`, `/wkc` | Group leaderboard for a genre or country (`/fwkg`, `/fwkc` among your friends) |
| `/chart [NxM]` | `/c` | Album collage, up to 10x10; `artists` for artist photos, `notitles` hides titles, `skip` leaves out ones without an image |

Periods, with the same aliases as FMBot:
- From Last.fm directly: `weekly` (default), `monthly`, `quarterly`, `half`, `yearly`, `alltime`
- From your imported history: `today`, `yesterday`, `1d` … `6d`, `2y`, or a year like `2019`.
  Ranges up to 8 days also work live for people without an imported history. Calendar days and years
  follow your `/timezone` (UTC by default).

Targeting someone else: reply to their message, or add `@handle` (if they've used the bot)
or `lfm:username` (any Last.fm user).

## Scrobble history

After `/login` the bot imports your full Last.fm history into its database in the background
and messages you when it's done. A background loop then fetches new scrobbles for active users
every couple of hours, and commands that need fresh data (`/whoknows`, range top lists) update
you first. If you edit or delete scrobbles on Last.fm, run `/update full`.

Migrations run automatically at startup (Alembic). To add one after changing `fmbotg/db/models.py`:
`DATABASE_URL=... uv run alembic revision --autogenerate -m "describe change"`.

## /fm templates

`/settings template` followed by your layout on the next lines replaces `/fm` with your own text,
using FMBot's `{{variable}}` names (`{{track.name}}`, `{{artist.plays}}`, `{{discovery.artist-discovered}}`, …).
`$$title:` lines are bold and `$$footer:` lines italic; lines whose variables are all empty are left out.
`/settings template` alone lists every variable, `/settings template off` removes it.

## Nicknames

`FM_ALIASES` in `.env` gives people in a group short names that work in every command: with
`FM_ALIASES='{"-1001234567890": {"alex": "alexlfm"}}'`, `/fm alex`, `/taste alex` or
`/topartists alex monthly` in that group use the Last.fm user `alexlfm`. If that person connected
Last.fm to the bot, their imported history is used too.

In commands that also search for music (`/artist`, `/album`, `/artistplays`, `/discovery`, …) only the
last word counts as a nickname, so `/artistplays radiohead alex` works and `/artist red hot chili peppers`
still searches for the band even if someone is called "red". Nicknames only work in their own chat.
They live in `.env` (not committed) so friends' usernames stay private. Restart the bot after changing them.

## Groups

The Telegram Bot API can't list a group's members, so the bot remembers who it has seen in each
group. Someone shows up in `/whoknows` once they've connected Last.fm and sent any command in
that group. Making the bot a group admin lets it notice when people leave. Who knows counts members
with an imported history from the database and looks up the rest live on Last.fm.

Crowns work like in FMBot: whoever has the most plays of an artist (30 by default) holds its crown in
that group. Crowns change hands when someone runs `/whoknows` for the artist; a tie keeps the current
holder, and a crown is never taken from someone whose playcount couldn't be fetched. Admin commands
check Telegram's admin list, so anonymous admins work too.

Disabled commands are ignored silently in that group and still work in private chat. `/help`,
`/login` and the enable/disable commands can't be turned off.

Global who knows only counts imported histories. People who keep the default `/privacy chat` show
as "Private user", except to themselves and in groups they're in.

## Spotify-style data without Spotify

FMBot gets artist photos, track lengths and popularity from Spotify. Since February 2026 Spotify's
API needs the app owner to have Premium, allows 5 users in development mode and no longer returns
popularity, so this port uses free sources instead:
- Artist photos (`/artist`, `/chart artists`) and track lengths: Deezer's public API; lengths come
  from Last.fm first. Plays of tracks with no known length count at your average length.
- Iceberg: Last.fm listener counts instead of Spotify popularity.
- Lyrics: LRCLIB. Links: exact Deezer links; Spotify and YouTube links open a search unless you set
  `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` or `YOUTUBE_API_KEY`.

All of it is cached per artist or track in the database and shared between users.

## Owner tools

Put your Telegram id (from `/whoami`) in `BOT_OWNER_IDS`. Owners can ban people from the whole bot
(`/botban`, they're ignored silently), hide fake scrobblers from global who knows (`/hideglobal`),
and censor covers (`/censor Artist - Album`, or `/censor Artist` for all their albums), which keeps
them out of charts, `/fm`, `/album`, `/cover` and pixelation. `/moderation` lists all of it.

## Games

`/jumble` gives 25 seconds to unscramble one of your artists from hints; `/pixelation` gives 40
seconds to name one of your albums from a cover that gets clearer as time passes. Answers are
lenient like FMBot's: case, accents, punctuation and edition suffixes don't matter, and one typo is
fine (two on long names). With Telegram's privacy mode on (the default), the bot only sees replies
to its own messages in groups, so players reply to the game message. One game runs per chat; games
live in memory, so a restart ends them.

## Genres and countries

Genres are an artist's Last.fm tags, minus tags that aren't genres ("seen live", nationalities,
decades). Countries come from MusicBrainz, which allows one request per second. Both are saved per
artist and shared between users, so each artist is looked up once (refreshed after 90 and 180 days).
A command waits a few seconds for missing artists, answers with what it knows, and keeps looking up
the rest in the background; the reply says when that happened. Set `MUSICBRAINZ_CONTACT` to a URL or
email so MusicBrainz can reach you, as they ask.

## Images

Charts and receipts are drawn with Pillow. It needs a font that covers the names in your music
library: the Docker image installs Noto CJK (Latin, Cyrillic, Greek, Chinese, Japanese, Korean).
Elsewhere it looks for common system fonts; set `FONT_PATH` to use a specific `.ttf`/`.ttc` file.

## Code layout

- `fmbotg/__main__.py` builds every service once (`Services`) and the dispatcher; handlers get services
  injected by parameter name.
- `fmbotg/handlers/` holds one aiogram router per area. Handlers import services, never each other, and
  nothing outside `handlers/` imports them.
- Services: `stats.py` (top lists, who knows counts), `indexer.py` (history import), `metadata.py` and
  `dimensions.py` (genres, countries, photos, track lengths), `members.py`, `social.py`, `group_charts.py`,
  `chart_service.py`, `moderation.py`, `autopost.py`, `judge.py`, `links.py`.
- `fmbotg/db/` is a package: `models.py` (tables), and query mixins by topic (`users`, `plays`, `chats`,
  `crowns`, `meta`) combined into `Database`.
- Leaf modules with no bot imports: `domain.py` (Target, errors, kinds), `names.py` (name matching),
  `periods.py`, `formatting.py`.
- Last.fm calls are limited to `LASTFM_RATE_LIMIT` requests per second (5 by default) across the bot, and
  request URLs (which carry session keys) are kept out of the logs.

## Tests

```bash
uv run pytest
```
