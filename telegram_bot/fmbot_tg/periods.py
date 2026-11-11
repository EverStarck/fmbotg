"""Time period parsing, mirroring the aliases in FMBot.Core/TimePeriodParser.cs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Period:
    key: str  # one-letter key, used in callback data
    api: str  # Last.fm `period` parameter
    label: str


WEEKLY = Period("w", "7day", "weekly")
MONTHLY = Period("m", "1month", "monthly")
QUARTERLY = Period("q", "3month", "quarterly")
HALF = Period("h", "6month", "half-yearly")
YEARLY = Period("y", "12month", "yearly")
ALL_TIME = Period("o", "overall", "all-time")

BY_KEY = {p.key: p for p in (WEEKLY, MONTHLY, QUARTERLY, HALF, YEARLY, ALL_TIME)}

_ALIASES: dict[str, Period] = {}
for period, words in (
    (WEEKLY, ["weekly", "week", "w", "7d"]),
    (MONTHLY, ["monthly", "month", "m", "1m", "30d"]),
    (QUARTERLY, ["quarterly", "quarter", "q", "3m", "90d"]),
    (HALF, ["half-yearly", "halfyearly", "half", "h", "6m", "180d"]),
    (YEARLY, ["yearly", "year", "y", "12m", "365d", "1y"]),
    (ALL_TIME, ["overall", "alltime", "all-time", "all", "a", "o", "at"]),
):
    for word in words:
        _ALIASES[word] = period

# Supported by FMBot through its own play index; needs the phase 2 scrobble importer here.
_NOT_YET_SUPPORTED = {
    "1-day", "1day", "1d", "24h", "today", "day", "daily", "yesterday", "yd",
    "2d", "3d", "4d", "5d", "6d", "2-day", "3-day", "4-day", "5-day", "6-day",
    "two-year", "twoyear", "2y", "24m", "730d",
}


class UnsupportedPeriod(ValueError):
    pass


def parse_period(args: list[str], default: Period = WEEKLY) -> tuple[Period, list[str]]:
    """Pulls the first period word out of `args`. Returns (period, remaining args)."""
    for index, raw in enumerate(args):
        word = raw.lower()
        if word in _ALIASES:
            return _ALIASES[word], args[:index] + args[index + 1:]
        if word in _NOT_YET_SUPPORTED:
            raise UnsupportedPeriod(
                f"'{raw}' isn't supported yet. Use weekly, monthly, quarterly, half, yearly or alltime."
            )
    return default, list(args)
