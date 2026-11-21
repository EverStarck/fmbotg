import asyncio

import httpx

from fmbot_tg.db import Database
from fmbot_tg.lastfm import LastFmClient
from fmbot_tg.metadata import (
    Metadata, by_genre, clean_tags, flag, match_country, pick_musicbrainz_artist,
)


def test_tags_drop_noise_nationalities_decades_and_spelling_variants():
    tags = [("alternative", 100), ("seen live", 90), ("British", 80), ("Hip Hop", 60), ("hip-hop", 55),
            ("90s", 50), ("80's", 45), ("art rock", 40), ("electronic", 5)]
    assert clean_tags(tags) == ["alternative", "hip hop", "art rock"]


def test_genre_totals_merge_spellings_and_count_each_artist_once_per_genre():
    rows = [("Radiohead", 100), ("Kendrick Lamar", 50)]
    genres = {"radiohead": ["alternative", "art rock"], "kendrick lamar": ["hip-hop", "alternative"]}
    assert by_genre(rows, genres) == [("alternative", 150, ["Radiohead", "Kendrick Lamar"]),
                                      ("art rock", 100, ["Radiohead"]), ("hip-hop", 50, ["Kendrick Lamar"])]


def test_musicbrainz_needs_an_exact_name_and_skips_namesakes_without_a_country():
    results = [
        {"name": "Nirvana", "score": 100},  # no country: don't guess from the next one
        {"name": "Nirvana", "score": 100, "country": "GB", "area": {"type": "Country", "name": "United Kingdom"}},
    ]
    assert pick_musicbrainz_artist("nirvana", results) == ("GB", "United Kingdom")
    assert pick_musicbrainz_artist("Nirvana UK", results) is None
    # Area is a city: fall back to the code
    city = [{"name": "Björk", "score": 100, "country": "IS", "area": {"type": "City", "name": "Reykjavík"}}]
    assert pick_musicbrainz_artist("BJÖRK", city) == ("IS", "IS")


def test_countries_match_codes_names_aliases_and_prefixes():
    known = [("GB", "United Kingdom"), ("US", "United States"), ("JP", "Japan")]
    assert match_country("uk", known) == ("GB", "United Kingdom")
    assert match_country("jp", known) == ("JP", "Japan")
    assert match_country("united s", known) == ("US", "United States")
    assert match_country("un", known) is None  # too short to guess
    assert flag("GB") == "🇬🇧" and flag("XW") == ""


async def test_lookups_are_cached_and_failures_retried(tmp_path):
    calls = []

    def lastfm_api(request):
        artist = request.url.params["artist"]
        calls.append(artist)
        if artist == "Flaky" and calls.count("Flaky") == 1:
            return httpx.Response(200, json={"error": 10, "message": "Invalid API key"})
        return httpx.Response(200, json={"toptags": {"tag": [{"name": "shoegaze", "count": 100}]}})

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    await db.create_schema()
    lastfm = LastFmClient("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(lastfm_api)))
    metadata = Metadata(db, lastfm, lastfm.http, "test")
    try:
        first = await metadata.genres(["Slowdive", "slowdive", "Flaky"])
        assert first.values == {"slowdive": ["shoegaze"]} and first.pending == 1
        second = await metadata.genres(["Slowdive", "Flaky"])
        assert second.values == {"slowdive": ["shoegaze"], "flaky": ["shoegaze"]} and second.pending == 0
        assert calls.count("Slowdive") + calls.count("slowdive") == 1  # cached in the database
        # Nothing new to fetch: answers without waiting
        assert (await asyncio.wait_for(metadata.genres(["Slowdive"]), 1)).pending == 0
    finally:
        await metadata.close()
        await db.close()
