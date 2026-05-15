"""OpenAI provider — stub.

The Grok provider already speaks OpenAI's API shape, so a real OpenAI
provider is essentially the same class with a different base URL. Wire
it up when needed.
"""
from __future__ import annotations

from typing import Any

from .base import Message


class OpenAIProvider:
    name = "openai"

    async def chat(
        self, messages: list[Message], system: str | None = None, **kwargs
    ) -> str:
        raise NotImplementedError("OpenAIProvider not yet implemented — set LLM_PROVIDER=grok")

    async def chat_with_tools(self, *args, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("OpenAIProvider not yet implemented")

    async def complete_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError("OpenAIProvider not yet implemented")
