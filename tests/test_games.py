import io
import random
from datetime import datetime, timezone

from PIL import Image

from fmbotg.autopost import next_post_at
from fmbotg.db import Database
from fmbotg.games import answer_is_right, first_letter_hint, normalize_answer, pick, pixelate, scramble
from fmbotg.handlers import all_routers
from fmbotg.shortcuts import Shortcuts
from fmbotg.toggles import CommandToggles


def test_guesses_ignore_case_accents_punctuation_editions_and_one_typo():
    assert answer_is_right("Björk", "bjork")
    assert answer_is_right("Sigur Rós", "sigur ros")
    assert answer_is_right("AC/DC", "acdc")
    assert answer_is_right("Simon & Garfunkel", "simon and garfunkel")
    assert answer_is_right("OK Computer (Remastered)", "ok computer")
    assert answer_is_right("Radiohead", "it's radiohead!")  # the answer inside a longer guess
    assert answer_is_right("Radiohead", "radiohed")  # one typo
    assert not answer_is_right("Muse", "mse")  # too short for typos
    assert answer_is_right("The Smashing Pumpkins", "the smashng pumpkns")  # two typos on a long name
    assert not answer_is_right("Radiohead", "")
    assert normalize_answer("Mø") == "mo"


def test_scramble_keeps_letters_per_word_and_never_gives_the_answer():
    rng = random.Random(1)
    for name in ("Muse", "Arctic Monkeys", "ABBA", "Oasis"):
        scrambled = scramble(name, rng)
        assert scrambled != name.upper()
        assert [sorted(w) for w in scrambled.split()] == [sorted(w) for w in name.upper().split()]
    assert first_letter_hint("Sigur Rós") == "S____ ___"


def test_pick_prefers_well_known_answers_and_skips_recent_ones():
    rng = random.Random(3)
    library = [(f"Artist {i}", 100 - i) for i in range(10)] + [("Rare", 1), ("XX", 500)]
    picks = {pick(library, set(), rng)[0] for _ in range(50)}
    assert "Rare" not in picks and "XX" not in picks  # too few plays / name too short
    recent = {normalize_answer(f"Artist {i}") for i in range(9)}
    assert pick(library, recent, rng)[0] in ("Artist 9", "Rare")
    assert pick([], set()) is None


def test_pixelate_returns_a_blocky_jpeg():
    source = io.BytesIO()
    Image.radial_gradient("L").convert("RGB").save(source, "PNG")
    image = Image.open(io.BytesIO(pixelate(source.getvalue(), 6)))
    assert image.size == (600, 600)
    assert image.getpixel((5, 5)) == image.getpixel((90, 90))  # same 100px block


def test_autoposts_go_out_on_mondays_and_the_first_at_noon_utc():
    friday = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    assert next_post_at("weekly", friday) == datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    monday_noon = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    assert next_post_at("weekly", monday_noon) == datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)
    assert next_post_at("monthly", friday) == datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    assert next_post_at("monthly", datetime(2026, 12, 5, tzinfo=timezone.utc)) == datetime(2027, 1, 1, 12, 0,
                                                                                            tzinfo=timezone.utc)


async def test_shortcuts_expand_with_extra_words_but_never_shadow_commands(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    await db.create_schema()
    user = await db.link_lastfm(7, "me", "sk")
    toggles = CommandToggles(db)
    toggles.register(all_routers())
    shortcuts = Shortcuts(db, toggles)
    await db.set_shortcut(user.id, "mine", "/fm lfm:someone")
    await db.set_shortcut(user.id, "fm", "/recent")  # can't be added through /shortcut, and must not apply

    assert await shortcuts.expand(7, "/mine@fmbot compact") == "/fm lfm:someone compact"
    assert await shortcuts.expand(7, "/MINE") == "/fm lfm:someone"
    assert await shortcuts.expand(7, "/fm") is None
    assert await shortcuts.expand(8, "/mine") is None  # someone else's
    await db.remove_shortcut(user.id, "mine")
    shortcuts.forget(7)
    assert await shortcuts.expand(7, "/mine") is None
    await db.close()


def test_short_answers_must_be_guessed_exactly():
    assert answer_is_right("Air", "air")
    assert not answer_is_right("Air", "not fair")  # ordinary chat mustn't win
    assert answer_is_right("Radiohead", "it's radiohead!")


def test_pace_far_in_the_future_has_no_date():
    from fmbotg.playlogic import goal_date
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    assert goal_date(now, 1_000, 10).date().isoformat() == "2027-03-03"  # 100 days
    assert goal_date(now, 10**9, 0.001) is None  # would overflow a date


async def test_autoposts_pause_when_the_bot_was_removed_from_the_group(tmp_path):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import GetChat

    from fmbotg.autopost import Autoposter
    from fmbotg.members import Members

    class KickedBot:
        async def get_chat(self, chat_id):
            raise TelegramForbiddenError(GetChat(chat_id=chat_id), "Forbidden: bot was kicked from the group chat")

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    await db.create_schema()
    post = await db.add_autopost(-100, "recap", "weekly", False, datetime(2026, 1, 1, tzinfo=timezone.utc))
    await Autoposter(db, Members(db, 100)).post(post, KickedBot())
    assert not (await db.autoposts(-100))[0].enabled
    await db.close()
