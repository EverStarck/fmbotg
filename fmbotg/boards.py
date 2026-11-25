"""Who-knows style leaderboards: ranked people with play counts (shared by every who knows variant)."""

import html
from dataclasses import dataclass

from .formatting import esc, lastfm_user_url, num, plural

SHOWN = 15


@dataclass(frozen=True)
class Entry:
    name: str
    lastfm_username: str | None  # None hides the profile link (private users in global who knows)
    plays: int
    is_caller: bool = False
    crown: bool = False


def _line(position: int, entry: Entry) -> str:
    name = esc(entry.name)
    if entry.lastfm_username:
        name = f'<a href="{html.escape(lastfm_user_url(entry.lastfm_username))}">{name}</a>'
    if entry.is_caller:
        name = f"<b>{name}</b>"
    return f"{'👑' if entry.crown else f'{position}.'} {name} · <b>{plural(entry.plays, 'play')}</b>"


def render_board(heading: str, entries: list[Entry], failed: int = 0, notes: list[str] = ()) -> str:
    """Entries must be sorted by plays, highest first, and only include listeners."""
    lines = [heading, ""]
    lines += [_line(position, entry) for position, entry in enumerate(entries[:SHOWN], 1)]
    caller = next(((i, e) for i, e in enumerate(entries, 1) if e.is_caller), None)
    if caller and caller[0] > SHOWN:
        lines += ["…", _line(*caller)]

    total = sum(e.plays for e in entries)
    footer = f"{plural(len(entries), 'listener')} · {plural(total, 'play')} · {num(total // len(entries))} avg"
    if failed:
        footer += f" · {failed} couldn't be fetched"
    lines += ["", f"<code>{footer}</code>"]
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)
