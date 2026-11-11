import time
from collections import OrderedDict
from typing import Any, Hashable


class TTLCache:
    """Small in-process LRU cache with per-entry expiry."""

    def __init__(self, ttl_seconds: float, max_items: int = 10_000):
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable, default: Any = None) -> Any:
        entry = self._items.get(key)
        if entry is None:
            return default
        expires, value = entry
        if expires < time.monotonic():
            del self._items[key]
            return default
        self._items.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any) -> None:
        self._items[key] = (time.monotonic() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
