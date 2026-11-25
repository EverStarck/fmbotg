"""Background tasks kept by key, so starting one again replaces the old one (e.g. one login poll per person)."""

import asyncio
from collections.abc import Coroutine
from typing import Any, Hashable


class KeyedTasks:
    def __init__(self):
        self._tasks: dict[Hashable, asyncio.Task] = {}  # strong references so tasks aren't garbage collected

    def start(self, key: Hashable, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        previous = self._tasks.get(key)
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(coro)
        self._tasks[key] = task
        task.add_done_callback(lambda t: self._tasks.pop(key, None) if self._tasks.get(key) is t else None)
        return task

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
