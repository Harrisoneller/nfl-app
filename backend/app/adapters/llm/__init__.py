"""LLM provider abstraction.

Use `get_llm()` everywhere; never import a specific provider directly.
"""
from __future__ import annotations

from ...config import get_settings
from .anthropic import AnthropicProvider
from .base import LLMProvider
from .grok import GrokProvider
from .openai import OpenAIProvider


def get_llm() -> LLMProvider:
    settings = get_settings()
    p = settings.llm_provider
    if p == "grok":
        return GrokProvider()
    if p == "anthropic":
        return AnthropicProvider()
    if p == "openai":
        return OpenAIProvider()
    raise ValueError(f"Unknown LLM provider: {p}")
