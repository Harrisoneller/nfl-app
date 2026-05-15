"""Lightweight in-process TTL cache.

Replace with Redis later if needed. Suitable for single-process deployments
and dev. Thread-safe via a single lock; values are kept in memory.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

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
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires = time.time() + ttl_seconds
        with self._lock:
            self._store[key] = (expires, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


cache = TTLCache()
