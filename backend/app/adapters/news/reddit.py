"""Reddit adapter — uses public JSON endpoint, no auth.

News refresh runs every five minutes. A 403 or 429 starts a process-local
cooldown of ``REDDIT_BLOCK_TTL_SECONDS`` (15 minutes) and logs one warning.
Later Reddit calls in that window return no items and do not hit the network.
The cooldown is not configurable and does not cover other status codes or
other news sources. A process restart clears it.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ...logging_config import get_logger
from ...utils.team_aliases import TEAM_SUBREDDITS

log = get_logger(__name__)

# Two skipped news ticks, then one retry. 403/429 from the public JSON
# endpoint are usually a block, not a one-off miss.
REDDIT_BLOCK_TTL_SECONDS = 15 * 60
_BLOCKED_STATUSES = frozenset({403, 429})
_blocked_until = 0.0


def _now() -> float:
    return time.monotonic()


def _start_cooldown(status_code: int) -> None:
    """Start the cooldown and warn once. A later 403/429 in-window is silent."""
    global _blocked_until
    now = _now()
    if now < _blocked_until:
        return
    _blocked_until = now + REDDIT_BLOCK_TTL_SECONDS
    log.warning(
        "reddit_cooldown",
        status_code=status_code,
        ttl_seconds=REDDIT_BLOCK_TTL_SECONDS,
        detail="Skipping Reddit until the cooldown ends.",
    )


def reddit_cooldown_active() -> bool:
    return _now() < _blocked_until


class RedditAdapter:
    source = "reddit"
    HEADERS = {"User-Agent": "nfl-app/0.2 (read-only aggregator)"}

    def __init__(self, subreddit: str = "nfl", limit: int = 40) -> None:
        self.subreddit = subreddit
        self.limit = limit
        self.client = httpx.AsyncClient(timeout=10.0, headers=self.HEADERS)

    @property
    def url(self) -> str:
        return f"https://www.reddit.com/r/{self.subreddit}/hot.json?limit={self.limit}"

    async def fetch(self) -> list[dict[str, Any]]:
        if reddit_cooldown_active():
            return []
        r = await self.client.get(self.url)
        if r.status_code in _BLOCKED_STATUSES:
            _start_cooldown(r.status_code)
            return []
        r.raise_for_status()
        data = r.json()
        items: list[dict[str, Any]] = []
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            link = "https://reddit.com" + d.get("permalink", "")
            items.append({
                "id": _hash_id("reddit", link),
                "source": "reddit",
                "source_label": f"r/{self.subreddit}",
                "title": (d.get("title") or "")[:512],
                "summary": (d.get("selftext") or "")[:5000],
                "link": link[:1024],
                "author": ("u/" + (d.get("author") or "?"))[:255],
                "image_url": (d.get("thumbnail") if (d.get("thumbnail") or "").startswith("http") else "")[:1024],
                "published_at": _from_epoch(d.get("created_utc")),
                "team_tags": "",
            })
        return items

    async def aclose(self) -> None:
        await self.client.aclose()


def subreddit_for_team(team_id: str) -> str | None:
    return TEAM_SUBREDDITS.get(team_id.upper())


def _hash_id(source: str, link: str) -> str:
    return hashlib.sha1(f"{source}::{link}".encode()).hexdigest()


def _from_epoch(v) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except Exception:
        return None
