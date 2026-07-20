"""Fantasy market consensus — ADP + trending, blended into projection ranks.

The fantasy market has its own "closing line": Average Draft Position. It
aggregates thousands of drafters' priors on role, health, and talent — exactly
the context a pure stat model can't see (holdouts, camp reports, coaching-staff
signals). This service:

1. Ingests FantasyFootballCalculator ADP (free, keyless; 12-team, per scoring
   format) and Sleeper trending adds (24h add counts — the in-season "market
   momentum" of fantasy).
2. Matches rows to our players by normalized name (+ position tiebreak).
3. Exposes, per player: ``adp``, ``adp_overall_rank``, ``adp_pos_rank``,
   ``trending_adds``, ``value_vs_adp`` (market rank − model rank; positive =
   the market drafts them LATER than our model ranks them, i.e. model sees
   value), and a ``consensus_rank_score`` blending model and market ranks.

The market weight in the rank blend decays as real games accumulate: ADP is a
strong prior in August and mostly noise by November, when observed usage has
taken over — the same prior→evidence handoff the Bayesian player engine uses.

All external pulls are best-effort and cached via artifact_cache; a dead source
degrades to model-only ranks with no error surfaced to the product.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..logging_config import get_logger
from ..utils.seasons import current_or_upcoming_season
from . import artifact_cache

log = get_logger(__name__)

MARKET_VERSION = "fantasy-market-v1"

# ---- Rank-blend weight -------------------------------------------------------

# Preseason the market carries this much of the consensus rank ...
_ADP_WEIGHT_PRESEASON = 0.55
# ... decaying per completed week, to this floor (never zero: ADP still
# encodes role/pedigree information late in the year).
_ADP_WEIGHT_DECAY_PER_WEEK = 0.045
_ADP_WEIGHT_FLOOR = 0.15

_TRENDING_LOOKBACK_HOURS = 24
_TRENDING_LIMIT = 200

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def adp_weight(weeks_played: int) -> float:
    """Market share of the consensus rank, given completed regular-season weeks."""
    from . import param_registry as _pr
    w = (_pr.value("adp.weight_preseason")
         - _pr.value("adp.weight_decay_per_week") * max(0, weeks_played))
    return max(_pr.value("adp.weight_floor"), w)


def consensus_rank_score(
    model_rank: int, adp_overall_rank: int | None, weeks_played: int,
) -> float:
    """Blended rank score (lower = better). No ADP → pure model rank."""
    if adp_overall_rank is None:
        return float(model_rank)
    w = adp_weight(weeks_played)
    return (1 - w) * model_rank + w * adp_overall_rank


def _norm(name: str | None) -> str:
    """Casefold, strip punctuation + generational suffixes (mirrors the
    player_predictions normalizer; duplicated to avoid an import cycle)."""
    if not name:
        return ""
    tokens = "".join(
        ch if (ch.isalnum() or ch.isspace()) else " " for ch in name.lower()
    ).split()
    while tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# ---- Ingestion ---------------------------------------------------------------


async def adp_board(
    db: Session, scoring: str = "ppr", season: int | None = None,
) -> dict[str, dict[str, Any]]:
    """{normalized_name: {adp, adp_overall_rank, adp_pos_rank, position,
    stdev, trending_adds}} — cached 12h (L2 Postgres, survives restarts)."""
    if not get_settings().adp_enabled:
        return {}
    season = season or current_or_upcoming_season()

    async def _compute() -> dict[str, dict[str, Any]]:
        return await _build_adp_board(scoring, season)

    try:
        return await artifact_cache.get_or_compute(
            kind="fantasy_adp",
            key=f"{season}:{scoring}",
            compute=_compute,
            ttl_seconds=60 * 60 * 12,
            l1_ttl_seconds=60 * 30,
        )
    except Exception as e:  # noqa: BLE001 — enrichment, never fatal
        log.warning("adp_board_failed", error=str(e)[:200])
        return {}


async def _build_adp_board(scoring: str, season: int) -> dict[str, dict[str, Any]]:
    from ..adapters.data.ffc_adp import FfcAdpAdapter  # lazy imports keep

    # module load light and tests patchable.
    adapter = FfcAdpAdapter()
    try:
        rows = await adapter.fetch_adp(scoring, year=season)
    finally:
        await adapter.aclose()
    if not rows:
        return {}

    trending = await _trending_adds()

    out: dict[str, dict[str, Any]] = {}
    pos_counter: dict[str, int] = {}
    for i, r in enumerate(rows):
        key = _norm(r["name"])
        if not key or key in out:
            continue
        pos = r["position"]
        pos_counter[pos] = pos_counter.get(pos, 0) + 1
        out[key] = {
            "adp": r["adp"],
            "adp_overall_rank": i + 1,
            "adp_pos_rank": pos_counter[pos],
            "position": pos,
            "team": r.get("team"),
            "stdev": r.get("stdev"),
            "times_drafted": r.get("times_drafted"),
            "trending_adds": trending.get(key),
        }
    # Trending-only players (waiver risers with no draft-season ADP).
    for key, adds in trending.items():
        if key not in out:
            out[key] = {
                "adp": None, "adp_overall_rank": None, "adp_pos_rank": None,
                "position": None, "team": None, "stdev": None,
                "times_drafted": None, "trending_adds": adds,
            }
    return out


async def _trending_adds() -> dict[str, int]:
    """Sleeper 24h add counts keyed by normalized name. Best-effort."""
    from ..adapters.data.sleeper import SleeperAdapter

    adapter = SleeperAdapter()
    try:
        trending = await adapter.fetch_trending(
            "add", lookback_hours=_TRENDING_LOOKBACK_HOURS, limit=_TRENDING_LIMIT,
        )
        if not trending:
            return {}
        # Trending rows are {player_id, count}; resolve names via the players dump.
        players = await adapter.fetch_all_players()
        out: dict[str, int] = {}
        for t in trending:
            pid = str(t.get("player_id") or "")
            meta = players.get(pid) or {}
            name = meta.get("full_name") or (
                f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
            )
            key = _norm(name)
            if key:
                out[key] = int(t.get("count") or 0)
        return out
    except Exception as e:  # noqa: BLE001
        log.info("sleeper_trending_unavailable", error=str(e)[:160])
        return {}
    finally:
        try:
            await adapter.aclose()
        except Exception:  # noqa: BLE001
            pass


# ---- Season projection anchoring ---------------------------------------------
#
# The board-level fix for "model ranks a player nowhere near market": shrink
# each player's season fantasy projection toward the MARKET-IMPLIED level.
# The mapping is order-statistics, fully self-calibrating: take the market's
# positional rank (ADP) and read off OUR OWN projected per-game curve at that
# rank. "The market says he's RB11" becomes "the market says he scores like
# our 11th-best RB" — no external ADP→points table needed, and the curve
# adapts automatically to scoring format and league environment.

_SEASON_ANCHOR_MIN_CURVE = 5  # need at least this many projected players at a position


def apply_adp_anchor(
    rows: list[dict[str, Any]],
    adp_map: dict[str, dict[str, Any]],
    *,
    weeks_played: int,
) -> list[dict[str, Any]]:
    """Return anchored copies of leaderboard rows (input rows untouched —
    they belong to a shared cache).

    For every scoring format: build the model's per-game points curve per
    position, look up each player's ADP positional rank on that curve, and
    shrink mean/per_game/p10/p90 proportionally with weight
    ``adp_weight(weeks_played)``. Rows without ADP (or with a position
    mismatch) pass through unchanged. Each anchored fantasy block records the
    adjustment under ``adp_anchor`` for transparency.
    """
    if not rows or not adp_map:
        return rows
    w = adp_weight(weeks_played)
    fmts = ("ppr", "half_ppr", "standard")

    # Model per-game curves: position → format → sorted descending list.
    curves: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        pos = r.get("position")
        for fmt in fmts:
            f = r.get(f"fantasy_{fmt}")
            if pos and isinstance(f, dict) and f.get("per_game") is not None:
                curves.setdefault(pos, {}).setdefault(fmt, []).append(float(f["per_game"]))
    for pos in curves:
        for fmt in curves[pos]:
            curves[pos][fmt].sort(reverse=True)

    out: list[dict[str, Any]] = []
    for r in rows:
        m = adp_map.get(_norm(r.get("name")))
        pos = r.get("position")
        pos_rank = (m or {}).get("adp_pos_rank")
        adp_pos = (m or {}).get("position")
        if (
            not m or not pos_rank or not pos
            or (adp_pos and adp_pos != pos)  # name collision across positions
        ):
            out.append(r)
            continue

        new_row = {**r}
        for fmt in fmts:
            fk = f"fantasy_{fmt}"
            f = r.get(fk)
            curve = curves.get(pos, {}).get(fmt) or []
            if not isinstance(f, dict) or f.get("per_game") is None or len(curve) < _SEASON_ANCHOR_MIN_CURVE:
                continue
            model_pg = float(f["per_game"])
            idx = min(int(pos_rank) - 1, len(curve) - 1)
            market_pg = curve[idx]
            anchored_pg = (1 - w) * model_pg + w * market_pg
            if model_pg <= 0:
                continue
            scale = anchored_pg / model_pg
            new_row[fk] = {
                **f,
                "mean": round(float(f["mean"]) * scale, 1),
                "p10": round(float(f["p10"]) * scale, 1),
                "p90": round(float(f["p90"]) * scale, 1),
                "per_game": round(anchored_pg, 2),
                "adp_anchor": {
                    "adp_pos_rank": int(pos_rank),
                    "model_per_game": round(model_pg, 2),
                    "market_implied_per_game": round(market_pg, 2),
                    "weight": round(w, 2),
                },
            }
        out.append(new_row)
    return out


# ---- Row enrichment ----------------------------------------------------------


def attach_market_context(
    rows: list[dict[str, Any]],
    adp_map: dict[str, dict[str, Any]],
    *,
    weeks_played: int,
) -> None:
    """Mutate ranked leaderboard rows in place with fantasy-market fields.

    Rows must already carry ``rank`` (model rank within the current view) and
    ``name``. Adds a ``market`` block + ``consensus_rank_score``; sorting by
    the blended score is the caller's choice (sort="consensus").
    """
    if not rows:
        return
    for r in rows:
        m = adp_map.get(_norm(r.get("name"))) if adp_map else None
        model_rank = int(r.get("rank") or 0)
        adp_rank = (m or {}).get("adp_overall_rank")
        r["market"] = {
            "adp": (m or {}).get("adp"),
            "adp_overall_rank": adp_rank,
            "adp_pos_rank": (m or {}).get("adp_pos_rank"),
            "trending_adds": (m or {}).get("trending_adds"),
            # Positive = drafters take this player LATER than our model ranks
            # him → the model sees value. Negative = market is higher than us.
            "value_vs_adp": (
                (adp_rank - model_rank) if (adp_rank and model_rank) else None
            ),
            "adp_weight": round(adp_weight(weeks_played), 2),
        }
        r["consensus_rank_score"] = round(
            consensus_rank_score(model_rank, adp_rank, weeks_played), 1,
        )
