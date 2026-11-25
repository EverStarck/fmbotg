import html
import re
import time
from urllib.parse import quote

TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def num(value: int) -> str:
    return f"{value:,}"


def plural(count: int, word: str, plural_word: str | None = None) -> str:
    return f"{num(count)} {word if count == 1 else (plural_word or word + 's')}"


def link(text: str, url: str) -> str:
    return f'<a href="{html.escape(url)}">{esc(text)}</a>'


def lastfm_user_url(username: str) -> str:
    return f"https://www.last.fm/user/{quote(username)}"


def user_link(username: str) -> str:
    return link(username, lastfm_user_url(username))


def ago(timestamp: int | None, now: float | None = None) -> str:
    if not timestamp:
        return ""
    seconds = int((now or time.time()) - timestamp)
    if seconds < 60:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            return f"{plural(seconds // size, unit)} ago"
    return ""


def duration(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


_TAG_RE = re.compile(r"<[^>]+>")
_READ_MORE_RE = re.compile(r"<a href=\"https://www\.last\.fm[^\"]*\">Read more on Last\.fm</a>\.?", re.I)


def clean_wiki(summary: str | None, limit: int = 500) -> str:
    if not summary:
        return ""
    text = _TAG_RE.sub("", _READ_MORE_RE.sub("", summary)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return esc(text)


def truncate(text: str, limit: int) -> str:
    """Cut HTML text at a line boundary so tags aren't split."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit("\n", 1)[0]
    return cut + "\n…"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{num(n)}{suffix}"


def span(days: float) -> str:
    """'3 days', or '5 hours' / '20 minutes' for spans shorter than a day."""
    if days >= 1:
        return plural(round(days), "day")
    hours = days * 24
    return plural(round(hours), "hour") if hours >= 1 else plural(max(1, round(hours * 60)), "minute")


def ranked_items(items, bold_plays: bool = False) -> list[str]:
    """'1. Name — Artist · 12' lines for top list items (anything with name, artist, url, playcount)."""
    lines = []
    for i, item in enumerate(items, 1):
        by = f" — {esc(item.artist)}" if item.artist else ""
        plays = f"<b>{num(item.playcount)}</b>" if bold_plays else num(item.playcount)
        lines.append(f"{i}. {link(item.name, item.url)}{by} · {plays}")
    return lines
