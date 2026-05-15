# AI design

## Goals

1. **Answer NFL questions** — anything from "who has the most rushing yards in 2024" to "is Joe Burrow's INT rate up YoY".
2. **Build widgets** — "show me a widget comparing the Eagles' and 49ers' red-zone efficiency this season" → renders + saves a widget on the user's dashboard.

## Provider abstraction

```
backend/app/adapters/llm/
├── base.py        LLMProvider Protocol  (chat, chat_with_tools, complete_json)
├── grok.py        Default — xAI grok-2-latest
├── anthropic.py   Stub: claude-sonnet-4-6
└── openai.py      Stub: gpt-4o-mini
```

Selection via `LLM_PROVIDER` env var. Switching providers is one line in `.env`.

## Tool-use

The AI has access to the same services the REST API uses, exposed as tools:

| Tool | Returns |
|---|---|
| `get_current_scoreboard` | Live games today/this week |
| `get_team` | Team profile + record |
| `get_team_roster` | Active roster |
| `get_team_stats` | Season aggregates |
| `get_player` | Player profile + season stats |
| `compare_teams` | Side-by-side stats for N teams (or vs league average) |
| `compare_players` | Side-by-side stats for N players |
| `get_latest_news` | Top news items |
| `get_odds` | Markets for a game / future |
| `build_widget` | Returns a `WidgetSpec` and saves it to the user's dashboard |

The model decides which tools to call. Results are cached so a follow-up question rarely re-hits external APIs.

## Widget specification

A widget is fully described by JSON. The frontend has a generic `<WidgetRenderer />` that interprets the spec.

```jsonc
{
  "id": "wgt_01HX...",
  "title": "Eagles vs 49ers — Red Zone Efficiency",
  "kind": "comparison_table",          // or "stat_card" | "line_chart" | "bar_chart" | "list" | "scoreboard"
  "data_source": {
    "service": "comparison",
    "method": "compare_teams",
    "params": { "team_ids": ["PHI", "SF"], "metric": "redzone_pct", "season": 2025 }
  },
  "display": {
    "columns": ["team", "redzone_pct", "rank"],
    "highlight_winner": true
  }
}
```

When the AI generates a widget it returns the spec; the user can pin it to the home dashboard.

## Prompts

- **System prompt** sets persona ("expert NFL analyst, data-grounded, concise"), enumerates tools, and instructs the model to *prefer tool calls over guessing*.
- **Widget builder prompt** (separate sub-flow) gets the user's freeform "show me X" request + a JSON Schema for `WidgetSpec` and is asked to return *only* JSON.
