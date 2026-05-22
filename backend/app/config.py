"""Application configuration via Pydantic settings.

All config comes from environment variables (or a `.env` file at repo root).
Adapters and services depend on `settings`; nothing references env vars
directly so test overrides stay localized.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core
    app_env: Literal["development", "production"] = "development"
    # web = API only (Railway public service). worker = scheduler + ingest/derive.
    # Local dev: use worker (or two terminals with web + worker) — see RUNBOOK.
    app_role: Literal["web", "worker"] = "worker"
    log_level: str = "INFO"
    secret_key: str = "change-me"

    # Database
    database_url: str = "postgresql+psycopg://localhost/nflapp"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: object) -> object:
        """Railway/Heroku inject postgresql://; SQLAlchemy needs the psycopg driver."""
        if isinstance(v, str) and v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://") :]
        return v
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # Auth
    multi_user_mode: bool = False
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    # LLM
    llm_provider: Literal["grok", "anthropic", "openai"] = "grok"
    grok_api_key: str = ""
    grok_model: str = "grok-3"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Cost controls (USD). Conservative defaults — tighten in prod.
    ai_global_daily_budget_usd: float = 10.0
    ai_per_user_daily_budget_usd: float = 1.0
    ai_cost_per_1k_input_tokens_usd: float = 0.002   # rough Grok-equivalent
    ai_cost_per_1k_output_tokens_usd: float = 0.010

    # Rate limits (per IP)
    rate_limit_default: str = "60/minute"
    rate_limit_ai: str = "20/hour"
    rate_limit_search: str = "120/minute"

    # Observability
    sentry_dsn: str = ""           # set to enable Sentry
    sentry_traces_sample_rate: float = 0.0  # 0.0 = errors only

    # Cache backend — use redis on multi-replica web tiers (shared L1 for JSON artifacts)
    cache_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    cache_max_entries: int = 2048

    # Data sources
    espn_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
    odds_api_key: str = ""
    odds_api_base: str = "https://api.the-odds-api.com/v4"

    @field_validator("odds_api_key", mode="before")
    @classmethod
    def normalize_odds_api_key(cls, v: object) -> object:
        """Strip whitespace; reject inline comments accidentally pasted into the value."""
        if not isinstance(v, str):
            return v
        key = v.strip()
        if "#" in key:
            key = key.split("#", 1)[0].strip()
        return key

    # News / social
    enable_twitter: bool = False
    twitter_bearer_token: str = ""
    rss_feeds: str = (
        "https://www.espn.com/espn/rss/nfl/news,"
        "https://profootballtalk.nbcsports.com/feed/,"
        "https://www.cbssports.com/rss/headlines/nfl/"
    )
    fantasy_rss_feeds: str = (
        "https://www.fantasypros.com/nfl/rss/news.php,"
        "https://www.rotoballer.com/category/nfl/feed,"
        "https://www.fftoday.com/rss/news.xml,"
        "https://establishtherun.com/feed/"
    )

    # Scheduler (worker role only — see app_role)
    # Boot: none | minimal (seed + current-season schedule) | full (legacy dev warmups)
    boot_warmup_level: Literal["none", "minimal", "full"] = "minimal"
    schedule_scores_seconds: int = 30
    schedule_news_seconds: int = 300
    schedule_odds_seconds: int = 900  # deprecated for odds (cron-driven now); kept for back-compat
    # Heavy derive chain (materialize, elo, profiles, MC, awards) — UTC hours
    derive_cron_hours_utc: str = "6,18"
    # H2H prewarm only (separate from derive) — default 03:30 UTC
    h2h_cron_hours_utc: str = "3"

    # Odds budget controls — The Odds API free tier = 500 credits/mo, and each
    # pull costs (markets × regions) credits (our standard pull = 3). Only the
    # scheduled job hits the API; everything user-facing reads the DB snapshot.
    odds_refresh_hours_utc: str = "1,13"   # cron hours (UTC) for the twice-daily pull (~180 credits/mo)
    odds_min_refresh_hours: float = 6.0    # floor: skip an auto-pull if we pulled more recently than this
    odds_lookahead_days: int = 10          # offseason guard: skip auto-pull if no game kicks off within this window

    # CORS
    cors_origins: str = "http://localhost:3000"

    @property
    def rss_feed_list(self) -> list[str]:
        return [u.strip() for u in self.rss_feeds.split(",") if u.strip()]

    @property
    def fantasy_rss_feed_list(self) -> list[str]:
        return [u.strip() for u in self.fantasy_rss_feeds.split(",") if u.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [u.strip() for u in self.cors_origins.split(",") if u.strip()]

    @property
    def scheduler_enabled(self) -> bool:
        return self.app_role == "worker"

    @property
    def derive_cron_hour_list(self) -> list[int]:
        out: list[int] = []
        for part in self.derive_cron_hours_utc.split(","):
            part = part.strip()
            if part.isdigit():
                h = int(part)
                if 0 <= h <= 23:
                    out.append(h)
        return out or [6, 18]

    @property
    def derive_cron_hours_expr(self) -> str:
        """Comma-separated hours for APScheduler (expects a string, not a list)."""
        return ",".join(str(h) for h in self.derive_cron_hour_list)

    @property
    def h2h_cron_hour_list(self) -> list[int]:
        out: list[int] = []
        for part in self.h2h_cron_hours_utc.split(","):
            part = part.strip()
            if part.isdigit():
                h = int(part)
                if 0 <= h <= 23:
                    out.append(h)
        return out or [3]

    @property
    def h2h_cron_hours_expr(self) -> str:
        return ",".join(str(h) for h in self.h2h_cron_hour_list)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
