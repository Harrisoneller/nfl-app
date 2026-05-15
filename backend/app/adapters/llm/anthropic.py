"""Anthropic provider — stub.

Implementation sketch: use the Anthropic SDK or httpx against
https://api.anthropic.com/v1/messages. Tool-use shape differs from
OpenAI's (Anthropic uses `tool_use` content blocks). Translate to/from
the OpenAI-style transcript when implementing.
"""
from __future__ import annotations

from typing import Any

from .base import Message


class AnthropicProvider:
    name = "anthropic"

    async def chat(
        self, messages: list[Message], system: str | None = None, **kwargs
    ) -> str:
        raise NotImplementedError("AnthropicProvider not yet implemented — set LLM_PROVIDER=grok")

    async def chat_with_tools(self, *args, **kwargs) -> dict[str, Any]:
        raise NotImplementedError("AnthropicProvider not yet implemented")

    async def complete_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError("AnthropicProvider not yet implemented")
