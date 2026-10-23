"""Production refuses to boot unless auth is configured.

Development and the default test env stay permissive. The messages name
variables and never include the secret value.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.startup_doctor import (
    ProductionConfigError,
    enforce_production_auth,
    production_boot_problems,
)

_REAL_SECRET = "unit-test-secret-not-a-placeholder"
_SENTINEL = "SENTINEL-must-not-appear-in-errors"


def _settings() -> Settings:
    """Build settings from the current env. Don't leave them cached."""
    get_settings.cache_clear()
    try:
        return Settings()
    finally:
        get_settings.cache_clear()


def _prod(monkeypatch, **env: str) -> Settings:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_ROLE", "web")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return _settings()


def test_development_stays_permissive(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("MULTI_USER_MODE", "false")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    assert production_boot_problems(_settings()) == []
    enforce_production_auth(_settings())


def test_production_refuses_placeholder_secret(monkeypatch):
    settings = _prod(
        monkeypatch,
        SECRET_KEY="change-me",
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="admin@example.com",
    )
    problems = production_boot_problems(settings)
    assert len(problems) == 1
    assert "SECRET_KEY" in problems[0]
    assert "change-me" in problems[0]
    with pytest.raises(ProductionConfigError, match="Refusing to start in production"):
        enforce_production_auth(settings)


def test_production_refuses_change_me_prefix_without_leaking_it(monkeypatch):
    secret = f"change-me-{_SENTINEL}"
    settings = _prod(
        monkeypatch,
        SECRET_KEY=secret,
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="admin@example.com",
    )
    with pytest.raises(ProductionConfigError) as caught:
        enforce_production_auth(settings)
    assert _SENTINEL not in str(caught.value)
    assert secret not in str(caught.value)


def test_production_refuses_blank_secret(monkeypatch):
    settings = _prod(
        monkeypatch,
        SECRET_KEY="   ",
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="admin@example.com",
    )
    problems = production_boot_problems(settings)
    assert any("SECRET_KEY" in problem for problem in problems)


def test_production_refuses_multi_user_without_admin_list(monkeypatch):
    settings = _prod(
        monkeypatch,
        SECRET_KEY=_REAL_SECRET,
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="",
    )
    problems = production_boot_problems(settings)
    assert len(problems) == 1
    assert "Auth isn't configured" in problems[0]
    assert "ADMIN_EMAILS" in problems[0]


def test_production_refuses_allowlist_without_multi_user(monkeypatch):
    settings = _prod(
        monkeypatch,
        SECRET_KEY=_REAL_SECRET,
        MULTI_USER_MODE="false",
        ADMIN_EMAILS="admin@example.com",
    )
    problems = production_boot_problems(settings)
    assert len(problems) == 1
    assert "Auth isn't configured" in problems[0]
    assert "MULTI_USER_MODE" in problems[0]


def test_production_allows_multi_user_allowlist(monkeypatch):
    settings = _prod(
        monkeypatch,
        SECRET_KEY=_REAL_SECRET,
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="Admin@Example.com, other@example.com",
    )
    assert production_boot_problems(settings) == []
    enforce_production_auth(settings)


def test_production_refuses_single_user_without_allowlist(monkeypatch):
    settings = _prod(
        monkeypatch,
        SECRET_KEY=_REAL_SECRET,
        MULTI_USER_MODE="false",
        ADMIN_EMAILS="",
    )
    problems = production_boot_problems(settings)
    assert len(problems) == 1
    assert "Auth isn't configured" in problems[0]
    assert "system admin" in problems[0]
    assert "MULTI_USER_MODE=true" in problems[0]


def test_production_refuses_short_secret(monkeypatch):
    secret = "short-but-mixed-Aa1!"
    settings = _prod(
        monkeypatch,
        SECRET_KEY=secret,
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="admin@example.com",
    )
    problems = production_boot_problems(settings)
    assert len(problems) == 1
    assert "at least 32 characters" in problems[0]
    assert secret not in problems[0]


def test_production_refuses_low_entropy_secret(monkeypatch):
    secret = "a" * 40
    settings = _prod(
        monkeypatch,
        SECRET_KEY=secret,
        MULTI_USER_MODE="true",
        ADMIN_EMAILS="admin@example.com",
    )
    problems = production_boot_problems(settings)
    assert len(problems) == 1
    assert "entropy" in problems[0]
    assert secret not in problems[0]


def test_railway_environment_triggers_the_check(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    problems = production_boot_problems(_settings())
    assert any("SECRET_KEY" in problem for problem in problems)


def test_unset_app_env_any_railway_environment_is_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "staging")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    problems = production_boot_problems(_settings())
    assert any("SECRET_KEY" in problem for problem in problems)


def test_unset_app_env_railway_environment_name_is_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "pr-21")
    monkeypatch.setenv("SECRET_KEY", _REAL_SECRET)
    monkeypatch.setenv("MULTI_USER_MODE", "false")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    problems = production_boot_problems(_settings())
    assert any("system admin" in problem for problem in problems)


def test_development_on_railway_still_fails_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "staging")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    problems = production_boot_problems(_settings())
    assert any("SECRET_KEY" in problem for problem in problems)


def test_development_with_only_railway_environment_name_fails_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "preview")
    monkeypatch.setenv("SECRET_KEY", _REAL_SECRET)
    monkeypatch.setenv("MULTI_USER_MODE", "false")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    problems = production_boot_problems(_settings())
    assert any("system admin" in problem for problem in problems)


def test_lifespan_refuses_to_serve(monkeypatch):
    """The refusal happens in startup, outside the doctor's swallowed errors."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_ROLE", "web")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    get_settings.cache_clear()
    try:
        from app.main import create_app

        with pytest.raises(ProductionConfigError, match="Refusing to start"):  # noqa: SIM117
            with TestClient(create_app()):
                pass
    finally:
        get_settings.cache_clear()
