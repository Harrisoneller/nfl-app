"""In-process bounded TTL + LRU cache."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

DEFAULT_MAX_ENTRIES = 2048


class MemoryTTLCache:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires < now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires = time.time() + ttl_seconds
        with self._lock:
            self._store[key] = (expires, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
