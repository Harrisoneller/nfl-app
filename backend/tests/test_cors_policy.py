"""Credentialed CORS matches this project's Vercel hosts exactly."""
from __future__ import annotations

import re

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "cors_origins": "http://localhost:3000",
        "cors_production_origins": "https://statletics.io",
        "cors_vercel_project": "nfl-app",
        "cors_vercel_team": "harrisonellers-projects",
        "cors_vercel_deployment_prefix": "nfl",
        "cors_vercel_exact_hosts": "nfl-app-wheat.vercel.app",
        "cors_allow_vercel_regex": True,
    }
    base.update(overrides)
    return Settings(**base)


def _compiled(**overrides: object) -> re.Pattern[str]:
    pattern = _settings(**overrides).cors_origin_regex
    assert pattern is not None
    return re.compile(pattern)


def test_exact_project_and_team_hosts_match():
    compiled = _compiled()
    assert compiled.fullmatch("https://nfl-app.vercel.app")
    assert compiled.fullmatch("https://nfl-app-harrisonellers-projects.vercel.app")
    assert compiled.fullmatch("https://nfl-app-wheat.vercel.app")
    assert compiled.fullmatch(
        "https://nfl-app-git-main-harrisonellers-projects.vercel.app"
    )
    assert compiled.fullmatch(
        "https://nfl-app-git-cursor-gate-admin-ro-4a379d-harrisonellers-projects.vercel.app"
    )
    assert compiled.fullmatch(
        "https://nfl-ags06w9h3-harrisonellers-projects.vercel.app"
    )


def test_lookalike_origins_do_not_match():
    compiled = _compiled()
    assert compiled.fullmatch("https://nfl-app-evil.vercel.app") is None
    assert compiled.fullmatch("https://evilnfl-app.vercel.app") is None
    assert compiled.fullmatch("https://nfl-app.vercel.app.evil.com") is None
    assert compiled.fullmatch("https://evil.vercel.app") is None
    assert compiled.fullmatch("https://not-nfl-app.vercel.app") is None
    assert compiled.fullmatch("https://nfl-app-git-main.vercel.app") is None
    assert compiled.fullmatch(
        "https://nfl-app-git-main-other-team.vercel.app"
    ) is None
    assert compiled.fullmatch(
        "https://nfl-app-evil-harrisonellers-projects.vercel.app"
    ) is None
    assert compiled.fullmatch(
        "https://nfl-app-git-main-harrisonellers-projects.vercel.app.evil.com"
    ) is None
    assert compiled.fullmatch("http://nfl-app.vercel.app") is None


def test_production_origins_join_the_allowlist():
    origins = _settings().cors_origin_list
    assert "http://localhost:3000" in origins
    assert "https://statletics.io" in origins
    assert "https://www.statletics.io" in origins


def test_vercel_project_and_team_are_configurable():
    compiled = _compiled(
        cors_vercel_project="statletics",
        cors_vercel_team="example-team",
        cors_vercel_deployment_prefix="",
        cors_vercel_exact_hosts="",
    )
    assert compiled.fullmatch("https://statletics.vercel.app")
    assert compiled.fullmatch("https://statletics-example-team.vercel.app")
    assert compiled.fullmatch("https://statletics-git-main-example-team.vercel.app")
    assert compiled.fullmatch("https://nfl-app.vercel.app") is None
    assert compiled.fullmatch("https://statletics-evil.vercel.app") is None
    assert compiled.fullmatch("https://statletics-git-main.vercel.app") is None


def test_regex_can_be_disabled():
    assert _settings(cors_allow_vercel_regex=False).cors_origin_regex is None


def test_unsafe_slug_disables_the_regex():
    assert _settings(cors_vercel_project=".*").cors_origin_regex is None
    assert _settings(cors_vercel_project="").cors_origin_regex is None
    assert _settings(cors_vercel_team=".*").cors_origin_regex is None
    assert _settings(cors_vercel_exact_hosts="https://evil.com").cors_origin_regex is not None
    compiled = _compiled(cors_vercel_exact_hosts="https://evil.com,*.vercel.app")
    assert compiled.fullmatch("https://evil.com") is None
    assert compiled.fullmatch("https://nfl-app.vercel.app")
