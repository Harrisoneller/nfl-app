"""Lightweight in-process TTL cache with bounded size + LRU eviction.

Replace with Redis later if needed. Suitable for single-process deployments
and dev. Thread-safe via a single lock; values are kept in memory.

Why bounded: values here include enriched pandas DataFrames (seasonal tables,
betting frames). An unbounded dict would let those accumulate across many
distinct (season, position, player) keys and pin RAM indefinitely — a slow
leak that contributed to out-of-memory crashes. We cap the number of entries
and evict least-recently-used keys once the cap is hit, so memory has a ceiling
regardless of traffic patterns. TTL still applies on top of LRU.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

# Entry count ceiling. Entries are mostly small dicts/arrays; a handful are
# multi-MB DataFrames. A few thousand entries is a safe ceiling that bounds RAM
# without evicting hot data during normal use.
DEFAULT_MAX_ENTRIES = 2048


class TTLCache:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._store: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
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
            # Mark as most-recently-used.
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expires = time.time() + ttl_seconds
        with self._lock:
            self._store[key] = (expires, value)
            self._store.move_to_end(key)
            # Evict least-recently-used entries past the cap.
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


cache = TTLCache()
