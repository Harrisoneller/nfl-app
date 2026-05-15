"""LLMProvider interface.

Three capabilities:
- chat: stateless message → message
- chat_with_tools: full tool-use loop, runs tools server-side via the registry
- complete_json: prompt + JSON schema → parsed dict (used by widget builder)
"""
from __future__ import annotations

from typing import Any, Protocol


Message = dict[str, Any]      # {"role": "user|assistant|system|tool", "content": "...", ...}
ToolDef = dict[str, Any]      # OpenAI/Grok-style: {"type":"function","function":{"name":...,"description":...,"parameters":{...}}}
ToolResult = dict[str, Any]   # {"tool_call_id": ..., "content": "..."}


class LLMProvider(Protocol):
    name: str

    async def chat(
        self, messages: list[Message], system: str | None = None, **kwargs
    ) -> str: ...

    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDef],
        tool_runner,                        # async callable: (name, args) -> any JSON-serializable
        system: str | None = None,
        max_iters: int = 6,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Returns {"content": str, "messages": [...full transcript...]}.
        """

    async def complete_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]: ...
