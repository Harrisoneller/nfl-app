"""Optional Redis L1 — shared across web replicas (JSON-safe values only)."""
from __future__ import annotations

from typing import Any

import orjson

from ..logging_config import get_logger

log = get_logger(__name__)

_KEY_PREFIX = "nflapp:cache:"


def _redis_key(key: str) -> str:
    return f"{_KEY_PREFIX}{key}"


class RedisTTLCache:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.from_url(url, decode_responses=False)
        self._client.ping()

    def get(self, key: str) -> Any | None:
        raw = self._client.get(_redis_key(key))
        if raw is None:
            return None
        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            self._client.delete(_redis_key(key))
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            payload = orjson.dumps(value)
        except (TypeError, orjson.JSONEncodeError) as e:
            log.debug("redis_cache_skip_nonserializable", key=key[:80], error=str(e)[:80])
            return
        self._client.setex(_redis_key(key), max(1, int(ttl_seconds)), payload)

    def delete(self, key: str) -> None:
        self._client.delete(_redis_key(key))

    def clear(self) -> None:
        cursor = 0
        pattern = f"{_KEY_PREFIX}*"
        while True:
            cursor, keys = self._client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break

    def ping(self) -> bool:
        return bool(self._client.ping())

    def close(self) -> None:
        self._client.close()
