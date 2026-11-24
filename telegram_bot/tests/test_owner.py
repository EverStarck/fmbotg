import json

import httpx
import pytest
from aiogram.types import Message

from fmbot_tg import moderation
from fmbot_tg.db import Database, PlayRow
from fmbot_tg.judge import Judge, JudgeUnavailable
from fmbot_tg.metadata import pick_release_date


def test_release_date_needs_the_same_title_and_artist_and_takes_the_earliest():
    groups = [
        {"title": "OK Computer", "score": 100, "first-release-date": "2017-06-23",
         "artist-credit": [{"name": "Radiohead"}]},  # the OKNOTOK reissue group, same title
        {"title": "OK Computer", "score": 100, "first-release-date": "1997-05-21",
         "artist-credit": [{"name": "Radiohead"}]},
        {"title": "OK Computer", "score": 95, "first-release-date": "1990", "artist-credit": [{"name": "Tribute Band"}]},
    ]
    assert pick_release_date("radiohead", "OK Computer (Remastered)", groups) == "1997-05-21"
    assert pick_release_date("Radiohead", "Kid A", groups) is None


async def test_banned_people_are_ignored_but_owners_never_are(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'o.db'}")
    await db.create_schema()
    for telegram_id, name in ((1, "owner"), (2, "troll")):
        user = await db.link_lastfm(telegram_id, name, "sk")
        await db.set_user_flag(user.id, bot_banned=True)
    moderation.configure_owners({1})
    await moderation.load(db)

    handled = []

    async def handler(event, data):
        handled.append(event.from_user.id)

    def message(user_id: int) -> Message:
        return Message.model_validate({"message_id": 1, "date": 0, "text": "/fm", "chat": {"id": user_id, "type": "private"},
                                       "from": {"id": user_id, "is_bot": False, "first_name": "x"}})

    middleware = moderation.BanMiddleware()
    for user_id in (1, 2, 3):
        await middleware(handler, message(user_id), {})
    assert handled == [1, 3]
    moderation.set_banned(2, False)
    await middleware(handler, message(2), {})
    assert handled == [1, 3, 2]
    moderation.configure_owners(set())
    await db.close()


async def test_censoring_covers_an_album_or_every_album_of_an_artist(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    await db.create_schema()
    assert await db.add_censored("Band", "Bad Cover")
    assert not await db.add_censored("band", "bad cover")
    await db.add_censored("Other", None)
    await moderation.load(db)
    assert moderation.is_censored("BAND", "Bad Cover")
    assert not moderation.is_censored("Band", "Fine Cover")
    assert moderation.is_censored("other", "Anything") and moderation.is_censored("Other")
    await db.remove_censored("Other", None)
    await moderation.load(db)
    assert not moderation.is_censored("Other", "Anything")
    await db.close()


async def test_hidden_users_are_left_out_of_global_who_knows(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
    await db.create_schema()
    users = [await db.link_lastfm(i, f"u{i}", "sk") for i in (1, 2)]
    for user in users:
        await db.insert_plays(user.id, [PlayRow(10, "Muse", None, "T")])
        await db.finish_index(user.id, 10, full=True)
    await db.set_user_flag(users[1].id, global_hidden=True)
    assert [u.id for u, _ in await db.global_playcounts("artist", ("Muse",))] == [users[0].id]
    await db.close()


async def test_judge_calls_openrouter_and_limits_daily_use():
    sent = []

    def api(request: httpx.Request) -> httpx.Response:
        sent.append((request.headers, json.loads(request.content)))
        if len(sent) == 2:
            return httpx.Response(200, json={"choices": [{"finish_reason": "content_filter",
                                                          "message": {"role": "assistant", "content": ""}}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "Lovely taste."}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(api))
    judge = Judge(http, "test-key", None, daily_limit=2)
    assert await judge.run(1, "compliment", "weekly", [("Muse", 10)], [("Uprising", "Muse", 4)]) == "Lovely taste."
    headers, body = sent[0]
    assert headers["authorization"] == "Bearer test-key"
    assert body["model"] == "anthropic/claude-opus-5.5" and judge.model_name == "claude-opus-5.5"
    assert body["messages"][0]["role"] == "system" and "Muse (10)" in body["messages"][1]["content"]
    with pytest.raises(JudgeUnavailable, match="declined"):
        await judge.run(1, "roast", "weekly", [("Muse", 10)], [])
    with pytest.raises(JudgeUnavailable, match="judgements for today"):
        await judge.run(1, "roast", "weekly", [("Muse", 10)], [])
    assert len(sent) == 2 and judge.remaining(2) == 2
    assert not Judge(http, None, None, 5).enabled
    await http.aclose()
