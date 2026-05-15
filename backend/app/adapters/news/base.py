"""News adapter base contract."""
from __future__ import annotations

from typing import Any, Protocol


class NewsAdapter(Protocol):
    source: str

    async def fetch(self) -> list[dict[str, Any]]:
        """Return news items as dicts matching NewsItem column names."""
