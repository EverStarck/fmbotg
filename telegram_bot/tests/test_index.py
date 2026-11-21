import asyncio

import httpx
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from fmbot_tg.db import Base, Database
from fmbot_tg.indexer import Indexer
from fmbot_tg.lastfm import LastFmClient
from fmbot_tg.migrate import upgrade
from fmbot_tg.stats import aggregate


class FakeHistory:
    """Serves user.getRecentTracks like Last.fm: newest first, paged, honouring from/to."""

    def __init__(self, plays: list[tuple[int, str, str, str]], now_playing: tuple[str, str] | None = None):
        self.plays = plays  # (uts, artist, album, track)
        self.now_playing = now_playing
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        params = request.url.params
        limit, page = int(params["limit"]), int(params["page"])
        low, high = int(params.get("from", 0)), int(params.get("to", 2**40))
        matching = sorted((p for p in self.plays if low <= p[0] <= high), reverse=True)
        chunk = matching[(page - 1) * limit: page * limit]
        tracks = [{"name": t, "artist": {"#text": a}, "album": {"#text": al}, "url": "", "image": [],
                   "date": {"uts": str(ts)}} for ts, a, al, t in chunk]
        if self.now_playing and page == 1:
            tracks.insert(0, {"name": self.now_playing[1], "artist": {"#text": self.now_playing[0]},
                              "album": {"#text": ""}, "url": "", "image": [], "@attr": {"nowplaying": "true"}})
        pages = max(1, -(-len(matching) // limit))
        return httpx.Response(200, json={"recenttracks": {
            "@attr": {"total": str(len(matching)), "page": str(page), "totalPages": str(pages)}, "track": tracks}})


async def _setup(tmp_path, history: FakeHistory):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'index.db'}")
    await db.create_schema()
    user = await db.link_lastfm(1, "alice", "sk")
    lastfm = LastFmClient("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(history)))
    return db, lastfm, Indexer(db, lastfm), user


async def test_full_import_pages_skips_now_playing_and_updates_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr("fmbot_tg.indexer.PAGE_SIZE", 2)
    history = FakeHistory(
        [(100 + i, "Radiohead" if i % 2 else "radiohead", "Kid A", f"T{i}") for i in range(5)],
        now_playing=("Radiohead", "Live"),
    )
    db, lastfm, indexer, user = await _setup(tmp_path, history)

    assert await indexer.run_full_import(user.id) == 5  # 3 pages; now playing isn't a scrobble
    assert (await db.get_user_by_id(user.id)).last_scrobble_ts == 104
    # Case variants of the artist count together
    assert await db.playcounts("artist", ("RADIOHEAD",), [user.id]) == {user.id: 5}

    history.plays += [(200, "Björk", "Homogenic", "Jóga"), (201, "Björk", "Homogenic", "Bachelorette")]
    assert await indexer.update(user.id) == 2
    assert await indexer.update(user.id) == 0  # nothing new, and no duplicates
    assert await db.play_count(user.id) == 7

    top = await db.top_from_plays(user.id, "artists", 0, 10_000)
    assert [(r.name.lower(), r.playcount) for r in top] == [("radiohead", 5), ("björk", 2)]
    assert [r.name for r in await db.top_from_plays(user.id, "tracks", 200, 202)] == ["Bachelorette", "Jóga"]
    await lastfm.close()
    await db.close()


async def test_switching_lastfm_account_drops_the_old_index(tmp_path):
    db, lastfm, indexer, user = await _setup(tmp_path, FakeHistory([(100, "A", "B", "C")]))
    await indexer.run_full_import(user.id)
    relinked = await db.link_lastfm(1, "someone_else", "sk2")
    assert relinked.indexed_at is None and await db.play_count(user.id) == 0
    await lastfm.close()
    await db.close()


def test_live_aggregate_skips_albumless_plays_for_album_lists():
    plays = [("A", None, "x"), ("A", "Alb", "y"), ("a", "alb", "z")]
    rows = aggregate(plays, "albums")
    assert [(r.name, r.artist, r.playcount) for r in rows] == [("Alb", "A", 2)]


def test_migrations_match_models(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"
    upgrade(url)

    async def diff():
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            result = await conn.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata))
        await engine.dispose()
        return result

    assert asyncio.run(diff()) == []


def test_phase1_database_is_stamped_then_upgraded(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}"

    async def create_phase1_tables():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(
                c, tables=[Base.metadata.tables["users"], Base.metadata.tables["chat_members"]]))
            # Remove every column added after phase 1
            for column in ("indexed_at", "updated_at", "last_scrobble_ts", "timezone", "fm_mode",
                           "fm_template"):
                await conn.exec_driver_sql(f"ALTER TABLE users DROP COLUMN {column}")
        await engine.dispose()

    asyncio.run(create_phase1_tables())
    upgrade(url)  # must not fail with "table users already exists"
    upgrade(url)  # and is idempotent


async def test_discoveries_only_include_artists_first_played_in_the_range(tmp_path):
    history = FakeHistory([(100, "Old", "A", "x"), (500, "Old", "A", "y"), (600, "New", "B", "z"),
                           (700, "new", "B", "z"), (800, "Newer", "C", "w")])
    db, lastfm, indexer, user = await _setup(tmp_path, history)
    await indexer.run_full_import(user.id)

    rows = await db.discoveries(user.id, 400, 1000)
    assert [(a.lower(), first, plays) for a, first, plays in rows] == [("new", 600, 2), ("newer", 800, 1)]
    assert await db.count_plays(user.id, 400, 1000) == 4
    assert await db.track_counts(user.id, "NEW", ["Z", "missing"]) == {"z": 2}
    await lastfm.close()
    await db.close()


async def test_gaps_report_the_first_long_break_and_search_matches_substrings(tmp_path):
    day = 86400
    t0 = 1_000_000  # any real timestamp; 0 means "no timestamp" in Last.fm data
    history = FakeHistory([
        (t0, "Comeback", "A", "x"), (t0 + 1 * day, "Comeback", "A", "x"), (t0 + 200 * day, "Comeback", "A", "x"),
        (t0 + 600 * day, "Comeback", "A", "x"),  # a second, longer gap: FMBot reports the first one
        (t0, "Steady", "B", "y"), (t0 + 10 * day, "Steady", "B", "y"), (t0 + 20 * day, "Steady", "B", "y"),
        (t0, "Rare", "C", "z"), (t0 + 300 * day, "Rare", "C", "z"),  # long gap, but under 3 plays
    ])
    db, lastfm, indexer, user = await _setup(tmp_path, history)
    await indexer.run_full_import(user.id)

    gaps = await db.listening_gaps(user.id, "artists", min_plays=3, min_gap_seconds=90 * day)
    assert [(name, resumed, gap // day, total) for name, _, resumed, gap, total in gaps] == \
        [("Comeback", t0 + 200 * day, 199, 4)]
    # Tracks only need one play before the gap
    assert {g[0] for g in await db.listening_gaps(user.id, "tracks", 1, 90 * day)} == {"x", "z"}

    assert [r.name for r in await db.search_library(user.id, "artists", "EAD")] == ["Steady"]
    assert await db.search_library(user.id, "artists", "%") == []  # wildcards are literal
    await lastfm.close()
    await db.close()


async def test_saved_streak_is_updated_in_place_and_survives_reimport(tmp_path):
    from fmbot_tg.db import SavedStreak

    db, lastfm, indexer, user = await _setup(tmp_path, FakeHistory([(100, "A", "B", "C")]))

    def streak(count):
        return SavedStreak(artist="A", album="B", track="C", artist_count=count, album_count=count,
                           track_count=count, started=50, ended=100 + count)

    assert await db.save_streak(user.id, streak(25)) is True
    assert await db.save_streak(user.id, streak(30)) is False
    await indexer.run_full_import(user.id)
    saved = await db.saved_streaks(user.id)
    assert [(s.artist_count, s.ended) for s in saved] == [(30, 130)]

    await db.link_lastfm(1, "another_account", "sk2")
    assert await db.saved_streaks(user.id) == []
    await lastfm.close()
    await db.close()


async def test_billboard_compares_with_the_previous_window(tmp_path):
    import time

    from fmbot_tg.context import Target
    from fmbot_tg.periods import WEEKLY
    from fmbot_tg.stats import Stats

    now, day = int(time.time()), 86400
    def plays(count, artist, days_ago):
        return [(int(now - days_ago * day) + i, artist, artist, artist) for i in range(count)]

    history = FakeHistory(
        # previous week (7-14 days ago)
        plays(1, "Old", 9) + plays(1, "Riser", 10) + plays(3, "Faller", 8)
        # this week
        + plays(5, "Riser", 2) + plays(2, "Faller", 1) + plays(1, "Newcomer", 0.1)
    )
    db, lastfm, indexer, user = await _setup(tmp_path, history)
    await indexer.run_full_import(user.id)
    target = Target.from_user(await db.get_user_by_id(user.id), True)

    top = await Stats(db, lastfm, indexer).top(target, "artists", WEEKLY, page=1, page_size=10, billboard=True)
    # Previous week: Faller (3) #1, Old (1) / Riser (1) tied after it
    assert [(i.name, i.playcount, i.previous_rank) for i in top.items] == [
        ("Riser", 5, 3), ("Faller", 2, 1), ("Newcomer", 1, 0)]
    await lastfm.close()
    await db.close()
