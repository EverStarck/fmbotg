import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} (see .env.example)")
    return value


def parse_fm_aliases(raw: str | None) -> dict[int, dict[str, str]]:
    """FM_ALIASES: JSON of chat id -> {nickname: Last.fm username}, e.g.
    {"-1001234567890": {"alex": "alexlfm"}}. Nicknames are matched case-insensitively."""
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return {int(chat): {nick.lower(): str(user) for nick, user in names.items()}
                for chat, names in data.items()}
    except (ValueError, AttributeError, TypeError) as exc:
        raise RuntimeError(
            'FM_ALIASES must be JSON like {"-1001234567890": {"nickname": "lastfmuser"}}'
        ) from exc


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    lastfm_api_key: str
    lastfm_api_secret: str
    # Optional second key for unsigned calls. FMBot does this because the public key is
    # visible in the login URL and was abused to burn the bot's rate limit.
    lastfm_private_key: str | None
    database_url: str
    whoknows_max_members: int
    # Per-chat /fm nicknames, kept out of the code so friends' usernames don't end up on GitHub
    fm_aliases: dict[int, dict[str, str]] = field(default_factory=dict)
    # MusicBrainz (artist countries) asks clients to identify themselves with a way to reach the operator
    musicbrainz_contact: str | None = None
    # Optional: exact Spotify/YouTube links instead of search links
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    youtube_api_key: str | None = None
    # Optional: /judge through OpenRouter; costs money per call, so each person gets a few per day
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    # Telegram user ids allowed to use the owner commands (bans, censoring, /botstats)
    owner_ids: frozenset[int] = frozenset()
    judge_daily_limit: int = 5

    @property
    def user_agent(self) -> str:
        return "fmbot-telegram/0.1" + (f" ( {self.musicbrainz_contact} )" if self.musicbrainz_contact else "")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            telegram_token=_required("TELEGRAM_BOT_TOKEN"),
            lastfm_api_key=_required("LASTFM_API_KEY"),
            lastfm_api_secret=_required("LASTFM_API_SECRET"),
            lastfm_private_key=os.getenv("LASTFM_PRIVATE_KEY") or None,
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///fmbot.db"),
            whoknows_max_members=int(os.getenv("WHOKNOWS_MAX_MEMBERS", "100")),
            fm_aliases=parse_fm_aliases(os.getenv("FM_ALIASES")),
            musicbrainz_contact=os.getenv("MUSICBRAINZ_CONTACT") or None,
            spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID") or None,
            spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET") or None,
            youtube_api_key=os.getenv("YOUTUBE_API_KEY") or None,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv("OPENROUTER_MODEL") or None,
            owner_ids=frozenset(int(i) for i in os.getenv("BOT_OWNER_IDS", "").replace(",", " ").split()),
            judge_daily_limit=int(os.getenv("JUDGE_DAILY_LIMIT", "5")),
        )
