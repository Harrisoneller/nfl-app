"""Player insights: usage trends, consistency profiles, and side-by-side
projection comparison.

Everything here is derived from data the app already syncs:

- **Usage** comes from the nflverse weekly frame — target/carry share is
  computed against team totals per week, so it works even when the frame
  doesn't ship a ``target_share`` column.
- **Consistency** is a per-game fantasy profile over the most recent completed
  season: mean, week-to-week SD, coefficient of variation, floor (p25) and
  ceiling (p75) — the "is he boom/bust or metronome" answer.
- **Compare** stacks 2–4 players: season projection distributions (v2 engine),
  next-game distributions, usage, and consistency in one response so the UI
  can overlay them.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..cache import cache
from ..logging_config import get_logger
from ..models.player import Player
from ..utils.seasons import latest_completed_season
from . import player_predictions_service as proj

log = get_logger(__name__)

CACHE_TTL = 60 * 30
MAX_COMPARE = 4

# Weekly-frame stat columns surfaced in the usage series (when present).
_USAGE_STATS = [
    "targets", "receptions", "receiving_yards", "receiving_tds",
    "carries", "rushing_yards", "rushing_tds",
    "attempts", "passing_yards", "passing_tds",
    "fantasy_points_ppr",
]


async def usage_profile(
    player: Player, season: int | None = None, weeks: int = 18,
) -> dict[str, Any]:
    """Usage + consistency for one player over one (completed) season.

    Returns ``{"season", "games", "weekly": [...], "shares": {...},
    "consistency": {...}}``. Empty-but-valid shape when the player has no rows
    (rookies, missed season).
    """
    season = season or latest_completed_season()
    gsis = await proj._resolve_gsis(player)  # noqa: SLF001
    empty = {"season": season, "games": 0, "weekly": [], "shares": {}, "consistency": {}}
    if gsis is None:
        return empty

    key = f"usage_profile:{gsis}:{season}"
    if (v := cache.get(key)) is not None:
        return v

    df = await proj._player_weekly_frame(season)  # noqa: SLF001
    if df is None or len(df) == 0 or "player_id" not in df.columns:
        return empty
    sub = df[df["player_id"] == gsis].sort_values("week")
    if not len(sub):
        cache.set(key, empty, CACHE_TTL)
        return empty

    # Team totals per week → opportunity shares that don't depend on optional
    # pre-computed share columns.
    team_col = "recent_team" if "recent_team" in df.columns else None
    team_totals = None
    if team_col:
        team_totals = df.groupby([team_col, "week"])[
            [c for c in ("targets", "carries") if c in df.columns]
        ].sum()

    weekly: list[dict[str, Any]] = []
    for _, r in sub.tail(weeks).iterrows():
        row: dict[str, Any] = {"week": int(r["week"])}
        if "opponent_team" in sub.columns and isinstance(r.get("opponent_team"), str):
            row["opponent"] = r["opponent_team"]
        for c in _USAGE_STATS:
            if c in sub.columns and pd.notna(r.get(c)):
                row[c] = round(float(r[c]), 2)
        if team_totals is not None and team_col and isinstance(r.get(team_col), str):
            tkey = (r[team_col], r["week"])
            if tkey in team_totals.index:
                tt = team_totals.loc[tkey]
                for share_of, label in (("targets", "target_share"), ("carries", "carry_share")):
                    if share_of in tt.index and float(tt[share_of]) > 0 and share_of in row:
                        row[label] = round(row[share_of] / float(tt[share_of]), 3)
        weekly.append(row)

    fp = pd.to_numeric(sub.get("fantasy_points_ppr"), errors="coerce").dropna()
    consistency: dict[str, Any] = {}
    if len(fp) >= 3:
        mean = float(fp.mean())
        sd = float(fp.std())
        consistency = {
            "ppg_ppr": round(mean, 2),
            "sd": round(sd, 2),
            "cv": round(sd / mean, 3) if mean > 0 else None,
            "floor_p25": round(float(fp.quantile(0.25)), 1),
            "ceiling_p75": round(float(fp.quantile(0.75)), 1),
            "best": round(float(fp.max()), 1),
            "worst": round(float(fp.min()), 1),
        }

    shares: dict[str, Any] = {}
    share_vals = {
        k: [w[k] for w in weekly if k in w] for k in ("target_share", "carry_share")
    }
    for k, vals in share_vals.items():
        if vals:
            shares[k] = round(sum(vals) / len(vals), 3)

    out = {
        "season": season,
        "games": int(sub["week"].nunique()),
        "weekly": weekly,
        "shares": shares,
        "consistency": consistency,
    }
    cache.set(key, out, CACHE_TTL)
    return out


async def compare_players(
    db: Session, player_ids: list[str], season: int | None = None,
) -> dict[str, Any]:
    """Side-by-side projection + usage comparison for 2–4 players."""
    ids = [p.strip() for p in player_ids if p.strip()][:MAX_COMPARE]
    if len(ids) < 2:
        return {"error": "need at least 2 player ids", "players": []}

    usage_season = latest_completed_season()
    out_players: list[dict[str, Any]] = []
    for pid in ids:
        player: Player | None = db.get(Player, pid)
        if player is None:
            out_players.append({"player_id": pid, "error": "player not found"})
            continue
        season_proj = await proj.player_season_projection(db, pid, season)
        game_preds = await proj.player_game_predictions(db, pid, season)
        usage = await usage_profile(player, usage_season)
        next_game = (game_preds.get("games") or [None])[0]
        out_players.append({
            "player_id": pid,
            "name": player.full_name,
            "position": (player.position or "").upper(),
            "team": player.team_id,
            "injury_status": (player.metadata_json or {}).get("injury_status"),
            "season_projection": {
                "season": season_proj.get("season"),
                "games_remaining": season_proj.get("games_remaining"),
                "stats": season_proj.get("stats") or {},
                "fantasy": season_proj.get("fantasy") or {},
                "role": season_proj.get("role"),
                "error": season_proj.get("error"),
            },
            "next_game": next_game,
            "usage": usage,
        })

    return {
        "season": season or (out_players[0].get("season_projection") or {}).get("season"),
        "usage_season": usage_season,
        "model_version": proj.MODEL_VERSION,
        "players": out_players,
    }
