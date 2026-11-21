from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fmbot_tg.periods import ALL_TIME, MONTHLY, TODAY, YESTERDAY, parse_period
from fmbot_tg.playlogic import Play, Streak, goal_amount, milestone_amount, overview, taste


def test_goal_defaults_to_next_breakpoint_and_accepts_k_suffix():
    assert goal_amount([], 1300)[0] == 1337
    assert goal_amount(["100k", "@bob"], 45_000) == (100_000, ["@bob"])
    # A goal below the current count is ignored, like FMBot
    assert goal_amount(["1000"], 45_000)[0] == 50_000


def test_milestone_defaults_to_last_breakpoint_passed():
    assert milestone_amount([], 1338)[:2] == (1337, False)
    assert milestone_amount(["10k"], 12_000)[:2] == (10_000, False)
    amount, is_random, _ = milestone_amount(["random"], 500)
    assert is_random and 1 <= amount < 500


def test_streak_counts_artist_album_and_track_independently():
    now_playing = Play(None, "Radiohead", "Kid A", "Idioteque")
    history = [
        Play(500, "radiohead", "Kid A", "Idioteque"),
        Play(400, "Radiohead", "Kid A", "Idioteque"),
        Play(300, "Radiohead", "Kid A", "Optimistic"),
        Play(200, "Radiohead", "OK Computer", "Airbag"),
        Play(100, "Björk", "Homogenic", "Jóga"),
    ]
    streak = Streak(now_playing)
    assert not streak.feed(history)
    assert (streak.artist_count, streak.album_count, streak.track_count) == (5, 4, 3)
    assert streak.started == 200


def test_streak_can_be_fed_in_chunks():
    streak = Streak(Play(1000, "A", None, "x"))
    assert streak.album_count == 0
    assert streak.feed([Play(900, "A", None, "y")])  # artist streak still alive at the end of the chunk
    assert not streak.feed([Play(800, "A", None, "z"), Play(700, "B", None, "x")])
    assert streak.artist_count == 3 and streak.track_count == 1


def test_overview_groups_by_local_day():
    tz = ZoneInfo("America/New_York")
    # 03:00 UTC on Mar 2 is still Mar 1 in New York
    ts = int(datetime(2026, 3, 2, 3, 0, tzinfo=timezone.utc).timestamp())
    days = overview([Play(ts, "A", "Al", "T"), Play(ts + 86400, "B", None, "U"), Play(ts + 86401, "b", None, "U")], tz)
    assert [(d.day, d.plays) for d in days] == [(date(2026, 3, 2), 2), (date(2026, 3, 1), 1)]
    assert days[0].top_artist == ("B", 2) and days[0].top_album is None


def test_calendar_periods_follow_the_time_zone():
    tz = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)  # already Mar 2, 05:00 in Tokyo
    start, _ = TODAY.bounds(now, tz)
    assert datetime.fromtimestamp(start, tz) == datetime(2026, 3, 2, tzinfo=tz)
    _, end = YESTERDAY.bounds(now, tz)
    assert end == start


def test_period_in_a_search_only_counts_at_the_edges():
    assert parse_period("Beatles - Yesterday".split(), ALL_TIME, edges_only=True) == \
        (ALL_TIME, ["Beatles", "-", "Yesterday"])
    assert parse_period("Radiohead monthly".split(), ALL_TIME, edges_only=True) == (MONTHLY, ["Radiohead"])
    assert parse_period("The Month Of May".split(), ALL_TIME, edges_only=True)[0] == ALL_TIME


def test_taste_overlap_is_symmetric_and_case_insensitive():
    a = [("Radiohead", 50), ("Björk", 50)]
    b = [("radiohead", 10), ("Muse", 90)]
    ab, ba = taste(a, b), taste(b, a)
    assert ab.similarity == ba.similarity == 10.0
    assert ab.shared == [("Radiohead", 50, 10)]
    assert taste(a, a).similarity == 100.0
    assert taste(a, []).similarity == 0.0
