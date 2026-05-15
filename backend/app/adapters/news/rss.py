"""RSS news adapter — runs feedparser against the configured feeds."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser  # type: ignore

from ...config import get_settings

settings = get_settings()


class RSSAdapter:
    source = "rss"

    def __init__(self, feeds: list[str] | None = None, source_name: str = "rss") -> None:
        self.feeds = feeds or settings.rss_feed_list
        self.source = source_name

    async def fetch(self) -> list[dict[str, Any]]:
        # feedparser is sync; offload
        loop = asyncio.get_event_loop()
        results: list[dict[str, Any]] = []
        for url in self.feeds:
            parsed = await loop.run_in_executor(None, feedparser.parse, url)
            label = (parsed.feed.get("title") or _domain(url))[:128]
            for entry in parsed.entries[:50]:
                link = entry.get("link", "")
                if not link:
                    continue
                pub = _parse_time(entry.get("published_parsed") or entry.get("updated_parsed"))
                results.append({
                    "id": _hash_id(self.source, link),
                    "source": self.source,
                    "source_label": label,
                    "title": (entry.get("title") or "")[:512],
                    "summary": (entry.get("summary") or "")[:5000],
                    "link": link[:1024],
                    "author": (entry.get("author") or "")[:255],
                    "image_url": _image_from_entry(entry)[:1024],
                    "published_at": pub,
                    "team_tags": "",
                })
        return results


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def _hash_id(source: str, link: str) -> str:
    return hashlib.sha1(f"{source}::{link}".encode()).hexdigest()


def _parse_time(t) -> datetime | None:
    if not t:
        return None
    try:
        return datetime(*t[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _image_from_entry(entry: dict) -> str:
    media = entry.get("media_content") or entry.get("media_thumbnail")
    if isinstance(media, list) and media:
        return media[0].get("url", "")
    links = entry.get("links") or []
    for l in links:
        if l.get("type", "").startswith("image/"):
            return l.get("href", "")
    return ""
