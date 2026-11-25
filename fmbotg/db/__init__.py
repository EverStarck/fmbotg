"""Database access. Models live in models.py; queries are grouped by topic in mixins that
`Database` combines, so callers keep using one object."""

from .chats import ChatQueries
from .crowns import CrownQueries
from .meta import MetaQueries
from .models import (  # noqa: F401  (the models are part of this package's API)
    DEFAULT_CROWN_THRESHOLD, PRIVACY_CHAT, PRIVACY_GLOBAL, AlbumMeta, ArtistMeta, Autopost, Base, CensoredMusic,
    ChatBlockedUser, ChatMember, ChatSettings, Crown, Friend, SavedStreak, TrackMeta, User, UserPlay, UserShortcut,
)
from .plays import PlayQueries
from .rows import AggregateRow, PlayRow  # noqa: F401
from .users import UserQueries


class Database(UserQueries, PlayQueries, ChatQueries, CrownQueries, MetaQueries):
    """One object for every query; see the mixins for each topic."""
