"""Pure play-statistics logic (ports of SettingService.GetGoalAmount/GetMilestoneAmount and
PlayService.GetCurrentStreak), kept free of I/O so it can be tested directly."""

import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Iterable

from .names import name_key

# Constants.PlayCountBreakPoints
BREAKPOINTS = [
    50, 100, 250, 420, 500, 1000, 1337, 2500, 5000, 10000, 25000, 50000, 100000, 150000, 200000, 250000,
    300000, 350000, 400000, 450000, 500000, 600000, 700000, 800000, 900000, 1000000, 2000000, 3000000,
    4000000, 5000000,
]
MAX_GOAL = 10_000_000
STREAK_SAVE_THRESHOLD = 25  # Constants.StreakSaveThreshold


def _parse_amount(option: str) -> int | None:
    option = option.lower().strip("()*`").replace(",", "").replace(".", "")
    try:
        return int(option[:-1]) * 1000 if option.endswith("k") else int(option)
    except ValueError:
        return None


def goal_amount(args: list[str], current: int) -> tuple[int, list[str]]:
    """Pace goal: the first number above the current playcount, else the next breakpoint."""
    rest = list(args)
    for option in args:
        amount = _parse_amount(option)
        if amount is not None and amount > current:
            rest.remove(option)
            return min(amount, MAX_GOAL), rest
    goal = next((b for b in BREAKPOINTS if current < b), MAX_GOAL)
    return min(goal, MAX_GOAL), rest


def milestone_amount(args: list[str], current: int, rng: random.Random | None = None) -> tuple[int, bool, list[str]]:
    """Milestone: a number below the current playcount, `random`, or the last breakpoint passed.

    Returns (amount, is_random, remaining args).
    """
    rest = list(args)
    for option in args:
        if option.lower() in ("random", "rnd"):
            rest.remove(option)
            return (rng or random).randint(1, max(1, current - 1)), True, rest
        amount = _parse_amount(option)
        if amount is not None and 0 < amount < current:
            rest.remove(option)
            return amount, False, rest
    passed = [b for b in BREAKPOINTS if current > b]
    return (passed[-1] if passed else 1), False, rest


@dataclass(frozen=True)
class Play:
    played_at: int | None  # None for the now playing track
    artist: str
    album: str | None
    track: str


@dataclass
class Streak:
    """Consecutive plays of the current artist, album and track, counting backwards."""

    current: Play
    artist_count: int = 1
    album_count: int = 1
    track_count: int = 1
    started: int | None = None
    _alive: set[str] = field(default_factory=lambda: {"artist", "album", "track"})

    def __post_init__(self):
        self.started = self.current.played_at
        if not self.current.album:
            self.album_count = 0
            self._alive.discard("album")

    @property
    def alive(self) -> bool:
        return bool(self._alive)

    @property
    def worth_saving(self) -> bool:
        return max(self.artist_count, self.album_count, self.track_count) >= STREAK_SAVE_THRESHOLD

    def feed(self, previous: Iterable[Play]) -> bool:
        """Feed older plays, newest first. Returns whether any streak could still continue."""
        cur = self.current
        for play in previous:
            if not self._alive:
                break
            same_artist = name_key(play.artist) == name_key(cur.artist)
            matches = {
                "artist": same_artist,
                "album": bool(play.album) and name_key(play.album) == name_key(cur.album),
                "track": same_artist and name_key(play.track) == name_key(cur.track),
            }
            for kind in list(self._alive):
                if matches[kind]:
                    setattr(self, f"{kind}_count", getattr(self, f"{kind}_count") + 1)
                    if play.played_at is not None:
                        self.started = min(self.started or play.played_at, play.played_at)
                else:
                    self._alive.discard(kind)
        return self.alive


@dataclass(frozen=True)
class DayOverview:
    day: date
    plays: int
    top_artist: tuple[str, int] | None
    top_album: tuple[str, str, int] | None  # (album, artist, plays)
    top_track: tuple[str, str, int] | None  # (track, artist, plays)


def _top(counter: Counter, names: dict) -> tuple | None:
    if not counter:
        return None
    key, count = min(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return (*names[key], count)


def overview(plays: Iterable[Play], tz: tzinfo) -> list[DayOverview]:
    """Per local day: playcount and the most played artist, album and track. Newest day first."""
    days: dict[date, dict] = {}
    for play in plays:
        if play.played_at is None:
            continue
        day = datetime.fromtimestamp(play.played_at, tz).date()
        bucket = days.setdefault(day, {"n": 0, "a": Counter(), "b": Counter(), "t": Counter(), "names": {}})
        bucket["n"] += 1
        akey = name_key(play.artist)
        bucket["a"][akey] += 1
        bucket["names"].setdefault(("a", akey), (play.artist,))
        if play.album:
            bkey = (akey, name_key(play.album))
            bucket["b"][bkey] += 1
            bucket["names"].setdefault(("b", bkey), (play.album, play.artist))
        tkey = (akey, name_key(play.track))
        bucket["t"][tkey] += 1
        bucket["names"].setdefault(("t", tkey), (play.track, play.artist))

    result = []
    for day in sorted(days, reverse=True):
        b = days[day]
        names = b["names"]
        result.append(DayOverview(
            day=day,
            plays=b["n"],
            top_artist=_top(b["a"], {k: names[("a", k)] for k in b["a"]}),
            top_album=_top(b["b"], {k: names[("b", k)] for k in b["b"]}),
            top_track=_top(b["t"], {k: names[("t", k)] for k in b["t"]}),
        ))
    return result


@dataclass(frozen=True)
class TasteMatch:
    shared: list[tuple[str, int, int]]  # (name, plays for a, plays for b), most shared first
    similarity: float  # 0-100


def taste(a: list[tuple[str, int]], b: list[tuple[str, int]]) -> TasteMatch:
    """Compare two top artist lists (port of FMBot's taste command).

    Similarity is the overlap of both listening distributions: the sum over shared artists of the
    smaller of each user's share of plays. 100 means identical proportions, 0 nothing in common.
    """
    total_a = sum(count for _, count in a) or 1
    total_b = sum(count for _, count in b) or 1
    b_by_key = {name_key(name): count for name, count in b}
    shared = []
    overlap = 0.0
    for name, count_a in a:
        count_b = b_by_key.get(name_key(name))
        if count_b:
            shared.append((name, count_a, count_b))
            overlap += min(count_a / total_a, count_b / total_b)
    shared.sort(key=lambda row: (-min(row[1] / total_a, row[2] / total_b), row[0].lower()))
    return TasteMatch(shared, round(overlap * 100, 1))


MAX_PACE_DAYS = 365 * 200  # beyond this, "at this pace" stops meaning anything (and dates overflow)


def goal_date(now: datetime, remaining: int, per_day: float) -> datetime | None:
    """When a goal is reached at a steady pace, or None if that's more than 200 years away."""
    if per_day <= 0:
        return None
    days = remaining / per_day
    return None if days > MAX_PACE_DAYS else now + timedelta(days=days)
