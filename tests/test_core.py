import hashlib
from datetime import datetime, timezone

from fmbotg.charts import Tile, parse_size, render
from fmbotg.db import Database
from fmbotg.lastfm import parse_recent_tracks, parse_top, pick_image, sign
from fmbotg.periods import ALL_TIME, MONTHLY, ROLLING_DAYS, WEEKLY, YESTERDAY, parse_period, period_from_key


def test_period_is_pulled_out_of_args_wherever_it_is():
    assert parse_period(["@alice", "monthly"]) == (MONTHLY, ["@alice"])
    assert parse_period(["at", "x"]) == (ALL_TIME, ["x"])
    assert parse_period([]) == (WEEKLY, [])


def test_range_periods_and_years():
    now = datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)
    assert parse_period(["3d"])[0] == ROLLING_DAYS[3]
    start, end = YESTERDAY.bounds(now)
    assert datetime.fromtimestamp(start, timezone.utc) == datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert datetime.fromtimestamp(end, timezone.utc) == datetime(2026, 3, 1, tzinfo=timezone.utc)

    year, rest = parse_period(["2019", "@bob"])
    assert year.label == "2019" and rest == ["@bob"]
    assert period_from_key(year.key) == year
    # Not a year we can have scrobbles for, so it stays in the args
    assert parse_period(["1999"]) == (WEEKLY, ["1999"])


def test_signature_excludes_format_and_sorts_keys():
    params = {"token": "abc", "method": "auth.getSession", "api_key": "key", "format": "json"}
    expected = hashlib.md5(b"api_keykeymethodauth.getSessiontokenabcsecret").hexdigest()
    assert sign(params, "secret") == expected


def test_single_recent_track_is_returned_as_object_not_list():
    data = {"recenttracks": {
        "@attr": {"total": "1"},
        "track": {"name": "Idioteque", "artist": {"name": "Radiohead"}, "album": {"#text": "Kid A"},
                  "url": "u", "image": [], "@attr": {"nowplaying": "true"}},
    }}
    result = parse_recent_tracks(data)
    assert result.total == 1
    assert result.tracks[0].artist == "Radiohead"
    assert result.tracks[0].now_playing and result.tracks[0].timestamp is None


def test_top_albums_parses_nested_artist():
    data = {"topalbums": {"@attr": {"total": "2", "page": "1", "totalPages": "1"}, "album": [
        {"name": "OK Computer", "artist": {"name": "Radiohead"}, "playcount": "12", "url": "u", "image": []},
    ]}}
    top = parse_top(data, "albums")
    assert top.items[0].artist == "Radiohead" and top.items[0].playcount == 12


def test_placeholder_images_are_ignored():
    images = [{"size": "extralarge", "#text": "https://x/2a96cbd8b46e442fc41c2b86b821562f.png"}]
    assert pick_image(images) is None


def test_chart_size_bounds():
    assert parse_size("4x3") == (4, 3)
    assert parse_size("11x2") is None
    assert parse_size("0x5") is None
    assert parse_size("weekly") is None


def test_chart_renders_with_missing_covers():
    jpeg = render([Tile(None, "Album", "Artist")] * 4, 2, 2)
    assert jpeg[:2] == b"\xff\xd8"


async def test_whoknows_members_only_include_linked_users_in_that_chat(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.create_schema()
    await db.touch(1, "alice", "Alice", chat_id=-100)
    await db.touch(2, "bob", "Bob", chat_id=-100)
    await db.touch(3, "carol", "Carol", chat_id=-200)
    await db.link_lastfm(1, "alice_lfm", "sk1")
    await db.link_lastfm(3, "carol_lfm", "sk3")

    members = await db.chat_lastfm_users(-100, limit=10)
    assert [m.lastfm_username for m in members] == ["alice_lfm"]

    await db.remove_member(-100, 1)
    assert await db.chat_lastfm_users(-100, limit=10) == []
    assert (await db.get_user_by_handle("@Carol")).lastfm_username == "carol_lfm"
    await db.close()


def test_cover_url_drops_the_size_segment():
    from fmbotg.lastfm import full_size_image
    url = "https://lastfm.freetls.fastly.net/i/u/300x300/abc.jpg"
    assert full_size_image(url) == "https://lastfm.freetls.fastly.net/i/u/abc.jpg"


def test_nicknames_only_apply_in_their_chat_and_only_as_last_word_of_a_search():
    from fmbotg.config import parse_fm_aliases
    from fmbotg.context import Aliases
    aliases = Aliases(parse_fm_aliases('{"-100123": {"Alex": "alex_lfm", "red": "RedPlays"}}'))
    assert aliases.apply(-100123, ["monthly", "ALEX"]) == ["monthly", "lfm:alex_lfm"]
    assert aliases.apply(-100999, ["alex"]) == ["alex"]
    # In a search, "red" at the start is part of the search; at the end it's the person
    assert aliases.apply(-100123, "red hot chili peppers".split(), search=True) == "red hot chili peppers".split()
    assert aliases.apply(-100123, ["radiohead", "red"], search=True) == ["radiohead", "lfm:RedPlays"]
    assert parse_fm_aliases("") == {}


def test_malformed_fm_aliases_fail_at_startup_with_a_clear_message():
    import pytest
    from fmbotg.config import parse_fm_aliases
    with pytest.raises(RuntimeError, match="FM_ALIASES must be JSON"):
        parse_fm_aliases('{"not-a-chat-id": {"a": "b"}}')


async def test_lastfm_requests_are_spaced_to_the_rate_limit():
    import asyncio
    import time

    import httpx

    from fmbotg.lastfm import LastFmClient

    def api(request):
        return httpx.Response(200, json={"user": {"name": "x"}})

    client = LastFmClient("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(api)), requests_per_second=50)
    started = time.monotonic()
    await asyncio.gather(*(client.user_info("x") for _ in range(6)))
    assert time.monotonic() - started >= 5 / 50 * 0.9  # six requests need five gaps of 20 ms
    await client.close()
