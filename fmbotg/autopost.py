"""Scheduled group charts (port of AutopostService).

Weekly posts go out on Mondays and monthly ones on the 1st, both at 12:00 UTC, covering the
week or month before. A loop checks for due posts every minute, so a restart only delays them.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .domain import UserFacingError
from .group_charts import group_top_text
from .members import Members
from .db import Autopost, Database
from .domain import utcnow
from .periods import ALL_TIME, MONTHLY, WEEKLY, Period

log = logging.getLogger(__name__)

CONTENT = {"recap": ["artists", "albums", "tracks"], "artists": ["artists"], "albums": ["albums"],
           "tracks": ["tracks"]}
SCHEDULES = ("weekly", "monthly")
POST_HOUR_UTC = 12
MAX_PER_CHAT = 5
CHECK_EVERY = 60


def next_post_at(schedule: str, after: datetime) -> datetime:
    """The next Monday (weekly) or 1st of the month (monthly) at 12:00 UTC, strictly after `after`."""
    after = after.astimezone(timezone.utc)
    if schedule == "weekly":
        candidate = (after + timedelta(days=(7 - after.weekday()) % 7)).replace(
            hour=POST_HOUR_UTC, minute=0, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=7)
    candidate = after.replace(day=1, hour=POST_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate > after:
        return candidate
    year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
    return candidate.replace(year=year, month=month)


def period_for(post: Autopost) -> Period:
    if post.all_time:
        return ALL_TIME
    return WEEKLY if post.schedule == "weekly" else MONTHLY


def describe(post: Autopost) -> str:
    what = "recap" if post.content == "recap" else f"top {post.content}"
    span = " (all-time)" if post.all_time else ""
    state = "" if post.enabled else " · paused"
    return f"#{post.id} · {what}{span} · {post.schedule}{state}"


class Autoposter:
    def __init__(self, db: Database, members: Members):
        self._db = db
        self._members = members
        self._task: asyncio.Task | None = None

    def start(self, bot: Bot) -> None:
        self._task = asyncio.create_task(self._loop(bot))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self, bot: Bot) -> None:
        while True:
            try:
                for post in await self._db.due_autoposts(utcnow()):
                    await self.post(post, bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Autopost loop failed")
            await asyncio.sleep(CHECK_EVERY)

    async def render(self, post: Autopost, bot: Bot) -> str:
        try:
            title = (await bot.get_chat(post.chat_id)).title or "this group"
        except TelegramBadRequest:
            title = "this group"
        shown = 15 if post.content != "recap" else 5
        return await group_top_text(self._db, self._members, post.chat_id, title, CONTENT[post.content],
                                    period_for(post), shown=shown)

    async def post(self, post: Autopost, bot: Bot, scheduled: bool = True) -> None:
        """Send one autopost. Scheduled runs move the next date forward whatever happens."""
        if scheduled:
            await self._db.update_autopost(post.id, next_post_at=next_post_at(post.schedule, utcnow()))
        try:
            text = await self.render(post, bot)
        except TelegramForbiddenError:
            # Removed from the group: stop trying until an admin resumes it
            await self._db.update_autopost(post.id, enabled=False)
            return
        except UserFacingError as exc:
            log.info("Autopost %s skipped: %s", post.id, exc)
            if not scheduled:
                raise
            return
        try:
            await bot.send_message(post.chat_id, "🗓 " + text)
        except TelegramForbiddenError:
            # Removed from the group: stop trying until an admin resumes it
            await self._db.update_autopost(post.id, enabled=False)
            return
        await self._db.update_autopost(post.id, last_posted_at=utcnow())
