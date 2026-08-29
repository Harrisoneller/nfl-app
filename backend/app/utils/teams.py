"""Team-id normalization across data sources.

nfl-data-py has used several team abbreviations over the years
(LAR/LA, WAS/WSH, JAX/JAC, OAK→LV, SD→LAC). The Odds API uses full
"City Nickname" strings. This module maps any of those to our canonical
3-letter id matching the `teams.id` column.
"""
from __future__ import annotations

# Anything coming in on the left side maps to the canonical value on the right.
ALIASES: dict[str, str] = {
    "JAC": "JAX",
    "WSH": "WAS",
    "ARZ": "ARI",
    "LA": "LAR",     # 2016 onwards Rams have used both
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LAR",    # rare historical
    "HST": "HOU",
    "BLT": "BAL",
    "CLV": "CLE",
}

# Odds API / news / historical full-name variants that are not derivable
# from the seed table (unique nicknames and "City Nickname" are).
_EXTRA_ALIASES: dict[str, str] = {
    "ny jets": "NYJ",
    "ny giants": "NYG",
    "new york football giants": "NYG",
    "la rams": "LAR",
    "la chargers": "LAC",
    "oakland raiders": "LV",
    "san diego chargers": "LAC",
    "st louis rams": "LAR",
    "st. louis rams": "LAR",
    "washington football team": "WAS",
    "washington redskins": "WAS",
    "redskins": "WAS",
}


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(".", "").replace("'", "").split())


def _build_lookup() -> dict[str, str]:
    from ..models.seed import NFL_TEAMS

    lookup: dict[str, str] = {}
    market_counts: dict[str, int] = {}
    for t in NFL_TEAMS:
        m = _norm(t["market"])
        market_counts[m] = market_counts.get(m, 0) + 1

    for t in NFL_TEAMS:
        tid = t["id"]
        lookup[_norm(tid)] = tid
        lookup[_norm(t["name"])] = tid
        lookup[_norm(f"{t['market']} {t['name']}")] = tid
        market = _norm(t["market"])
        # "New York" and "Los Angeles" each cover two clubs — only unique
        # city names (Kansas City, Buffalo, …) may map on their own.
        if market_counts.get(market, 0) == 1:
            lookup[market] = tid

    for alias, canon in ALIASES.items():
        lookup[_norm(alias)] = canon
    lookup.update(_EXTRA_ALIASES)
    return lookup


def _lookup() -> dict[str, str]:
    cached = getattr(_lookup, "_cached", None)
    if cached is not None:
        return cached
    built = _build_lookup()
    _lookup._cached = built  # type: ignore[attr-defined]
    return built


def canonical_team(team: str | None) -> str | None:
    """Map a 3-letter id, nickname, or 'City Nickname' string to our id.

    Unknown inputs pass through uppercased so callers can still display them;
    they simply will not join to a modeled team.
    """
    if not team:
        return None
    hit = _lookup().get(_norm(team))
    if hit:
        return hit
    return team.strip().upper()


def nfl_team_ids() -> set[str]:
    """Canonical 3-letter ids from the seed table. Cached after first call."""
    cached = getattr(nfl_team_ids, "_cached", None)
    if cached is not None:
        return cached
    from ..models.seed import NFL_TEAMS
    ids = {t["id"] for t in NFL_TEAMS}
    nfl_team_ids._cached = ids  # type: ignore[attr-defined]
    return ids


def is_nfl_team(team_id: str | None) -> bool:
    if not team_id:
        return False
    return canonical_team(team_id) in nfl_team_ids()


def is_nfl_matchup(home_id: str | None, away_id: str | None) -> bool:
    """True only when both sides resolved to a modeled NFL team id.

    Unresolved Odds API names store null on one side; those games stay off
    the value board and parlay cart rather than being priced against a dummy.
    """
    return is_nfl_team(home_id) and is_nfl_team(away_id)
