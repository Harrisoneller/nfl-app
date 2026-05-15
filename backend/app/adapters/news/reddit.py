"""Reddit adapter — uses public JSON endpoint, no auth."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx

from ...utils.team_aliases import TEAM_SUBREDDITS


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
        try:
            r = await self.client.get(self.url)
            r.raise_for_status()
        except Exception:
            return []
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
