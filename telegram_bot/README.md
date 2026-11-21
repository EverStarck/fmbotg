# FMBot for Telegram (Python)

A Python port of FMBot that runs on Telegram instead of Discord. It uses
[aiogram 3](https://docs.aiogram.dev), httpx, SQLAlchemy 2 (async) and Pillow.

Phases 1 and 2 of the port are done, and phase 3 is partly done. See [MIGRATION_PLAN.md](MIGRATION_PLAN.md) for what's done and what comes next.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Create a Last.fm API account at https://www.last.fm/api/account/create (no callback URL needed).
3. Configure and run:

```bash
cd telegram_bot
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # fill in the values
.venv/bin/python -m fmbot_tg
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
| `/streak` | `/str` | Current artist/album/track streak |
| `/pace [goal] [period]` | `/pc` | When you'll reach a scrobble goal |
| `/milestone [n\|random]` | `/ms` | Your Nth scrobble |
| `/overview [days]` | `/ov` | Day-by-day summary, up to 8 days |
| `/artisttracks`, `/artistalbums` | `/at`, `/aa` | Your top tracks/albums by an artist |
| `/discovery` | `/dd`, `/ll` | When you first and last played an artist |
| `/love`, `/unlove`, `/loved` | `/l`, `/ul` | Love tracks on Last.fm, list loved tracks |
| `/settings`, `/timezone` | `/tz` | Time zone and `/fm` style (`full`/`compact`) |
| `/topartists`, `/topalbums`, `/toptracks` | `/ta`, `/tab`, `/tt` | Top lists with page buttons |
| `/artist`, `/album`, `/track` | `/a`, `/ab`, `/tr` | Info and your plays; defaults to what you're playing |
| `/whoknows`, `/whoknowsalbum`, `/whoknowstrack` | `/wk`, `/wka`, `/wkt` | Group leaderboard for an artist, album or track |
| `/chart [NxM]` | `/c` | Album collage, up to 10x10; add `notitles` to hide titles |

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
`DATABASE_URL=... .venv/bin/alembic revision --autogenerate -m "describe change"`.

## Groups

The Telegram Bot API can't list a group's members, so the bot remembers who it has seen in each
group. Someone shows up in `/whoknows` once they've connected Last.fm and sent any command in
that group. Making the bot a group admin lets it notice when people leave. Who knows counts members
with an imported history from the database and looks up the rest live on Last.fm.

## Tests

```bash
.venv/bin/python -m pytest
```
