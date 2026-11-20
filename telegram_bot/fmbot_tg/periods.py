"""Time period parsing, mirroring the aliases in FMBot.Core/TimePeriodParser.cs.

Periods either map onto a Last.fm `period` (weekly … all-time) or are a time range that is
answered from the play index (today, 2-day, two-year, a calendar year …).
Calendar boundaries are UTC until per-user time zones exist.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FIRST_SCROBBLE_YEAR = 2002


@dataclass(frozen=True)
class Period:
    key: str  # short key, used in callback data
    label: str
    api: str | None = None  # Last.fm `period` parameter; None for time ranges
    days: int | None = None  # rolling window
    calendar: str | None = None  # "today" | "yesterday"
    year: int | None = None

    @property
    def is_range(self) -> bool:
        return self.api is None

    def bounds(self, now: datetime | None = None) -> tuple[int, int]:
        """(from, to) unix timestamps, `to` exclusive."""
        now = now or datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.calendar == "today":
            start, end = midnight, now
        elif self.calendar == "yesterday":
            start, end = midnight - timedelta(days=1), midnight
        elif self.year is not None:
            start = datetime(self.year, 1, 1, tzinfo=timezone.utc)
            end = min(datetime(self.year + 1, 1, 1, tzinfo=timezone.utc), now)
        elif self.days is not None:
            start, end = now - timedelta(days=self.days), now
        else:
            raise ValueError(f"{self.key} is not a time range")
        return int(start.timestamp()), int(end.timestamp())


WEEKLY = Period("w", "weekly", api="7day")
MONTHLY = Period("m", "monthly", api="1month")
QUARTERLY = Period("q", "quarterly", api="3month")
HALF = Period("h", "half-yearly", api="6month")
YEARLY = Period("y", "yearly", api="12month")
ALL_TIME = Period("o", "all-time", api="overall")

TODAY = Period("d", "today (UTC)", calendar="today")
YESTERDAY = Period("yd", "yesterday (UTC)", calendar="yesterday")
ONE_DAY = Period("1", "last 24 hours", days=1)
TWO_YEAR = Period("2y", "two-year", days=730)
ROLLING_DAYS = {n: Period(str(n), f"{n}-day", days=n) for n in range(2, 7)}

_FIXED = [WEEKLY, MONTHLY, QUARTERLY, HALF, YEARLY, ALL_TIME, TODAY, YESTERDAY, ONE_DAY, TWO_YEAR,
          *ROLLING_DAYS.values()]
BY_KEY = {p.key: p for p in _FIXED}

_ALIASES: dict[str, Period] = {}
for period, words in (
    (WEEKLY, ["weekly", "week", "w", "7d"]),
    (MONTHLY, ["monthly", "month", "m", "1m", "30d"]),
    (QUARTERLY, ["quarterly", "quarter", "q", "3m", "90d"]),
    (HALF, ["half-yearly", "halfyearly", "half", "h", "6m", "180d"]),
    (YEARLY, ["yearly", "year", "y", "12m", "365d", "1y"]),
    (ALL_TIME, ["overall", "alltime", "all-time", "all", "a", "o", "at"]),
    (TODAY, ["today", "day", "daily"]),
    (YESTERDAY, ["yesterday", "yd"]),
    (ONE_DAY, ["1-day", "1day", "1d", "24h", "24-h", "24hr", "24-hr", "24hours"]),
    (TWO_YEAR, ["two-year", "twoyear", "two-yearly", "twoyearly", "2y", "2year", "24m", "730d"]),
):
    for word in words:
        _ALIASES[word] = period
for n, period in ROLLING_DAYS.items():
    for word in (f"{n}-day", f"{n}day", f"{n}d"):
        _ALIASES[word] = period

_YEAR_RE = re.compile(r"^(?:y)?(\d{4})$")


def year_period(year: int) -> Period:
    return Period(f"y{year}", str(year), year=year)


def _parse_year(word: str) -> Period | None:
    match = _YEAR_RE.match(word)
    if not match:
        return None
    year = int(match[1])
    if FIRST_SCROBBLE_YEAR <= year <= datetime.now(timezone.utc).year:
        return year_period(year)
    return None


def period_from_key(key: str) -> Period | None:
    if key in BY_KEY:
        return BY_KEY[key]
    return _parse_year(key) if key.startswith("y") else None


def parse_period(args: list[str], default: Period = WEEKLY) -> tuple[Period, list[str]]:
    """Pulls the first period word out of `args`. Returns (period, remaining args)."""
    for index, raw in enumerate(args):
        word = raw.lower()
        period = _ALIASES.get(word) or _parse_year(word)
        if period:
            return period, args[:index] + args[index + 1:]
    return default, list(args)
