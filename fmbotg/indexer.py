"""Scrobble history import and updates (port of IndexService / UpdateService / TimerService jobs).

Runs in-process: a queue of full imports worked by a few tasks, plus a loop that keeps active
users' play index fresh. One lock per user keeps an import and an update from interleaving.
"""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import timedelta
from typing import Awaitable, Callable

from .db import Database, PlayRow, User
from .lastfm import LastFmClient, LastFmError, RecentTracks

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
# An update that would need more pages than this is cheaper as a full re-import.
MAX_UPDATE_PAGES = 20

Notify = Callable[[int, str], Awaitable[object]]


class AccountChanged(Exception):
    """The user logged out or switched Last.fm accounts while their history was being imported."""


def plays_from(result: RecentTracks) -> list[PlayRow]:
    # The now playing entry has no timestamp and isn't a scrobble yet.
    return [PlayRow(s.timestamp, s.artist, s.album or None, s.track) for s in result.tracks if s.timestamp]


class Indexer:
    def __init__(
        self,
        db: Database,
        lastfm: LastFmClient,
        notify: Notify | None = None,
        workers: int = 2,
        update_interval: float = 600,
        stale_after: timedelta = timedelta(hours=2),
        active_within: timedelta = timedelta(days=14),
    ):
        self._db = db
        self._lastfm = lastfm
        self._notify = notify
        self._worker_count = workers
        self._update_interval = update_interval
        self._stale_after = stale_after
        self._active_within = active_within
        self._queue: asyncio.Queue[tuple[int, int | None]] = asyncio.Queue()
        self._queued: set[int] = set()
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(), name=f"index-worker-{i}")
                       for i in range(self._worker_count)]
        self._tasks.append(asyncio.create_task(self._update_loop(), name="index-updater"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # --- public API ---------------------------------------------------------------------

    def is_importing(self, user_id: int) -> bool:
        return user_id in self._queued

    def queue_full_import(self, user_id: int, notify_chat: int | None = None) -> bool:
        """Returns False if an import for this user is already queued or running."""
        if user_id in self._queued:
            return False
        self._queued.add(user_id)
        self._queue.put_nowait((user_id, notify_chat))
        return True

    async def update(self, user_id: int) -> int:
        """Fetch scrobbles newer than the last indexed one. Returns how many were added.

        Returns 0 without waiting if an import or another update for this user is running.
        """
        lock = self._locks[user_id]
        if lock.locked() or user_id in self._queued:
            return 0
        async with lock:
            user = await self._db.get_user_by_id(user_id)
            if user is None or not user.lastfm_username or user.indexed_at is None:
                return 0
            try:
                return await self._update_locked(user)
            except AccountChanged:
                return 0  # /login or /logout already reset the index
            except LastFmError as exc:
                await self._db.mark_attempt(user.id)
                log.info("Update for %s failed: %s", user.lastfm_username, exc)
                return 0

    async def run_full_import(self, user_id: int) -> int:
        """Import the full history now (normally called by the queue workers)."""
        async with self._locks[user_id]:
            user = await self._db.get_user_by_id(user_id)
            if user is None or not user.lastfm_username:
                return 0
            return await self._full_import_locked(user)

    # --- internals ----------------------------------------------------------------------

    async def _ensure_same_account(self, user: User) -> None:
        """/login and /logout don't wait for an import, so check between pages that the plays we're
        about to save still belong to the account linked now."""
        current = await self._db.get_user_by_id(user.id)
        if current is None or (current.lastfm_username or "").lower() != user.lastfm_username.lower():
            raise AccountChanged()

    async def _full_import_locked(self, user: User) -> int:
        started = int(time.time())
        await self._db.begin_full_index(user.id)
        imported, newest, page = 0, 0, 1
        while True:
            result = await self._lastfm.recent_tracks(
                user.lastfm_username, limit=PAGE_SIZE, session_key=user.session_key,
                page=page, to_ts=started, extended=False,
            )
            plays = plays_from(result)
            await self._ensure_same_account(user)
            await self._db.insert_plays(user.id, plays)
            imported += len(plays)
            newest = max([newest, *(p.played_at for p in plays)])
            if page >= result.total_pages or not result.tracks:
                break
            page += 1
        # Anything scrobbled after `started` is picked up by the next update.
        await self._ensure_same_account(user)
        await self._db.finish_index(user.id, newest or started, full=True)
        log.info("Imported %s plays for %s", imported, user.lastfm_username)
        return imported

    async def _update_locked(self, user: User) -> int:
        since = (user.last_scrobble_ts or 0) + 1
        plays: list[PlayRow] = []
        page = 1
        while True:
            result = await self._lastfm.recent_tracks(
                user.lastfm_username, limit=PAGE_SIZE, session_key=user.session_key,
                page=page, from_ts=since, extended=False,
            )
            plays += plays_from(result)
            if page >= result.total_pages or not result.tracks:
                break
            if page >= MAX_UPDATE_PAGES:
                await self._db.mark_attempt(user.id)
                self.queue_full_import(user.id)
                return 0
            page += 1

        await self._ensure_same_account(user)
        await self._db.insert_plays(user.id, plays)
        newest = max([user.last_scrobble_ts or 0, *(p.played_at for p in plays)])
        await self._db.finish_index(user.id, newest, full=False)
        return len(plays)

    async def _worker(self) -> None:
        while True:
            user_id, notify_chat = await self._queue.get()
            restart = False
            try:
                imported = await self.run_full_import(user_id)
                message = f"✅ Your Last.fm history is imported ({imported:,} scrobbles)."
            except AccountChanged:
                # Drop what this import saved for the old account; a new account is imported from scratch
                await self._db.reset_index(user_id)
                current = await self._db.get_user_by_id(user_id)
                restart = current is not None and current.lastfm_username is not None
                message = None
            except LastFmError as exc:
                log.warning("Import for user %s failed: %s", user_id, exc)
                await self._db.mark_attempt(user_id)
                message = f"Importing your history failed: {exc.user_message()} Use /update to retry."
            except Exception:
                log.exception("Import for user %s crashed", user_id)
                await self._db.mark_attempt(user_id)
                message = "Importing your history failed. Use /update to retry."
            finally:
                self._queued.discard(user_id)
                self._queue.task_done()
            if restart:
                self.queue_full_import(user_id, notify_chat)
                continue
            if message is not None and notify_chat is not None and self._notify is not None:
                try:
                    await self._notify(notify_chat, message)
                except Exception:
                    log.info("Couldn't send import notification to %s", notify_chat)

    async def _update_loop(self) -> None:
        while True:
            await asyncio.sleep(self._update_interval)
            try:
                for user in await self._db.users_needing_import(limit=20, retry_after=timedelta(hours=6)):
                    self.queue_full_import(user.id)
                due = await self._db.users_due_for_update(50, self._stale_after, self._active_within)
                for user in due:
                    await self.update(user.id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Periodic index update failed")
