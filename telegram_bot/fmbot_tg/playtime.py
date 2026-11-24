"""Listening time from play counts and track lengths (port of TimeService / PlayService playtime).

FMBot gets lengths from Spotify; here they come from Last.fm, then Deezer. Plays of tracks whose
length is unknown count as the average known length, like FMBot's fallback.
"""

from dataclasses import dataclass

from .names import name_key
from .formatting import plural
from .metadata import Metadata

DEFAULT_TRACK_MS = 210_000  # 3:30, when no length is known at all


@dataclass(frozen=True)
class TrackTime:
    artist: str
    track: str
    plays: int
    ms: int


@dataclass(frozen=True)
class ListeningTime:
    total_ms: int
    items: list[TrackTime]
    plays: int
    estimated_plays: int  # plays counted at the average length
    pending: int  # tracks still being looked up


async def listening_time(metadata: Metadata, counts: list[tuple[str, str, int]], wait: float = 10.0
                         ) -> ListeningTime:
    """`counts` is (artist, track, plays); the most played tracks are looked up first."""
    counts = sorted(counts, key=lambda c: -c[2])
    durations, pending = await metadata.durations([(a, t) for a, t, _ in counts], wait=wait)
    known = [(durations[(name_key(a), name_key(t))], p) for a, t, p in counts if (name_key(a), name_key(t)) in durations]
    known_plays = sum(p for _, p in known)
    average = sum(d * p for d, p in known) // known_plays if known_plays else DEFAULT_TRACK_MS

    items, estimated = [], 0
    for artist, track, plays in counts:
        length = durations.get((name_key(artist), name_key(track)))
        if length is None:
            estimated += plays
        items.append(TrackTime(artist, track, plays, (length or average) * plays))
    return ListeningTime(sum(i.ms for i in items), items, sum(p for _, _, p in counts), estimated, pending)


def hours(ms: int) -> str:
    """'3d 4h', '12h 05m' or '45m'."""
    minutes = ms // 60_000
    days, rest = divmod(minutes, 24 * 60)
    h, m = divmod(rest, 60)
    if days:
        return f"{days}d {h}h"
    return f"{h}h {m:02}m" if h else f"{m}m"


def estimate_note(result: ListeningTime) -> str | None:
    if not result.estimated_plays:
        return None
    note = f"{plural(result.estimated_plays, 'play')} counted at the average track length"
    if result.pending:
        note += f"; still looking up {plural(result.pending, 'track')}, so it gets more exact if you run it again"
    return note + "."
