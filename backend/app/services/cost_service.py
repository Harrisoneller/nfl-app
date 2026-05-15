"""AI cost tracking + budget gates.

In-memory ledger (suitable for single-process dev). Replace with a real
DB table for production multi-instance deployments — the API surface
stays the same.

Tracks per-user and global daily token totals; converts to USD using
configured prices; rejects new chat calls when budget is exhausted.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date, datetime, timezone

from ..config import get_settings
from ..logging_config import get_logger

log = get_logger(__name__)


class CostLedger:
    """Thread-safe in-process ledger keyed by (date, user_id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (day_iso, user_id) -> {"input_tokens", "output_tokens", "calls"}
        self._per_user: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        )
        # day_iso -> totals
        self._global: dict[str, dict[str, int]] = defaultdict(
            lambda: {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        )

    def record(self, user_id: str, input_tokens: int, output_tokens: int) -> None:
        day = _today()
        with self._lock:
            row = self._per_user[(day, user_id)]
            row["input_tokens"] += input_tokens
            row["output_tokens"] += output_tokens
            row["calls"] += 1
            g = self._global[day]
            g["input_tokens"] += input_tokens
            g["output_tokens"] += output_tokens
            g["calls"] += 1

    def get_user_today(self, user_id: str) -> dict[str, int]:
        return self._per_user[(_today(), user_id)]

    def get_global_today(self) -> dict[str, int]:
        return self._global[_today()]

    def summary(self) -> dict:
        s = get_settings()
        day = _today()
        g = self._global[day]
        g_cost = self._cost_usd(g["input_tokens"], g["output_tokens"])
        users_today = sorted(
            ((u, v) for (d, u), v in self._per_user.items() if d == day),
            key=lambda kv: kv[1]["calls"],
            reverse=True,
        )
        return {
            "date": day,
            "global": {
                **g,
                "cost_usd": round(g_cost, 4),
                "budget_usd": s.ai_global_daily_budget_usd,
                "pct_used": round(100 * g_cost / max(s.ai_global_daily_budget_usd, 0.0001), 1),
            },
            "top_users": [
                {
                    "user_id": uid,
                    **v,
                    "cost_usd": round(self._cost_usd(v["input_tokens"], v["output_tokens"]), 4),
                }
                for uid, v in users_today[:20]
            ],
        }

    @staticmethod
    def _cost_usd(input_tokens: int, output_tokens: int) -> float:
        s = get_settings()
        return (
            (input_tokens / 1000) * s.ai_cost_per_1k_input_tokens_usd
            + (output_tokens / 1000) * s.ai_cost_per_1k_output_tokens_usd
        )


ledger = CostLedger()


class BudgetExceeded(Exception):
    """Raised when an AI call would exceed user or global daily budget."""


def check_budget(user_id: str) -> None:
    """Raises BudgetExceeded if the next call should be rejected.

    Hard limit on global cap; per-user cap is also hard. Call BEFORE
    making the LLM request; bill afterwards with `ledger.record`.
    """
    s = get_settings()
    user_row = ledger.get_user_today(user_id)
    global_row = ledger.get_global_today()

    user_cost = CostLedger._cost_usd(user_row["input_tokens"], user_row["output_tokens"])
    global_cost = CostLedger._cost_usd(global_row["input_tokens"], global_row["output_tokens"])

    if global_cost >= s.ai_global_daily_budget_usd:
        log.warning("ai_global_budget_exceeded", cost=global_cost, budget=s.ai_global_daily_budget_usd)
        raise BudgetExceeded("Global AI budget for today has been reached. Try again tomorrow.")
    if user_cost >= s.ai_per_user_daily_budget_usd:
        log.info("ai_user_budget_exceeded", user_id=user_id, cost=user_cost)
        raise BudgetExceeded(
            f"You've reached today's AI usage limit (${s.ai_per_user_daily_budget_usd}). Resets at midnight UTC."
        )


def estimate_tokens(text: str) -> int:
    """Cheap token estimate when the LLM doesn't return usage stats.

    Roughly 4 chars per token for English; good enough for budget tracking.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
