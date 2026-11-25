from fmbotg.crowns import decide, update_crown
from fmbotg.db import PRIVACY_GLOBAL, Database, PlayRow, User
from fmbotg.handlers.whoknows import global_entry


def test_crown_is_claimed_at_the_threshold_and_ties_keep_the_holder():
    assert decide(None, {1: 29, 2: 10}, threshold=30).action == "none"
    assert decide(None, {1: 30, 2: 10}, threshold=30) == decide(None, {2: 10, 1: 30}, threshold=30)
    assert decide(None, {1: 30}, threshold=30).user_id == 1
    tie = decide(1, {1: 40, 2: 40}, threshold=30)
    assert (tie.action, tie.user_id, tie.plays) == ("keep", 1, 40)
    steal = decide(1, {1: 40, 2: 41}, threshold=30)
    assert (steal.action, steal.user_id) == ("steal", 2)


def test_crown_is_never_taken_on_an_unknown_count_but_moves_when_the_holder_is_gone():
    # Holder's Last.fm lookup failed: keep it rather than guess
    assert decide(1, {1: None, 2: 500}, threshold=30).action == "keep"
    # Holder left the chat or disconnected
    moved = decide(1, {2: 35}, threshold=30)
    assert (moved.action, moved.user_id) == ("steal", 2)
    assert decide(1, {2: 5}, threshold=30).action == "keep"


async def test_whoknows_crown_flow_keeps_history(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    await db.create_schema()
    alice = await db.link_lastfm(1, "alice", "sk")
    bob = await db.link_lastfm(2, "bob", "sk")

    change = await update_crown(db, -100, "Radiohead", [(alice, 50), (bob, 40)])
    assert change.holder_id == alice.id and "claimed" in change.announcement()
    change = await update_crown(db, -100, "Radiohead", [(alice, 55), (bob, 40)])
    assert change.holder_id == alice.id and change.announcement() is None
    assert (await db.active_crown(-100, "radiohead")).playcount == 55
    change = await update_crown(db, -100, "Radiohead", [(alice, 55), (bob, 60)])
    assert change.holder_id == bob.id and change.previous.id == alice.id and change.previous_plays == 55
    assert "stolen" in change.announcement() and "55 plays" in change.announcement()
    # Another chat has its own crowns
    assert await db.active_crown(-200, "Radiohead") is None

    history = await db.crown_history(-100, "Radiohead")
    assert [(c.user_id, c.active) for c, _ in history] == [(bob.id, True), (alice.id, False)]
    assert [u.id for u, _ in await db.crown_leaderboard(-100, [alice.id, bob.id])] == [bob.id]

    await db.update_chat_settings(-100, crowns_disabled=True)
    assert (await update_crown(db, -100, "Radiohead", [(alice, 999)])).holder_id is None
    await db.close()


async def test_seeding_only_fills_artists_without_a_crown(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    await db.create_schema()
    alice = await db.link_lastfm(1, "alice", "sk")
    bob = await db.link_lastfm(2, "bob", "sk")
    await db.insert_plays(alice.id, [PlayRow(i, "Björk" if i % 2 else "björk", None, "T") for i in range(40)])
    await db.insert_plays(bob.id, [PlayRow(i, "Muse", None, "T") for i in range(35)])
    await db.set_crown(-100, "Muse", alice.id, 1)

    candidates = await db.crown_candidates([alice.id, bob.id], threshold=30)
    assert sorted((user_id, plays) for _, user_id, plays in candidates) == [(alice.id, 40), (bob.id, 35)]
    assert await db.seed_crowns(-100, candidates) == 1  # Muse already had a crown
    assert (await db.active_crown(-100, "BJÖRK")).user_id == alice.id
    assert (await db.active_crown(-100, "Muse")).user_id == alice.id
    await db.close()


def test_global_whoknows_hides_names_unless_chosen_or_shared_group():
    private = User(id=5, telegram_id=50, lastfm_username="secret", display_name="Secret")
    public = User(id=6, telegram_id=60, lastfm_username="loud", privacy_level=PRIVACY_GLOBAL)
    assert global_entry(private, 10, caller_id=1, visible_ids=set()).name == "Private user"
    assert global_entry(private, 10, caller_id=1, visible_ids=set()).lastfm_username is None
    assert global_entry(private, 10, caller_id=50, visible_ids=set()).is_caller
    assert global_entry(private, 10, caller_id=1, visible_ids={5}).name == "Secret"
    assert global_entry(public, 10, caller_id=1, visible_ids=set()).lastfm_username == "loud"


async def test_friends_are_unique_per_user_ignoring_case(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'f.db'}")
    await db.create_schema()
    alice = await db.link_lastfm(1, "alice", "sk")
    assert await db.add_friend(alice.id, "RJ")
    assert not await db.add_friend(alice.id, "rj")
    assert await db.friends(alice.id) == ["RJ"]
    assert await db.remove_friend(alice.id, "rJ")
    assert await db.friends(alice.id) == []
    await db.close()


async def test_blocked_members_leave_group_leaderboards_but_not_other_groups(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    await db.create_schema()
    alice = await db.link_lastfm(1, "alice", "sk")
    bob = await db.link_lastfm(2, "bob", "sk")
    for telegram_id in (1, 2):
        await db.touch(telegram_id, None, "x", chat_id=-100)
        await db.touch(telegram_id, None, "x", chat_id=-200)
    assert await db.block_user(-100, bob.id) and not await db.block_user(-100, bob.id)
    assert [u.id for u in await db.chat_lastfm_users(-100, 10)] == [alice.id]
    assert len(await db.chat_lastfm_users(-200, 10)) == 2

    await db.insert_plays(alice.id, [PlayRow(10, "Muse", "Drones", "Dead Inside"), PlayRow(20, "Muse", None, "Psycho")])
    await db.insert_plays(bob.id, [PlayRow(15, "Muse", "Drones", "Dead Inside")])
    assert await db.group_top([alice.id, bob.id], "artists", 0, 100) == [("Muse", None, 3, 2)]
    assert await db.group_top([alice.id, bob.id], "albums", 0, 100) == [("Drones", "Muse", 2, 2)]
    assert await db.unblock_user(-100, bob.id)
    assert len(await db.chat_lastfm_users(-100, 10)) == 2
    await db.close()


async def test_disabled_commands_match_any_alias_and_skip_only_that_group(tmp_path):
    from aiogram.types import Message

    from fmbotg.handlers import all_routers
    from fmbotg.toggles import CommandToggles, DisabledCommandsMiddleware

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    await db.create_schema()
    toggles = CommandToggles(db)
    toggles.register(all_routers())
    assert toggles.resolve("/wk@fmbot") == "whoknows" and toggles.resolve("nope") is None
    await toggles.save(-100, {"whoknows"})

    calls = []

    async def handler(event, data):
        calls.append(event.chat.id)

    def message(chat_id: int, text: str) -> Message:
        return Message.model_validate({"message_id": 1, "date": 0, "text": text,
                                       "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"}})

    middleware = DisabledCommandsMiddleware(toggles)
    await middleware(handler, message(-100, "/w radiohead"), {})
    await middleware(handler, message(-200, "/whoknows"), {})
    await middleware(handler, message(5, "/whoknows"), {})
    await middleware(handler, message(-100, "/fm"), {})
    assert calls == [-200, 5, -100]
    # Survives a restart
    assert await CommandToggles(db).disabled(-100) == {"whoknows"}
    await db.close()


async def test_upgrading_to_a_supergroup_keeps_members_crowns_and_settings(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    await db.create_schema()
    alice = await db.link_lastfm(1, "alice", "sk")
    await db.touch(1, None, "Alice", chat_id=-100)
    await db.touch(1, None, "Alice", chat_id=-1009)  # already seen in the new supergroup
    await db.set_crown(-100, "Muse", alice.id, 40)
    await db.update_chat_settings(-100, crown_threshold=50)

    await db.migrate_chat(-100, -1009)
    assert [u.id for u in await db.chat_lastfm_users(-1009, 10)] == [alice.id]
    assert await db.chat_lastfm_users(-100, 10) == []
    assert (await db.active_crown(-1009, "Muse")).user_id == alice.id
    assert (await db.chat_settings(-1009)).crown_threshold == 50
    await db.close()


async def test_a_chat_can_never_have_two_active_crowns_for_one_artist(tmp_path):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from fmbotg.db import Crown

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'u.db'}")
    await db.create_schema()
    alice = await db.link_lastfm(1, "alice", "sk")
    bob = await db.link_lastfm(2, "bob", "sk")
    await db.set_crown(-100, "Muse", alice.id, 40)
    async with db.sessions() as session:  # what a racing second claim would try to write
        session.add(Crown(chat_id=-100, user_id=bob.id, artist="Muse", artist_key="muse", playcount=41,
                          start_playcount=41, active=True, seeded=False))
        with pytest.raises(IntegrityError):
            await session.commit()
    await db.set_crown(-100, "Muse", bob.id, 41)  # a normal steal still works
    assert (await db.active_crown(-100, "Muse")).user_id == bob.id
    await db.close()
