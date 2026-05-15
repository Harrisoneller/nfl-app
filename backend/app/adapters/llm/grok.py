"""Grok (xAI) provider.

xAI's API is OpenAI-compatible (same /v1/chat/completions shape, same
function-call format), so we hit it with raw httpx — no extra SDK.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)


class GrokProvider:
    name = "grok"
    BASE = "https://api.x.ai/v1"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.grok_api_key
        self.model = s.grok_model
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _post(self, path: str, body: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("GROK_API_KEY not set")
        r = await self.client.post(f"{self.BASE}{path}", content=json.dumps(body))
        if r.status_code >= 400:
            log.error("grok_api_error", status=r.status_code, body=r.text[:500])
            r.raise_for_status()
        return r.json()

    async def chat(
        self, messages: list[dict], system: str | None = None, **kwargs
    ) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        data = await self._post(
            "/chat/completions",
            {"model": self.model, "messages": msgs, **kwargs},
        )
        return data["choices"][0]["message"]["content"]

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_runner,
        system: str | None = None,
        max_iters: int = 6,
        **kwargs,
    ) -> dict[str, Any]:
        transcript: list[dict] = ([{"role": "system", "content": system}] if system else []) + list(messages)

        for _ in range(max_iters):
            data = await self._post(
                "/chat/completions",
                {
                    "model": self.model,
                    "messages": transcript,
                    "tools": tools,
                    "tool_choice": "auto",
                    **kwargs,
                },
            )
            msg = data["choices"][0]["message"]
            transcript.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return {"content": msg.get("content") or "", "messages": transcript}

            # Run tools and append results
            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result = await tool_runner(name, args)
                    content = json.dumps(result, default=str)[:50_000]
                except Exception as e:  # noqa: BLE001
                    content = json.dumps({"error": str(e)})
                transcript.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": content,
                })

        return {"content": "(max tool iterations reached)", "messages": transcript}

    async def complete_json(
        self, prompt: str, schema: dict[str, Any], system: str | None = None
    ) -> dict[str, Any]:
        sys_prompt = (system or "") + (
            "\nYou MUST respond with a single JSON object matching this JSON Schema:\n"
            + json.dumps(schema)
        )
        text = await self.chat(
            [{"role": "user", "content": prompt}],
            system=sys_prompt,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Best-effort recovery
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
            raise

    async def aclose(self) -> None:
        await self.client.aclose()
