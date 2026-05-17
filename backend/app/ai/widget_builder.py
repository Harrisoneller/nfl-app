"""Build a WidgetSpec from a freeform request.

Robustness notes:
- Grok inherits xAI's API surface which is OpenAI-compatible, but
  `response_format={"type":"json_object"}` is not 100% reliable. We
  reinforce JSON-only via the system prompt + a fenced example.
- Multi-pass parse: try strict json.loads, then extract the longest
  `{...}` slice, then ask the model to repair its own output.
- Schema validation: any field we can default we default; anything
  truly missing surfaces as a clear error to the caller.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..adapters.llm import get_llm
from ..logging_config import get_logger
from ..schemas.widget import WidgetSpec
from .prompts import WIDGET_BUILDER_SYSTEM

log = get_logger(__name__)

WIDGET_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": [
                "stat_card", "comparison_table", "list", "scoreboard",
                "bar_chart", "line_chart", "table", "news_list", "odds_table",
            ],
        },
        "data_source": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["comparison", "stats", "scores", "news", "odds"]},
                "method": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["service", "method"],
        },
        "display": {
            "type": "object",
            "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "sort_by": {"type": "string"},
                "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {"type": "integer"},
                "highlight_winner": {"type": "boolean"},
            },
        },
        "description": {"type": "string"},
    },
    "required": ["title", "kind", "data_source"],
}


# Few-shot examples drive a lot of the quality here. Include one of each common
# kind so the model learns the shape.
_FEW_SHOTS = """\
EXAMPLE 1 — User: "Compare Eagles, 49ers, and Chiefs offensive EPA per play this season"
JSON:
{
  "title": "PHI vs SF vs KC — Offensive EPA/play",
  "kind": "comparison_table",
  "data_source": {
    "service": "comparison",
    "method": "compare_teams",
    "params": {"team_ids": ["PHI", "SF", "KC"], "season": 2024}
  },
  "display": {"highlight_winner": true},
  "description": "Side-by-side offensive efficiency"
}

EXAMPLE 2 — User: "Show me today's scoreboard"
JSON:
{
  "title": "Live scoreboard",
  "kind": "scoreboard",
  "data_source": {"service": "scores", "method": "current", "params": {"limit": 16}},
  "display": {}
}

EXAMPLE 3 — User: "Top NFL news"
JSON:
{
  "title": "Top NFL news",
  "kind": "news_list",
  "data_source": {"service": "news", "method": "latest", "params": {"limit": 15}},
  "display": {}
}
"""


def _strict_system_prompt() -> str:
    return (
        WIDGET_BUILDER_SYSTEM
        + "\n\n"
        + "RESPONSE FORMAT: Return EXACTLY ONE JSON object — no prose, no markdown fences. "
        + "Schema:\n" + json.dumps(WIDGET_SCHEMA, indent=2)
        + "\n\n"
        + "Valid services + methods (use these EXACTLY):\n"
        + "- comparison.compare_teams, params: {team_ids: [...], season: <int>}\n"
        + "- comparison.compare_team_to_league, params: {team_id: <str>, season: <int>}\n"
        + "- comparison.compare_players, params: {names: [...], season: <int>}\n"
        + "- stats.team_aggregate, params: {team_id: <str>, season: <int>}\n"
        + "- scores.current, params: {limit: <int>}\n"
        + "- news.latest, params: {limit: <int>}\n"
        + "- odds.list, params: {market: <str>, limit: <int>}\n\n"
        + _FEW_SHOTS
    )


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction with three fallback passes."""
    if not text:
        return None
    # Pass 1: strict
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Pass 2: strip markdown fences, retry
    s = text.strip()
    for fence in ("```json", "```"):
        if s.startswith(fence):
            s = s[len(fence):]
        if s.endswith("```"):
            s = s[:-3]
    try:
        return json.loads(s.strip())
    except json.JSONDecodeError:
        pass
    # Pass 3: greedy slice of first balanced {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


async def build_widget_from_prompt(prompt: str) -> WidgetSpec:
    """Returns a validated WidgetSpec. Raises ValueError on persistent failure."""
    llm = get_llm()
    system = _strict_system_prompt()

    user_msg = (
        f"User request: {prompt!r}\n\n"
        "Return a single WidgetSpec JSON object matching the schema above. "
        "Do not include any commentary."
    )

    # Attempt 1 — primary call
    try:
        raw = await llm.complete_json(prompt=user_msg, schema=WIDGET_SCHEMA, system=system)
        return WidgetSpec.model_validate(raw)
    except ValidationError as e:
        log.warning("widget_builder_validation_failed_retry", error=str(e)[:300])
    except Exception as e:  # noqa: BLE001
        log.warning("widget_builder_primary_failed_retry", error=str(e)[:300])

    # Attempt 2 — explicitly ask the model to repair its own output via plain chat.
    try:
        text = await llm.chat(
            messages=[{"role": "user", "content": user_msg}],
            system=system,
        )
        parsed = _extract_json(text)
        if parsed is None:
            raise ValueError("LLM did not return parseable JSON")
        return WidgetSpec.model_validate(parsed)
    except ValidationError as e:
        log.error("widget_builder_validation_final_failure", error=str(e)[:500])
        # Best-effort minimal fallback so the UI still shows something useful.
        return WidgetSpec.model_validate({
            "title": (prompt[:60] or "Untitled widget"),
            "kind": "stat_card",
            "data_source": {"service": "scores", "method": "current", "params": {"limit": 16}},
            "description": f"Auto-fallback (couldn't parse model output): {prompt}",
        })
    except Exception as e:  # noqa: BLE001
        log.error("widget_builder_final_failure", error=str(e)[:500])
        raise ValueError(f"Widget builder failed: {e}")
