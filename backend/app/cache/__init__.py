"""Application cache — memory-only or tiered memory+Redis for scaled web replicas."""
from __future__ import annotations

from typing import Protocol

from ..config import get_settings
from ..logging_config import get_logger
from .memory import MemoryTTLCache
from .tiered import TieredCache

log = get_logger(__name__)


class CacheBackend(Protocol):
    def get(self, key: str) -> object | None: ...
    def set(self, key: str, value: object, ttl_seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...


_instance: CacheBackend | None = None


def build_cache() -> CacheBackend:
    settings = get_settings()
    memory = MemoryTTLCache(max_entries=settings.cache_max_entries)
    if settings.cache_backend != "redis":
        return memory
    try:
        from .redis_backend import RedisTTLCache

        redis = RedisTTLCache(settings.redis_url)
        log.info("cache_redis_enabled", url=_safe_redis_url(settings.redis_url))
        return TieredCache(memory, redis)
    except Exception as e:  # noqa: BLE001
        log.warning("cache_redis_unavailable", error=str(e)[:200], fallback="memory")
        return memory


def get_cache() -> CacheBackend:
    global _instance
    if _instance is None:
        _instance = build_cache()
    return _instance


def reset_cache() -> None:
    """Close and rebuild (tests / settings reload)."""
    global _instance
    if _instance is not None:
        _instance.close()
    _instance = None


def close_cache() -> None:
    global _instance
    if _instance is not None:
        _instance.close()
        _instance = None


class _CacheFacade:
    """Module-level `cache` object used across services."""

    def get(self, key: str):
        return get_cache().get(key)

    def set(self, key: str, value, ttl_seconds: int) -> None:
        get_cache().set(key, value, ttl_seconds)

    def delete(self, key: str) -> None:
        get_cache().delete(key)

    def clear(self) -> None:
        get_cache().clear()

    def ping(self) -> bool:
        return get_cache().ping()

    def __len__(self) -> int:
        inst = _instance
        return len(inst) if inst is not None else 0


cache = _CacheFacade()


def _safe_redis_url(url: str) -> str:
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if ":" in head.split("//", 1)[-1]:
        scheme, rest = head.split("//", 1)
        user = rest.split(":", 1)[0]
        return f"{scheme}//{user}:***@{tail}"
    return url
