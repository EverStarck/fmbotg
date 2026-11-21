"""Custom /fm templates (port of TemplateService, using the same {{variable}} names as FMBot).

Template text is escaped; only variable values (already HTML-safe) are inserted. Lines whose
variables all came out empty are dropped, like FMBot. Of the `$$option:` embed lines only
title, description and footer have a Telegram equivalent (bold, plain, italic); others are ignored.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from .context import Target
from .db import Database
from .formatting import duration, esc, lastfm_user_url, num
from .lastfm import LastFmClient, LastFmError, Scrobble, as_list
from .periods import MONTHLY, WEEKLY
from .stats import music_url

MAX_TEMPLATE_LENGTH = 2000
VARIABLE_RE = re.compile(r"\{\{\s*([a-z0-9.-]+)\s*\}\}")

EXAMPLE_TEMPLATE = (
    "$$title: {{track.name}}\n"
    "{{track.artist}} · {{track.album}}\n"
    "{{track.plays}} plays · {{artist.plays}} artist plays · {{track.loved}}\n"
    "$$footer: {{lastfm.total-scrobbles}} scrobbles · discovered {{discovery.artist-discovered}}"
)


@dataclass
class TemplateContext:
    target: Target
    display_name: str
    current: Scrobble
    previous: Scrobble | None
    total: int
    lastfm: LastFmClient
    db: Database


Resolver = Callable[[TemplateContext], Awaitable[str]]
VARIABLES: dict[str, Resolver] = {}


def variable(name: str):
    def register(fn: Resolver) -> Resolver:
        VARIABLES[name] = fn
        return fn
    return register


def _const(name: str, fn: Callable[[TemplateContext], str]) -> None:
    async def resolve(ctx: TemplateContext) -> str:
        return fn(ctx)
    VARIABLES[name] = resolve


_const("lastfm.user-name", lambda c: esc(c.target.lastfm_username))
_const("lastfm.url", lambda c: esc(lastfm_user_url(c.target.lastfm_username)))
_const("lastfm.total-scrobbles", lambda c: num(c.total))
_const("user.display-name", lambda c: esc(c.display_name))
_const("author.display-name", lambda c: esc(c.display_name))
_const("track.name", lambda c: esc(c.current.track))
_const("track.artist", lambda c: esc(c.current.artist))
_const("track.album", lambda c: esc(c.current.album))
_const("track.url", lambda c: esc(c.current.url))
_const("track.loved", lambda c: "❤️" if c.current.loved else "")
_const("album.cover-url", lambda c: esc(c.current.image or ""))
_const("previous-track.name", lambda c: esc(c.previous.track) if c.previous else "")
_const("previous-track.artist", lambda c: esc(c.previous.artist) if c.previous else "")
_const("previous-track.album", lambda c: esc(c.previous.album) if c.previous else "")
_const("previous-track.url", lambda c: esc(c.previous.url) if c.previous else "")


def _subject(ctx: TemplateContext, kind: str) -> tuple[str, ...]:
    cur = ctx.current
    return {"artist": (cur.artist,), "album": (cur.artist, cur.album), "track": (cur.artist, cur.track)}[kind]


def _music_url(ctx: TemplateContext, kind: str) -> str:
    subject = _subject(ctx, kind)
    return music_url(subject[0], subject[1] if len(subject) > 1 else None, kind + "s")


for _kind in ("artist", "album", "track"):
    def _make(kind: str) -> None:
        async def plays(ctx: TemplateContext) -> str:
            if kind == "album" and not ctx.current.album:
                return ""
            return num(await ctx.lastfm.user_playcount(kind, _subject(ctx, kind), ctx.target.lastfm_username))

        async def plays_in(ctx: TemplateContext, period) -> str:
            if not ctx.target.indexed or (kind == "album" and not ctx.current.album):
                return ""
            from_ts, to_ts = period.bounds(tz=ctx.target.tz)
            count, _, _ = await ctx.db.subject_stats(ctx.target.user_id, kind, _subject(ctx, kind), from_ts, to_ts)
            return num(count)

        async def first_last(ctx: TemplateContext, which: int) -> str:
            if not ctx.target.indexed or (kind == "album" and not ctx.current.album):
                return ""
            stats = await ctx.db.subject_stats(ctx.target.user_id, kind, _subject(ctx, kind))
            ts = stats[which]
            return f"{datetime.fromtimestamp(ts, ctx.target.tz):%d %B %Y}" if ts else ""

        VARIABLES[f"{kind}.plays"] = plays
        VARIABLES[f"{kind}.plays-week"] = lambda c: plays_in(c, WEEKLY)
        VARIABLES[f"{kind}.plays-month"] = lambda c: plays_in(c, MONTHLY)
        VARIABLES[f"discovery.{kind}-discovered"] = lambda c: first_last(c, 1)
        VARIABLES[f"lastlisten.{kind}-lastlistened"] = lambda c: first_last(c, 2)
        if kind != "track":
            _const(f"{kind}.url", lambda c: esc(_music_url(c, kind)))
    _make(_kind)


@variable("lastfm.join-date")
async def _join_date(ctx: TemplateContext) -> str:
    info = await ctx.lastfm.user_info(ctx.target.lastfm_username)
    ts = int((info.get("registered") or {}).get("unixtime") or 0)
    return f"{datetime.fromtimestamp(ts, ctx.target.tz):%d %B %Y}" if ts else ""


@variable("track.duration")
async def _duration(ctx: TemplateContext) -> str:
    ms = int((await ctx.lastfm.track_info(ctx.current.artist, ctx.current.track)).get("duration") or 0)
    return duration(ms) if ms else ""


@variable("artist.genres")
async def _genres(ctx: TemplateContext) -> str:
    info = await ctx.lastfm.artist_info(ctx.current.artist)
    tags = [t.get("name", "") for t in as_list((info.get("tags") or {}).get("tag"))][:3]
    return esc(", ".join(t.lower() for t in tags if t))


async def _value(name: str, ctx: TemplateContext) -> str:
    resolver = VARIABLES.get(name)
    if resolver is None:
        return ""
    try:
        return await resolver(ctx) or ""
    except LastFmError:
        return ""


OPTION_FORMATS = {"title": "<b>{}</b>", "description": "{}", "footer": "<i>{}</i>"}


def unknown_variables(template: str) -> list[str]:
    return sorted({name for name in VARIABLE_RE.findall(template) if name not in VARIABLES})


async def render(template: str, ctx: TemplateContext) -> str:
    names = set(VARIABLE_RE.findall(template))
    values = dict(zip(names, await asyncio.gather(*(_value(n, ctx) for n in names))))

    lines = []
    for raw in template.replace("$$fm-template", "").strip().split("\n"):
        line, wrapper = raw.rstrip(), "{}"
        if line.startswith("$$"):
            option, _, rest = line[2:].partition(":")
            wrapper = OPTION_FORMATS.get(option.strip().lower())
            if wrapper is None:
                continue
            line = rest.strip()
        parts = VARIABLE_RE.split(line)  # text, var, text, var, ...
        variables = parts[1::2]
        if variables and not any(values.get(v) for v in variables):
            continue
        rendered = "".join(esc(p) if i % 2 == 0 else values.get(p, "") for i, p in enumerate(parts)).strip()
        if rendered:
            lines.append(wrapper.format(rendered))
    return "\n".join(lines)

