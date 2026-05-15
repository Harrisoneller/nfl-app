"""Twitter / X adapter (stub).

Disabled by default. Flip ENABLE_TWITTER=true and provide TWITTER_BEARER_TOKEN
to activate. Implementation skeleton in place — fill in the API calls when
budget allows.
"""
from __future__ import annotations

from typing import Any

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()

# Reporters/handles to track once enabled. Curate freely.
DEFAULT_REPORTERS = [
    "AdamSchefter",
    "RapSheet",
    "MikeGarafolo",
    "JeffDarlington",
    "TomPelissero",
    "FieldYates",
    "MattMillerNFL",
]


class TwitterAdapter:
    """Stub. Calls out to Twitter API v2 when ENABLE_TWITTER=true."""

    source = "twitter"

    def __init__(self, handles: list[str] | None = None) -> None:
        self.handles = handles or DEFAULT_REPORTERS
        self.token = settings.twitter_bearer_token
        self.enabled = settings.enable_twitter and bool(self.token)

    async def fetch(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        # TODO: implement when ENABLE_TWITTER=true.
        # Suggested: GET /2/users/by/username/{handle} → user_id; then
        # GET /2/users/{user_id}/tweets?max_results=10 with bearer auth.
        log.info("twitter_adapter_stub_invoked", handles=len(self.handles))
        return []
