import httpx

from fmbot_tg import deezer
from fmbot_tg.db import Database
from fmbot_tg.handlers.music import iceberg_sections
from fmbot_tg.lastfm import LastFmClient
from fmbot_tg.metadata import Metadata
from fmbot_tg.playtime import hours, listening_time


def test_iceberg_bands_by_listeners_with_five_most_played_each():
    rows = [(f"Big {i}", 100 - i) for i in range(7)] + [("Small", 50), ("Unknown", 80)]
    listeners = {f"big {i}": 5_000_000 for i in range(7)} | {"small": 300}
    sections = iceberg_sections(rows, listeners)
    assert sections[0] == ("3M+ listeners", ["Big 0", "Big 1", "Big 2", "Big 3", "Big 4"])
    assert sections[-1] == ("Under 1k", ["Small"])
    assert all(names == [] for _, names in sections[1:-1])  # "Unknown" has no listener count yet


def test_hours_formatting():
    assert hours(45 * 60_000) == "45m"
    assert hours(12 * 3_600_000 + 5 * 60_000) == "12h 05m"
    assert hours(76 * 3_600_000) == "3d 4h"


async def test_listening_time_uses_lastfm_then_deezer_and_averages_the_unknown(tmp_path):
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.deezer.com":
            if "Obscure" in request.url.params["q"]:
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"data": [
                {"title": "Song B (Live)", "artist": {"name": "Band"}, "duration": 500, "link": "l1"},
                {"title": "Song B", "artist": {"name": "Band"}, "duration": 200, "link": "l2"}]})
        track = request.url.params["track"]
        return httpx.Response(200, json={"track": {"name": track, "duration": "100000" if track == "Song A" else "0"}})

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    await db.create_schema()
    lastfm = LastFmClient("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(api)))
    metadata = Metadata(db, lastfm, lastfm.http, "test")
    try:
        result = await listening_time(metadata, [("Band", "Song A", 3), ("Band", "Song B", 1), ("Obscure", "X", 2)])
        # A: 100s from Last.fm; B: 200s from Deezer's exact title, not the live version
        # X: unknown, counted at the play-weighted average (3*100 + 1*200) / 4 = 125s
        assert result.total_ms == 3 * 100_000 + 200_000 + 2 * 125_000
        assert result.estimated_plays == 2 and result.pending == 0
        assert (await db.track_meta([("band", "song b")]))[("band", "song b")].deezer_url == "l2"
    finally:
        await metadata.close()
        await db.close()


async def test_deezer_ignores_other_artists_and_placeholder_photos():
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"name": "Muse Tribute", "picture_xl": "https://x/artist/abc/1.jpg"},
            {"name": "Muse", "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist//1000x1000.jpg"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http:
        assert await deezer.artist_photo(http, "muse") is None
