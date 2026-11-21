# FMBot for Telegram (Python)

A Python port of FMBot that runs on Telegram instead of Discord. It uses
[aiogram 3](https://docs.aiogram.dev), httpx, SQLAlchemy 2 (async) and Pillow.

Phases 1, 2 and 3 of the port are done, and phase 4 is under way. See [MIGRATION_PLAN.md](MIGRATION_PLAN.md) for what's done and what comes next.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Create a Last.fm API account at https://www.last.fm/api/account/create (no callback URL needed).
3. Install [uv](https://docs.astral.sh/uv/), then configure and run:

```bash
cd telegram_bot
uv sync               # creates .venv with Python 3.12 and all dependencies
cp .env.example .env  # fill in the values
uv run fmbot-tg
```

Or with Docker: `docker build -t fmbot-tg . && docker run --env-file .env -v fmbot-data:/data fmbot-tg`

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
| `/topgenres [period]`, `/topcountries [period]` | `/tg`, `/tc` | Your top genres, or where your top artists are from |
| `/genre [genre or artist]`, `/country [country or artist]` | `/g` | Your top artists in a genre or from a country; with an artist, its genres or country |
| `/whoknowsgenre`, `/whoknowscountry` | `/wkg`, `/wkc` | Group leaderboard for a genre or country (`/fwkg`, `/fwkc` among your friends) |
| `/chart [NxM]` | `/c` | Album collage, up to 10x10; `notitles` hides titles, `skip` leaves out albums without a cover |

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

Migrations run automatically at startup (Alembic). To add one after changing `db.py`:
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

## Tests

```bash
uv run pytest
```
