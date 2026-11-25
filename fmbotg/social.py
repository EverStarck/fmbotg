"""Friends: the people you follow on Last.fm plus anyone added with /addfriend (port of FriendsService)."""

from .db import Database, User
from .domain import UserFacingError
from .lastfm import LastFmClient, LastFmError

MAX_ADDED = 20  # added with /addfriend
MAX_SHOWN = 25  # friends looked up per /friendsfm or friend who knows
NO_FRIENDS = ("You have no friends here yet. Follow people on Last.fm, or add them with "
              "<code>/addfriend username</code>.")


async def friend_names(me: User, db: Database, lastfm: LastFmClient) -> list[str]:
    """Added friends first, then Last.fm follows; no duplicates and never yourself."""
    added = await db.friends(me.id)
    try:
        following = await lastfm.following(me.lastfm_username, limit=MAX_SHOWN * 2)
    except LastFmError:
        following = []  # still show the friends added here
    seen, names = {me.lastfm_username.lower()}, []
    for name in added + following:
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names[:MAX_SHOWN]


async def friend_roster(me: User, db: Database, lastfm: LastFmClient) -> list[User]:
    """You plus your friends as Users. Friends who never used the bot get an unsaved User, so live
    Last.fm lookups can count them like everyone else."""
    names = await friend_names(me, db, lastfm)
    if not names:
        raise UserFacingError(NO_FRIENDS)
    registered = await db.users_by_lastfm(names)
    return [me] + [registered.get(n.lower()) or User(lastfm_username=n) for n in names]
