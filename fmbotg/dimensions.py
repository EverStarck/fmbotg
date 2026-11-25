"""Genres and countries as one idea: a way to group artists (port of GenreService and CountryService).

Every command that works per genre also works per country (top list, one item, who knows, the
recap pages), so each is written once against a `Dimension`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .domain import Target
from .formatting import esc, plural
from .metadata import Lookup, Metadata, flag, match_country
from .names import genre_key, name_key
from .periods import Period

if TYPE_CHECKING:
    from .stats import Stats


@dataclass(frozen=True)
class Category:
    """One genre or country."""

    key: str  # genre key ("hiphop") or ISO country code ("GB")
    label: str  # "hip hop", "United Kingdom"
    icon: str = ""  # a flag for countries

    @property
    def title(self) -> str:
        return f"{self.icon} {self.label}".strip()


@dataclass(frozen=True)
class Dimension:
    name: str  # "genre" or "country"
    plural: str
    top_artists: int  # how many top artists a breakdown is based on
    exclusive: bool  # an artist has one country but many genres, so shares only add up for countries
    example: str
    single_wait: float  # seconds to wait when looking up one artist (MusicBrainz is slower)
    _lookup: Callable[..., Awaitable[Lookup]]
    _categories: Callable[[object], list[Category]]

    async def lookup(self, metadata: Metadata, artists: list[str], wait: float | None = None) -> Lookup:
        return await self._lookup(metadata, artists, wait)

    def categories(self, value) -> list[Category]:
        """An artist's looked-up value (genre list, (code, name) or None) as categories."""
        return self._categories(value)

    def aggregate(self, rows: list[tuple[str, int]], values: dict) -> list[tuple[Category, int, list[str]]]:
        """Artist playcounts -> (category, plays, artists) per category, most played first. Like FMBot, an
        artist's plays count fully towards each of its genres."""
        totals: dict[str, list] = {}
        for artist, plays in rows:
            for category in self.categories(values.get(name_key(artist))):
                entry = totals.setdefault(category.key, [category, 0, []])
                entry[1] += plays
                entry[2].append(artist)
        return sorted((tuple(e) for e in totals.values()), key=lambda e: (-e[1], e[0].label))

    def find(self, query: str, known: list[Category]) -> Category | None:
        """What someone typed ("hip-hop", "uk", "japan") among the categories in their artists."""
        if self.name == "genre":
            wanted = genre_key(query)
            return next((c for c in known if c.key == wanted), None)
        found = match_country(query, [(c.key, c.label) for c in known])
        return next((c for c in known if found and c.key == found[0]), None)

    def guess(self, query: str) -> Category | None:
        """A category nobody's artists are known to have yet: any genre name works, countries must be known."""
        return Category(genre_key(query), query.lower()) if self.name == "genre" else None

    def describe(self, artist: str, categories: list[Category]) -> str:
        if self.name == "genre":
            return f"<b>{esc(artist)}</b>: " + ", ".join(esc(c.label) for c in categories)
        return f"<b>{esc(artist)}</b> is from <b>{esc(categories[0].title)}</b>"

    def list_title(self, whose: str, category: Category) -> str:
        if self.name == "genre":
            return f"<b>{whose} top {esc(category.label)} artists</b>"
        return f"<b>{whose} top artists from {esc(category.title)}</b>"


async def _genres(metadata: Metadata, artists: list[str], wait: float | None) -> Lookup:
    return await metadata.genres(artists, **({"wait": wait} if wait is not None else {}))


async def _countries(metadata: Metadata, artists: list[str], wait: float | None) -> Lookup:
    # MusicBrainz allows one lookup per second, so only the top artists are queued
    return await metadata.countries(artists, max_new=100, **({"wait": wait} if wait is not None else {}))


GENRE = Dimension("genre", "genres", 200, False, "shoegaze", 10, _genres,
                  lambda value: [Category(genre_key(g), g) for g in value or []])
COUNTRY = Dimension("country", "countries", 100, True, "japan", 15, _countries,
                    lambda value: [Category(value[0], value[1], flag(value[0]))] if value else [])
DIMENSIONS = {"genre": GENRE, "country": COUNTRY}


# --- shared rendering -------------------------------------------------------------------------

def pending_note(lookup: Lookup, dimension: Dimension) -> str | None:
    if not lookup.pending:
        return None
    return (f"<i>Still looking up {dimension.plural} for {plural(lookup.pending, 'artist')}; "
            "run it again in a minute for a fuller picture.</i>")


def category_line(position: int, category: Category, plays: int, artists: list[str], share: float | None) -> str:
    percent = f" ({share:.0%})" if share is not None else ""
    names = ", ".join(esc(a) for a in artists[:3])
    return f"{position}. <b>{esc(category.title)}</b> · {plural(plays, 'play')}{percent} · <i>{names}</i>"


def category_lines(dimension: Dimension, groups: list[tuple[Category, int, list[str]]], limit: int) -> list[str]:
    """Ranked categories; shares only for countries, where each artist counts once."""
    total = sum(plays for _, plays, _ in groups)
    return [category_line(i, category, plays, artists, plays / total if dimension.exclusive and total else None)
            for i, (category, plays, artists) in enumerate(groups[:limit], 1)]


async def artist_rows(stats: "Stats", target: Target, period: Period, limit: int) -> list[tuple[str, int]]:
    top = await stats.top(target, "artists", period, page=1, page_size=limit)
    return [(item.name, item.playcount) for item in top.items]
