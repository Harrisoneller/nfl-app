"""Credentialed CORS is limited to this project's domains and preview hosts."""
from __future__ import annotations

import re

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "cors_origins": "http://localhost:3000",
        "cors_production_origins": "https://statletics.io",
        "cors_vercel_project": "nfl-app",
        "cors_allow_vercel_regex": True,
    }
    base.update(overrides)
    return Settings(**base)


def test_preview_regex_matches_only_this_project():
    pattern = _settings().cors_origin_regex
    assert pattern is not None
    compiled = re.compile(pattern)
    assert compiled.fullmatch("https://nfl-app.vercel.app")
    assert compiled.fullmatch("https://nfl-app-git-main-team.vercel.app")
    assert compiled.fullmatch("https://nfl-app-abc123-team.vercel.app")
    assert compiled.fullmatch("https://evil.vercel.app") is None
    assert compiled.fullmatch("https://not-nfl-app.vercel.app") is None
    assert compiled.fullmatch("https://nfl-app.vercel.app.evil.example") is None
    assert compiled.fullmatch("http://nfl-app.vercel.app") is None


def test_production_origins_join_the_allowlist():
    origins = _settings().cors_origin_list
    assert "http://localhost:3000" in origins
    assert "https://statletics.io" in origins
    assert "https://www.statletics.io" in origins


def test_vercel_project_slug_is_configurable():
    pattern = _settings(cors_vercel_project="statletics").cors_origin_regex
    assert pattern is not None
    compiled = re.compile(pattern)
    assert compiled.fullmatch("https://statletics.vercel.app")
    assert compiled.fullmatch("https://statletics-git-main.vercel.app")
    assert compiled.fullmatch("https://nfl-app.vercel.app") is None


def test_regex_can_be_disabled():
    assert _settings(cors_allow_vercel_regex=False).cors_origin_regex is None


def test_unsafe_slug_disables_the_regex():
    assert _settings(cors_vercel_project=".*").cors_origin_regex is None
    assert _settings(cors_vercel_project="").cors_origin_regex is None
