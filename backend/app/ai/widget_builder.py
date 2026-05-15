"""Build a WidgetSpec from a freeform request via JSON-mode completion."""
from __future__ import annotations

from ..adapters.llm import get_llm
from ..schemas.widget import WidgetSpec
from .prompts import WIDGET_BUILDER_SYSTEM

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


async def build_widget_from_prompt(prompt: str) -> WidgetSpec:
    llm = get_llm()
    raw = await llm.complete_json(
        prompt=f"User request: {prompt}\n\nReturn a single WidgetSpec JSON object.",
        schema=WIDGET_SCHEMA,
        system=WIDGET_BUILDER_SYSTEM,
    )
    return WidgetSpec.model_validate(raw)
