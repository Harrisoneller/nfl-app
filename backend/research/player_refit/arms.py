"""Entry points for the two arms. They do not fit and they do not score.

The refit asks the pinned ``v5_replay`` for team points and nothing else.
The incumbent leaves the prop anchor off. Banned calls are not imported here.
Tests monkeypatch those calls to raise and then run these functions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# backend/research/nfl_drive_sim/v5_replay.py at research/nfl-drive-sim
# 0c49a45c9b90b6b4635ed248bc271effbe13abfd. This branch does not vendor that file.
PINNED_V5_REPLAY_SHA256 = "bee8006b5b3096e7c1bc3ff53afc2cb211b417e2e1b7ca66e47ac442ff9892d3"
PINNED_V5_REPLAY_COMMIT = "0c49a45c9b90b6b4635ed248bc271effbe13abfd"

# The historical incumbent does not shift quantiles toward a prop.
INCUMBENT_PROP_ANCHOR = False

MARKET_INPUTS = frozenset({
    "spread",
    "spread_team",
    "imp_pts",
    "spread_line",
    "total_line",
    "mkt_d",
    "odds",
    "over_prob",
})


class ProtocolError(RuntimeError):
    """An arm used a path the prereg forbids."""


def _as_eastern(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ProtocolError("kickoff must be an America/New_York instant")
    return value.astimezone(EASTERN)


def first_kickoff(rows: list[dict[str, Any]], season: int, week: int) -> datetime:
    """First regular-season kickoff of the scored week, Eastern."""
    kicks = [
        _as_eastern(row["kickoff_et"])
        for row in rows
        if int(row["season"]) == int(season)
        and int(row["week"]) == int(week)
        and str(row.get("game_type", "REG")) == "REG"
    ]
    if not kicks:
        raise ProtocolError(f"no regular-season kickoff for {season} week {week}")
    return min(kicks)


def fit_rows(
    rows: list[dict[str, Any]], season: int, week: int,
) -> tuple[list[dict[str, Any]], datetime]:
    """Rows strictly before the scored week's first kickoff. An equal time is out."""
    cutoff = first_kickoff(rows, season, week)
    kept = [row for row in rows if _as_eastern(row["kickoff_et"]) < cutoff]
    return kept, cutoff


def _strip_market(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in MARKET_INPUTS}


def _live_v5(replay: Any) -> bool:
    name = str(getattr(replay, "__name__", ""))
    if name.endswith("team_ratings_v5"):
        return True
    coefs = getattr(replay, "MARGIN_COEFS", None)
    return isinstance(coefs, dict) and "mkt_d" in coefs


def _require_pinned_replay(replay: Any) -> None:
    if _live_v5(replay):
        raise ProtocolError("refit cannot use live v5 (team_ratings_v5 with mkt_d)")
    if getattr(replay, "FILE_SHA256", None) != PINNED_V5_REPLAY_SHA256:
        raise ProtocolError("refit team points come only from the pinned v5_replay")
    if getattr(replay, "USES_MKT_D", False):
        raise ProtocolError("refit cannot use live v5 (team_ratings_v5 with mkt_d)")


def refit_team_points(
    rows: list[dict[str, Any]],
    season: int,
    week: int,
    replay: Any,
) -> Any:
    """Team points for one refit week. ``replay.team_points`` is the only source."""
    _require_pinned_replay(replay)
    kept, cutoff = fit_rows(rows, season, week)
    cleaned = [_strip_market(row) for row in kept]
    return {
        "cutoff": cutoff,
        "n_history": len(cleaned),
        "team_points": replay.team_points(cleaned),
        "prop_anchor": None,
    }


def incumbent_arm(
    rows: list[dict[str, Any]],
    season: int,
    week: int,
) -> dict[str, Any]:
    """Incumbent week setup. The prop anchor is off. This does not call odds."""
    kept, cutoff = fit_rows(rows, season, week)
    anchor = None
    if INCUMBENT_PROP_ANCHOR:
        raise ProtocolError("incumbent prop anchor is off")
    return {
        "cutoff": cutoff,
        "n_history": len(kept),
        "prop_anchor": anchor,
    }
