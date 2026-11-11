import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} (see .env.example)")
    return value


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
        )
