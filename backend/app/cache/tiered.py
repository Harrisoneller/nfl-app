"""Memory (always) + Redis (optional) for multi-replica web tiers."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .memory import MemoryTTLCache
from .redis_backend import RedisTTLCache


class TieredCache:
    """Hot local tier + shared Redis for serializable dict/list artifacts."""

    def __init__(self, memory: MemoryTTLCache, redis: RedisTTLCache | None) -> None:
        self._memory = memory
        self._redis = redis

    @staticmethod
    def _redis_eligible(value: Any) -> bool:
        if isinstance(value, (pd.DataFrame, pd.Series)):
            return False
        return True

    def get(self, key: str) -> Any | None:
        hit = self._memory.get(key)
        if hit is not None:
            return hit
        if self._redis is None:
            return None
        hit = self._redis.get(key)
        if hit is not None:
            self._memory.set(key, hit, 300)
        return hit

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._memory.set(key, value, ttl_seconds)
        if self._redis is not None and self._redis_eligible(value):
            self._redis.set(key, value, ttl_seconds)

    def delete(self, key: str) -> None:
        self._memory.delete(key)
        if self._redis is not None:
            self._redis.delete(key)

    def clear(self) -> None:
        self._memory.clear()
        if self._redis is not None:
            self._redis.clear()

    def ping(self) -> bool:
        if self._redis is not None:
            return self._redis.ping()
        return self._memory.ping()

    def close(self) -> None:
        self._memory.close()
        if self._redis is not None:
            self._redis.close()

    def __len__(self) -> int:
        return len(self._memory)
