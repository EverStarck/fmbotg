from fmbot_tg.crowns import decide, update_crown
from fmbot_tg.db import PRIVACY_GLOBAL, Database, PlayRow, User
from fmbot_tg.handlers.whoknows import global_entry


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

    holder, note = await update_crown(db, -100, "Radiohead", [(alice, 50), (bob, 40)])
    assert holder == alice.id and "claimed" in note
    holder, note = await update_crown(db, -100, "Radiohead", [(alice, 55), (bob, 40)])
    assert holder == alice.id and note is None
    assert (await db.active_crown(-100, "radiohead")).playcount == 55
    holder, note = await update_crown(db, -100, "Radiohead", [(alice, 55), (bob, 60)])
    assert holder == bob.id and "stolen" in note and "55 plays" in note
    # Another chat has its own crowns
    assert await db.active_crown(-200, "Radiohead") is None

    history = await db.crown_history(-100, "Radiohead")
    assert [(c.user_id, c.active) for c, _ in history] == [(bob.id, True), (alice.id, False)]
    assert [u.id for u, _ in await db.crown_leaderboard(-100, [alice.id, bob.id])] == [bob.id]

    await db.update_chat_settings(-100, crowns_disabled=True)
    assert await update_crown(db, -100, "Radiohead", [(alice, 999)]) == (None, None)
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
