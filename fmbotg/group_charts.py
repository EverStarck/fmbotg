"""Top artists/albums/tracks across a group's imported histories (port of the server charts),
shared by the /group* commands and scheduled autoposts."""

from .db import Database
from .domain import UserFacingError
from .formatting import TELEGRAM_TEXT_LIMIT, esc, link, plural, truncate
from .members import Members
from .periods import Period
from .stats import music_url

GROUP_TOP_SHOWN = 15


async def group_top_text(db: Database, members: Members, chat_id: int, chat_title: str, kinds: list[str],
                         period: Period, shown: int = GROUP_TOP_SHOWN) -> str:
    """Top artists/albums/tracks across a group's imported histories (port of the server charts).
    Several kinds make a recap with one section each."""
    people = await members.require_indexed(chat_id, "Group charts")
    from_ts, to_ts = period.bounds()
    title = "Top " + kinds[0] if len(kinds) == 1 else "Recap"
    lines = [f"<b>{title} in {esc(chat_title)}</b> · {esc(period.label)}"]
    found = False
    for kind in kinds:
        rows = await db.group_top([m.id for m in people], kind, from_ts, to_ts, limit=shown)
        found = found or bool(rows)
        lines.append("" if len(kinds) == 1 else f"\n<b>Top {kind}</b>")
        for i, (name, artist, plays, listeners) in enumerate(rows, 1):
            by = f" — {esc(artist)}" if artist else ""
            lines.append(f"{i}. {link(name, music_url(artist or name, name, kind))}{by} · "
                         f"<b>{plural(plays, 'play')}</b> · {plural(listeners, 'listener')}")
    if not found:
        raise UserFacingError(f"No plays here for {esc(period.label)}.")
    lines += ["", f"<code>From {plural(len(people), 'member')} with an imported history</code>"]
    return truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT)
