# FMBot for Telegram (Python)

A Python port of FMBot that runs on Telegram instead of Discord. It uses
[aiogram 3](https://docs.aiogram.dev), httpx, SQLAlchemy 2 (async) and Pillow.

This is phase 1 of the port. See [MIGRATION_PLAN.md](MIGRATION_PLAN.md) for what's done and what comes next.

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
| `/profile` | `/stats`, `/me` | Last.fm profile stats |
| `/topartists`, `/topalbums`, `/toptracks` | `/ta`, `/tab`, `/tt` | Top lists with page buttons |
| `/artist`, `/album`, `/track` | `/a`, `/ab`, `/tr` | Info and your plays; defaults to what you're playing |
| `/whoknows`, `/whoknowsalbum`, `/whoknowstrack` | `/wk`, `/wka`, `/wkt` | Group leaderboard for an artist, album or track |
| `/chart [NxM]` | `/c` | Album collage, up to 10x10; add `notitles` to hide titles |

Periods: `weekly` (default), `monthly`, `quarterly`, `half`, `yearly`, `alltime`, plus the same
short aliases as FMBot (`w`, `m`, `q`, `h`, `y`, `at`, ...).

Targeting someone else: reply to their message, or add `@handle` (if they've used the bot)
or `lfm:username` (any Last.fm user).

## Groups

The Telegram Bot API can't list a group's members, so the bot remembers who it has seen in each
group. Someone shows up in `/whoknows` once they've connected Last.fm and sent any command in
that group. Making the bot a group admin lets it notice when people leave.

## Tests

```bash
.venv/bin/python -m pytest
```
