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

    # Cache backend (redis support is wired but optional)
    cache_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"

    # Data sources
    espn_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
    odds_api_key: str = ""
    odds_api_base: str = "https://api.the-odds-api.com/v4"

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

    # Scheduler
    schedule_scores_seconds: int = 30
    schedule_news_seconds: int = 300
    schedule_odds_seconds: int = 900

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
