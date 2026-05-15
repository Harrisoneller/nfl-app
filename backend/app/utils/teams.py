"""Team-id normalization across data sources.

nfl-data-py has used several team abbreviations over the years
(LAR/LA, WAS/WSH, JAX/JAC, OAK→LV, SD→LAC). This module gives us a
canonical 3-letter id matching our `teams.id` column.
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


def canonical_team(team: str | None) -> str | None:
    if not team:
        return None
    t = team.strip().upper()
    return ALIASES.get(t, t)
